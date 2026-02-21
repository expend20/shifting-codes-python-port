"""Merge Function Pass — port of Polaris MergeFunction.cpp.

Merges multiple functions into a single switch-based dispatcher.
Non-void functions are first wrapped into void-returning internal
helpers that write their return value through a pointer parameter,
then all helpers are merged into one dispatcher.  The original
function bodies are replaced with thin stubs that call the dispatcher.
"""

from __future__ import annotations

import llvm

from shifting_codes.passes import PassRegistry
from shifting_codes.passes.base import ModulePass, PassInfo
from shifting_codes.utils.crypto import CryptoRandom


# ---------------------------------------------------------------------------
# Operand remapping helpers
# ---------------------------------------------------------------------------
#
# nanobind wraps LLVM types with strict ``__eq__`` that raises ``TypeError``
# when comparing across incompatible types.  Critically,
# ``inst.get_operand()`` returns BasicBlock operands (branch targets) as
# ``Value`` objects, NOT ``BasicBlock``.  A dict keyed by ``BasicBlock``
# cannot find them via normal lookup because ``BasicBlock.__eq__(Value)``
# raises.  However, both wrappers share the same ``__hash__`` (same
# underlying LLVM pointer), so we use hash-keyed lookup for blocks.

def _remap_operands(inst, value_map: dict, block_by_hash: dict):
    """Remap instruction operands.

    *value_map* is a normal ``{Value: Value}`` dict.
    *block_by_hash* is ``{hash(src_bb): dst_bb}`` to avoid type mismatches.
    """
    for i in range(inst.num_operands):
        op = inst.get_operand(i)
        # Try value map first (most operands are values)
        try:
            if op in value_map:
                inst.set_operand(i, value_map[op])
                continue
        except TypeError:
            pass
        # Try hash-based block lookup (for branch targets returned as Value)
        h = hash(op)
        if h in block_by_hash:
            inst.set_operand(i, block_by_hash[h])


def _safe_get(mapping: dict, key, default=None):
    """Look up *key* in *mapping*, returning *default* on TypeError."""
    try:
        return mapping.get(key, default)
    except TypeError:
        return default


def _block_hash_map(block_map: dict) -> dict:
    """Convert ``{BasicBlock: BasicBlock}`` to ``{hash(bb): Value}``.

    Values are converted via ``as_value()`` so they can be passed to
    ``set_operand()`` which requires ``Value``, not ``BasicBlock``.
    """
    return {hash(k): v.as_value() for k, v in block_map.items()}


# ---------------------------------------------------------------------------
# Cloning helpers
# ---------------------------------------------------------------------------

def _clone_function_body(
    source: llvm.Function,
    target: llvm.Function,
    arg_map: dict,
    return_bb: llvm.BasicBlock,
) -> llvm.BasicBlock:
    """Clone all blocks from *source* into *target*, remapping values.

    Returns the cloned entry basic block (for use as a switch target).
    All ``ret void`` terminators are replaced with ``br return_bb``.
    """
    source_blocks = list(source.basic_blocks)
    if not source_blocks:
        return return_bb

    value_map: dict = dict(arg_map)
    block_map: dict = {}
    for i, src_bb in enumerate(source_blocks):
        dst_bb = target.append_basic_block(f"merge.{source.name}.bb{i}")
        block_map[src_bb] = dst_bb

    phi_info: list[tuple] = []
    for src_bb in source_blocks:
        dst_bb = block_map[src_bb]
        with dst_bb.create_builder() as builder:
            for inst in src_bb.instructions:
                if inst.opcode == llvm.Opcode.Ret:
                    builder.br(return_bb)
                    continue
                if inst.opcode == llvm.Opcode.PHI:
                    incoming = list(inst.incoming)
                    cloned = builder.phi(inst.type, "")
                    value_map[inst] = cloned
                    phi_info.append((cloned, incoming))
                    continue
                cloned = inst.instruction_clone()
                builder.insert_into_builder_with_name(cloned, "")
                value_map[inst] = cloned

    bbh = _block_hash_map(block_map)
    for src_bb in source_blocks:
        dst_bb = block_map[src_bb]
        for inst in dst_bb.instructions:
            if inst.opcode == llvm.Opcode.PHI:
                continue
            _remap_operands(inst, value_map, bbh)

    for cloned_phi, original_incoming in phi_info:
        for val, blk in original_incoming:
            cloned_phi.add_incoming(
                _safe_get(value_map, val, val),
                _safe_get(block_map, blk, blk),
            )

    return block_map[source_blocks[0]]


def _clone_into_void_wrapper(
    source: llvm.Function,
    wrapper: llvm.Function,
    ctx: llvm.Context,
) -> None:
    """Clone *source* body into *wrapper*, converting returns to stores.

    *wrapper* has the same parameters as *source* followed by an extra
    ``ptr`` parameter (the return-value slot) when *source* is non-void.
    """
    src_fn_ty = source.function_type
    ret_ty = src_fn_ty.return_type
    is_void = ret_ty.kind == llvm.TypeKind.Void
    n_params = src_fn_ty.param_count

    value_map: dict = {}
    for i in range(n_params):
        value_map[source.get_param(i)] = wrapper.get_param(i)

    source_blocks = list(source.basic_blocks)
    if not source_blocks:
        return

    block_map: dict = {}
    for i, src_bb in enumerate(source_blocks):
        dst_bb = wrapper.append_basic_block(f"wrap.{source.name}.bb{i}")
        block_map[src_bb] = dst_bb

    phi_info: list[tuple] = []
    for src_bb in source_blocks:
        dst_bb = block_map[src_bb]
        with dst_bb.create_builder() as builder:
            for inst in src_bb.instructions:
                if inst.opcode == llvm.Opcode.Ret:
                    if not is_void:
                        ret_val = inst.get_operand(0)
                        mapped_val = _safe_get(value_map, ret_val, ret_val)
                        out_ptr = wrapper.get_param(n_params)
                        builder.store(mapped_val, out_ptr)
                    builder.ret_void()
                    continue
                if inst.opcode == llvm.Opcode.PHI:
                    incoming = list(inst.incoming)
                    cloned = builder.phi(inst.type, "")
                    value_map[inst] = cloned
                    phi_info.append((cloned, incoming))
                    continue
                cloned = inst.instruction_clone()
                builder.insert_into_builder_with_name(cloned, "")
                value_map[inst] = cloned

    bbh = _block_hash_map(block_map)
    for src_bb in source_blocks:
        dst_bb = block_map[src_bb]
        for inst in dst_bb.instructions:
            if inst.opcode == llvm.Opcode.PHI:
                continue
            _remap_operands(inst, value_map, bbh)

    for cloned_phi, original_incoming in phi_info:
        for val, blk in original_incoming:
            cloned_phi.add_incoming(
                _safe_get(value_map, val, val),
                _safe_get(block_map, blk, blk),
            )


# ---------------------------------------------------------------------------
# Pass
# ---------------------------------------------------------------------------

@PassRegistry.register
class MergeFunctionPass(ModulePass):

    def __init__(self, rng: CryptoRandom | None = None):
        self.rng = rng or CryptoRandom()

    @classmethod
    def info(cls) -> PassInfo:
        return PassInfo(
            name="merge_function",
            description="[Polaris] Merge functions into a switch dispatcher",
            is_module_pass=True,
        )

    def run_on_module(self, mod: llvm.Module, ctx: llvm.Context, selected_functions: set[str] | None = None) -> bool:
        targets = self._collect_targets(mod)
        if len(targets) < 2:
            return False

        i32 = ctx.types.i32
        ptr_ty = ctx.types.ptr
        void_ty = ctx.types.void

        # ── Phase 1: process() — create void-returning internal wrappers ──
        internals: list[llvm.Function] = []
        stubs: list[tuple] = []  # (original, wrapper, is_void)

        for func in targets:
            fn_ty = func.function_type
            ret_ty = fn_ty.return_type
            is_void = ret_ty.kind == llvm.TypeKind.Void

            wrapper_params = list(fn_ty.param_types)
            if not is_void:
                wrapper_params.append(ptr_ty)

            wrapper_fn_ty = ctx.types.function(void_ty, wrapper_params)
            wrapper = mod.add_function(
                f"{func.name}_internal", wrapper_fn_ty,
            )
            wrapper.linkage = llvm.Linkage.Private

            _clone_into_void_wrapper(func, wrapper, ctx)
            internals.append(wrapper)
            stubs.append((func, wrapper, is_void))

        # Replace original function bodies with thin stubs
        for func, wrapper, is_void in stubs:
            fn_ty = func.function_type
            ret_ty = fn_ty.return_type

            stub = mod.add_function(f"{func.name}_stub", fn_ty)
            stub.linkage = func.linkage

            entry = stub.append_basic_block("entry")
            with entry.create_builder() as builder:
                call_args = [stub.get_param(i) for i in range(fn_ty.param_count)]
                if not is_void:
                    ret_alloca = builder.alloca(ret_ty, name="retval")
                    call_args.append(ret_alloca)
                builder.call(wrapper, call_args, "")
                if is_void:
                    builder.ret_void()
                else:
                    loaded = builder.load(ret_ty, ret_alloca, "retload")
                    builder.ret(loaded)

            func.replace_all_uses_with(stub)
            func.erase()
            stub.name = stub.name.removesuffix("_stub")

        # ── Phase 2: merge() — merge all internal wrappers ──
        merged_params: list = [i32]
        arg_offsets: list[int] = []
        for wrapper in internals:
            arg_offsets.append(len(merged_params))
            merged_params.extend(wrapper.function_type.param_types)

        merged_fn_ty = ctx.types.function(void_ty, merged_params)
        merged_func = mod.add_function("__merged_function", merged_fn_ty)
        merged_func.linkage = llvm.Linkage.Private

        switch_bb = merged_func.append_basic_block("switch")
        return_bb = merged_func.append_basic_block("return")
        with return_bb.create_builder() as builder:
            builder.ret_void()

        entry_blocks: list = []
        for idx, wrapper in enumerate(internals):
            arg_map = {}
            w_fn_ty = wrapper.function_type
            offset = arg_offsets[idx]
            for i in range(w_fn_ty.param_count):
                arg_map[wrapper.get_param(i)] = merged_func.get_param(offset + i)
            entry_bb = _clone_function_body(
                wrapper, merged_func, arg_map, return_bb,
            )
            entry_blocks.append(entry_bb)

        with switch_bb.create_builder() as builder:
            selector = merged_func.get_param(0)
            switch = builder.switch_(selector, return_bb, len(internals))
            for idx, entry_bb in enumerate(entry_blocks):
                switch.add_case(i32.constant(idx), entry_bb)

        # Replace calls to each wrapper with calls to the merged dispatcher
        for idx, wrapper in enumerate(internals):
            w_fn_ty = wrapper.function_type
            call_sites = self._find_call_sites(mod, wrapper)
            for call_inst in call_sites:
                args: list = [i32.constant(idx)]
                for t_idx, w in enumerate(internals):
                    t_fn_ty = w.function_type
                    if t_idx == idx:
                        for i in range(w_fn_ty.param_count):
                            args.append(call_inst.get_operand(i))
                    else:
                        for pt in t_fn_ty.param_types:
                            args.append(pt.undef())

                bb = call_inst.block
                with bb.create_builder() as builder:
                    builder.position_before(call_inst)
                    builder.call(merged_func, args, "")
                call_inst.erase_from_parent()

        # Erase the now-unused wrapper functions
        for wrapper in internals:
            wrapper.erase()

        return True

    @staticmethod
    def _collect_targets(mod: llvm.Module) -> list[llvm.Function]:
        """Collect all non-declaration functions eligible for merging."""
        targets = []
        for func in mod.functions:
            if func.is_declaration:
                continue
            if func.name.startswith("__merged"):
                continue
            targets.append(func)
        return targets

    @staticmethod
    def _find_call_sites(mod: llvm.Module, target_func: llvm.Function) -> list:
        sites = []
        target_name = target_func.name
        for func in mod.functions:
            for bb in func.basic_blocks:
                for inst in bb.instructions:
                    if inst.opcode == llvm.Opcode.Call:
                        called = inst.get_operand(inst.num_operands - 1)
                        if hasattr(called, 'name') and called.name == target_name:
                            sites.append(inst)
        return sites

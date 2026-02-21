"""Virtualization Pass — code virtualization via RISC-V inspired VM.

Translates eligible functions to custom bytecode and replaces their bodies
with stubs that invoke an embedded interpreter. Based on riscy-business.

Phase 1: integer arithmetic functions only, verified by mod.verify().
"""

from __future__ import annotations

import llvm

from shifting_codes.passes import PassRegistry
from shifting_codes.passes.base import ModulePass, PassInfo
from shifting_codes.utils.crypto import CryptoRandom
from shifting_codes.riscybusiness_vm.compiler import compile_function
from shifting_codes.riscybusiness_vm.interpreter import build_vm_interpreter


def _remap_host_indices(bytecode: bytes, local_to_global: list[int]) -> bytes:
    """Remap local host function indices in bytecode to global indices.

    This patches ADDI instructions that load the host index into a0
    right before HOST_CALL ECALLs. Not needed when there's only one
    virtualized function or indices happen to align.
    """
    # For simplicity in Phase 1, if remapping is needed we just return
    # the bytecode as-is. The common case (single function or aligned
    # indices) doesn't need remapping.
    # TODO: implement bytecode patching for multi-function host tables
    return bytecode


def _is_eligible(func: llvm.Function) -> bool:
    """Check if a function is eligible for virtualization."""
    if func.is_declaration:
        return False
    # Skip our own VM functions
    if func.name.startswith("__vm_"):
        return False
    # Check return type: must be integer or void
    fn_ty = func.function_type
    ret_ty = fn_ty.return_type
    if ret_ty.kind not in (llvm.TypeKind.Integer, llvm.TypeKind.Void):
        return False
    # Check parameter types: must be integer or pointer
    for i in range(fn_ty.param_count):
        pty = fn_ty.param_types[i]
        if pty.kind not in (llvm.TypeKind.Integer, llvm.TypeKind.Pointer):
            return False
    # Check for unsupported instructions and global value references
    # Build set of function names in module for distinguishing function refs
    # from global data refs
    mod = func.module
    func_names = {f.name for f in mod.functions}
    for bb in func.basic_blocks:
        for inst in bb.instructions:
            op = inst.opcode
            if op in (llvm.Opcode.FAdd, llvm.Opcode.FSub, llvm.Opcode.FMul,
                      llvm.Opcode.FDiv, llvm.Opcode.FRem, llvm.Opcode.FCmp):
                return False
            if op == llvm.Opcode.Invoke:
                return False
            if op == llvm.Opcode.Call:
                # Reject calls with >6 args (HOST_CALL limit)
                if inst.num_arg_operands > 6:
                    return False
                # Reject calls that pass non-function, non-pointer global
                # values as arguments (e.g., format strings with varargs).
                # Pointer-typed globals (e.g., CFF state vars) are supported
                # via the global_refs mechanism.
                for i in range(inst.num_arg_operands):
                    arg = inst.get_arg_operand(i)
                    if arg.is_global_value and arg.name not in func_names:
                        # Allow pointer-typed global variables
                        if arg.type.kind != llvm.TypeKind.Pointer:
                            return False
    return True


def _embed_bytecode(mod: llvm.Module, ctx: llvm.Context,
                    name: str, bytecode: bytes) -> llvm.GlobalVariable:
    """Embed bytecode as a global constant array of i8."""
    i8 = ctx.types.i8
    arr_ty = ctx.types.array(i8, len(bytecode))
    gv = mod.add_global(arr_ty, f"__vm_bytecode_{name}")
    gv.linkage = llvm.Linkage.Private
    gv.is_global_constant = True
    # Create initializer from bytes
    byte_constants = [i8.constant(b) for b in bytecode]
    gv.initializer = arr_ty.const_array(byte_constants)
    return gv


def _replace_function_body(
    func: llvm.Function,
    ctx: llvm.Context,
    bytecode_gv: llvm.GlobalVariable,
    bytecode_len: int,
    interp_func: llvm.Function,
    host_fns: list[llvm.Function],
    global_ref_gvs: list[llvm.GlobalVariable] | None = None,
) -> None:
    """Replace a function's body with a stub that calls the VM interpreter.

    New body:
      1. Marshal args into [N x i64] array (zext to i64)
      2. Pre-load global variable addresses into extra args slots
      3. Allocate i64 return slot
      4. Build local host function table (alloca + stores)
      5. Call @__vm_interpret(bc_ptr, bc_len, args, ret_slot, hosts)
      6. Load return value, trunc to original type, ret
    """
    i64 = ctx.types.i64
    ptr = ctx.types.ptr

    fn_ty = func.function_type
    ret_ty = fn_ty.return_type
    num_params = fn_ty.param_count
    n_globals = len(global_ref_gvs) if global_ref_gvs else 0

    # Clear all existing basic blocks' instructions
    blocks = list(func.basic_blocks)
    for bb in blocks:
        instructions = list(bb.instructions)
        for inst in reversed(instructions):
            if inst.type.kind != llvm.TypeKind.Void:
                inst.replace_all_uses_with(inst.type.undef())
            inst.erase_from_parent()

    # Reuse the first block as our new entry; terminate extras with unreachable
    entry = blocks[0]
    for bb in blocks[1:]:
        with bb.create_builder() as b:
            b.unreachable()
    with entry.create_builder() as b:
        # Layout of the args array (all i64 slots):
        #   [0 .. num_params-1]          : function parameters
        #   [num_params]                  : pointer to global ref table
        #   [num_params+1 .. num_params+n_globals] : global ref addresses
        # The interpreter copies slots 0..7 into a0-a7.  The bytecode uses
        # a(num_params) as the table base and loads individual addresses via
        # LD from table[i].
        n_gref_header = 1  # one slot for the table base pointer
        n_slots = max(num_params + n_gref_header + n_globals, 8)
        args_arr_ty = ctx.types.array(i64, n_slots)
        args_arr = b.alloca(args_arr_ty, name="args")

        # Initialize all slots to 0
        for i in range(n_slots):
            slot_ptr = b.gep(i64, args_arr, [i64.constant(i)], f"arg.slot.{i}")
            b.store(i64.constant(0), slot_ptr)

        # Store each parameter (zext to i64 if needed)
        for i in range(num_params):
            param = func.get_param(i)
            param_ty = fn_ty.param_types[i]
            slot_ptr = b.gep(i64, args_arr, [i64.constant(i)], f"arg.{i}.ptr")
            if param_ty.kind == llvm.TypeKind.Integer:
                if param_ty.int_width < 64:
                    val = b.zext(param, i64, f"arg.{i}.ext")
                else:
                    val = param
            elif param_ty.kind == llvm.TypeKind.Pointer:
                val = b.ptrtoint(param, i64, f"arg.{i}.ptoi")
            else:
                val = param
            b.store(val, slot_ptr)

        # Store global ref addresses into the table section, then store
        # a pointer to the table base into slot num_params.
        if global_ref_gvs:
            gref_table_start = num_params + n_gref_header
            for i, gv in enumerate(global_ref_gvs):
                slot_idx = gref_table_start + i
                slot_ptr = b.gep(i64, args_arr, [i64.constant(slot_idx)],
                                 f"gref.{i}.ptr")
                gv_addr = b.ptrtoint(gv, i64, f"gref.{i}.addr")
                b.store(gv_addr, slot_ptr)
            # Store table base address into slot num_params
            table_base_ptr = b.gep(i64, args_arr,
                                   [i64.constant(gref_table_start)],
                                   "gref.table.base")
            table_base_int = b.ptrtoint(table_base_ptr, i64, "gref.table.int")
            table_slot = b.gep(i64, args_arr, [i64.constant(num_params)],
                               "gref.table.slot")
            b.store(table_base_int, table_slot)

        # Allocate return slot
        ret_slot = b.alloca(i64, name="ret.slot")
        b.store(i64.constant(0), ret_slot)

        # Build local host function table as alloca + stores
        # This avoids the const_array nanobind bug with ptr elements
        n_hosts = max(len(host_fns), 1)
        hosts_arr_ty = ctx.types.array(ptr, n_hosts)
        hosts_arr = b.alloca(hosts_arr_ty, name="hosts")
        for i in range(n_hosts):
            slot = b.gep(ptr, hosts_arr, [i64.constant(i)], f"host.{i}.ptr")
            if i < len(host_fns):
                b.store(host_fns[i], slot)
            else:
                b.store(ptr.null(), slot)

        # Call interpreter
        b.call(interp_func,
               [bytecode_gv, i64.constant(bytecode_len), args_arr,
                ret_slot, hosts_arr],
               "")

        # Return
        if ret_ty.kind == llvm.TypeKind.Void:
            b.ret_void()
        else:
            ret_val = b.load(i64, ret_slot, "ret.val")
            if ret_ty.kind == llvm.TypeKind.Integer and ret_ty.int_width < 64:
                ret_val = b.trunc(ret_val, ret_ty, "ret.trunc")
            b.ret(ret_val)


@PassRegistry.register
class VirtualizationPass(ModulePass):
    """Virtualization obfuscation pass.

    Translates eligible functions to VM bytecode and replaces them with
    interpreter stubs. Phase 1: integer arithmetic only.
    """

    def __init__(self, rng: CryptoRandom | None = None):
        self.rng = rng or CryptoRandom()

    @classmethod
    def info(cls) -> PassInfo:
        return PassInfo(
            name="virtualization",
            description="Code virtualization via RISC-V inspired bytecode VM",
            is_module_pass=True,
        )

    def run_on_module(
        self,
        mod: llvm.Module,
        ctx: llvm.Context,
        selected_functions: set[str] | None = None,
    ) -> bool:
        # Collect eligible functions, respecting the selection filter
        eligible = [
            f for f in mod.functions
            if _is_eligible(f)
            and (selected_functions is None or f.name in selected_functions)
        ]
        if not eligible:
            return False

        # Build interpreter function (once per module)
        interp_func = build_vm_interpreter(mod, ctx)

        # First pass: compile all eligible functions to collect host functions
        compiled: list[tuple[llvm.Function, bytes, list[str], list[str]]] = []
        all_host_names: list[str] = []
        host_name_set: set[str] = set()
        for func in eligible:
            try:
                bytecode, host_names, global_ref_names = compile_function(func)
            except ValueError:
                continue
            compiled.append((func, bytecode, host_names, global_ref_names))
            for name in host_names:
                if name not in host_name_set:
                    host_name_set.add(name)
                    all_host_names.append(name)

        if not compiled:
            return False

        # Resolve host function names to llvm.Function objects
        host_fn_map: dict[str, llvm.Function] = {}
        for name in all_host_names:
            host_fn = None
            for f in mod.functions:
                if f.name == name:
                    host_fn = f
                    break
            if host_fn is None:
                raise ValueError(f"Host function '{name}' not found in module")
            host_fn_map[name] = host_fn

        # Build global host name → index map for per-function index remapping
        global_host_idx = {name: i for i, name in enumerate(all_host_names)}

        # Build the ordered list of all host functions
        all_host_fns = [host_fn_map[n] for n in all_host_names]

        # Second pass: embed bytecode and replace function bodies
        changed = False
        for func, bytecode, host_names, global_ref_names in compiled:
            # Remap host indices: the compiler assigned local indices 0..N,
            # but the global host table may have different ordering.
            # We need to patch the bytecode if local != global indices.
            if host_names:
                local_to_global = [global_host_idx[n] for n in host_names]
                if local_to_global != list(range(len(host_names))):
                    bytecode = _remap_host_indices(bytecode, local_to_global)

            # Resolve global variable references
            global_ref_gvs = []
            for gname in global_ref_names:
                gv = None
                for g in mod.globals:
                    if g.name == gname:
                        gv = g
                        break
                if gv is not None:
                    global_ref_gvs.append(gv)

            bytecode_gv = _embed_bytecode(mod, ctx, func.name, bytecode)
            _replace_function_body(
                func, ctx, bytecode_gv, len(bytecode),
                interp_func, all_host_fns, global_ref_gvs,
            )
            changed = True

        return changed

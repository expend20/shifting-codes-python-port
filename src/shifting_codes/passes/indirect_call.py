"""Indirect Call Pass — port of Polaris IndirectCall.cpp.

Replaces direct function calls with indirect calls through per-call-site
global variables with add/subtract pointer masking.

Polaris upgrade: each call site gets its own GV and unique mask,
instead of Pluto's shared function table.
"""

from __future__ import annotations

import llvm

from shifting_codes.passes import PassRegistry
from shifting_codes.passes.base import ModulePass, PassInfo
from shifting_codes.utils.crypto import CryptoRandom


@PassRegistry.register
class IndirectCallPass(ModulePass):

    def __init__(self, rng: CryptoRandom | None = None):
        self.rng = rng or CryptoRandom()
        self._counter = 0

    @classmethod
    def info(cls) -> PassInfo:
        return PassInfo(
            name="indirect_call",
            description="[Polaris] Per-call masked indirect calls via globals",
            is_module_pass=True,
        )

    def run_on_module(self, mod: llvm.Module, ctx: llvm.Context, selected_functions: set[str] | None = None) -> bool:
        ptr_ty = ctx.types.ptr
        # Use i64 for pointer arithmetic (safe for 64-bit targets)
        ptr_int_ty = ctx.types.i64

        # Collect eligible call sites across all functions
        call_sites = []
        for func in mod.functions:
            if func.is_declaration:
                continue
            for bb in func.basic_blocks:
                for inst in bb.instructions:
                    if inst.opcode != llvm.Opcode.Call:
                        continue
                    called = inst.get_operand(inst.num_operands - 1)
                    if not hasattr(called, 'name') or not called.name:
                        continue
                    # Check if callee has a body (exact definition)
                    callee = None
                    for f in mod.functions:
                        if f.name == called.name and not f.is_declaration:
                            callee = f
                            break
                    if callee is not None:
                        call_sites.append((inst, callee))

        if not call_sites:
            return False

        for call_inst, callee in call_sites:
            self._counter += 1
            mask = self.rng.get_uint32() & 0xFFFFFFFF
            if mask == 0:
                mask = 1

            bb = call_inst.block
            func_ty = call_inst.called_function_type

            # Create per-call-site GV storing the raw function pointer
            gv = mod.add_global(ptr_ty, f".indcall.{self._counter}")
            gv.initializer = callee
            gv.linkage = llvm.Linkage.Private

            # Collect arguments
            args = []
            for i in range(call_inst.num_operands - 1):
                args.append(call_inst.get_operand(i))

            # Insert: load GV → ptrtoint → add mask → sub mask → inttoptr → call
            with bb.create_builder() as builder:
                builder.position_before(call_inst)
                loaded = builder.load(ptr_ty, gv, "indcall.raw")
                int_val = builder.ptrtoint(loaded, ptr_int_ty, "indcall.int")
                added = builder.add(int_val, ptr_int_ty.constant(mask),
                                    "indcall.add")
                unmasked = builder.sub(added, ptr_int_ty.constant(mask),
                                       "indcall.unmask")
                func_ptr = builder.inttoptr(unmasked, ptr_ty, "indcall.ptr")
                ret_ty = func_ty.return_type
                call_name = "" if ret_ty.kind == llvm.TypeKind.Void else "indcall.ret"
                new_call = builder.call(func_ty, func_ptr, args, call_name)

            call_inst.replace_all_uses_with(new_call)
            call_inst.erase_from_parent()

        return True

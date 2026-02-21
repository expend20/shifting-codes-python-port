"""[Pluto] Indirect Call Pass — port of Pluto IndirectCall.cpp.

Replaces direct function calls to internal/private functions with
indirect calls through global variable pointers. Unlike the Polaris
variant, uses one shared GV per function with no pointer masking.
"""

from __future__ import annotations

import llvm

from shifting_codes.passes import PassRegistry
from shifting_codes.passes.base import ModulePass, PassInfo
from shifting_codes.utils.crypto import CryptoRandom


@PassRegistry.register
class PlutoIndirectCallPass(ModulePass):

    def __init__(self, rng: CryptoRandom | None = None):
        self.rng = rng or CryptoRandom()

    @classmethod
    def info(cls) -> PassInfo:
        return PassInfo(
            name="indirect_call_pluto",
            description="[Pluto] Replace direct calls with indirect calls via globals",
            is_module_pass=True,
        )

    def run_on_module(self, mod: llvm.Module, ctx: llvm.Context, selected_functions: set[str] | None = None) -> bool:
        ptr_ty = ctx.types.ptr

        # Step 1: Collect internal/private functions with bodies
        internal_funcs = []
        for func in mod.functions:
            if func.is_declaration:
                continue
            if func.linkage in (llvm.Linkage.Internal, llvm.Linkage.Private):
                internal_funcs.append(func)

        if not internal_funcs:
            return False

        # Step 2: Collect call sites targeting these functions
        func_set = set(f.name for f in internal_funcs)
        call_sites = []  # (call_inst, callee_func)

        for func in mod.functions:
            for bb in func.basic_blocks:
                for inst in bb.instructions:
                    if inst.opcode == llvm.Opcode.Call:
                        called = inst.get_operand(inst.num_operands - 1)
                        if hasattr(called, 'name') and called.name in func_set:
                            call_sites.append((inst, called))

        if not call_sites:
            return False

        # Step 3: Create global variable for each internal function
        func_globals: dict[str, llvm.GlobalVariable] = {}
        for func in internal_funcs:
            gv = mod.add_global(ptr_ty, f".indcall.{func.name}")
            gv.initializer = func
            gv.linkage = llvm.Linkage.Private
            func_globals[func.name] = gv

        # Step 4: Replace each call site with an indirect call
        for call_inst, callee in call_sites:
            callee_name = callee.name
            gv = func_globals.get(callee_name)
            if gv is None:
                continue

            bb = call_inst.block
            func_ty = call_inst.called_function_type

            # Collect arguments from original call
            args = []
            for i in range(call_inst.num_operands - 1):
                args.append(call_inst.get_operand(i))

            # Use empty name for void-returning calls to avoid LLVM crash
            ret_name = "" if func_ty.return_type.kind == llvm.TypeKind.Void else "indcall.ret"

            with bb.create_builder() as builder:
                builder.position_before(call_inst)
                loaded = builder.load(ptr_ty, gv, "indcall.ptr")
                new_call = builder.call(func_ty, loaded, args, ret_name)

            call_inst.replace_all_uses_with(new_call)
            call_inst.erase_from_parent()

        return True

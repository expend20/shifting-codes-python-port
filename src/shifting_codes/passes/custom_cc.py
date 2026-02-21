"""Custom Calling Convention Pass — port of Polaris CustomCC.cpp.

Randomly assigns non-standard calling conventions to internal functions
and their call sites, breaking decompiler assumptions about register
usage, stack frame layout, and argument passing.
"""

from __future__ import annotations

import llvm

from shifting_codes.passes import PassRegistry
from shifting_codes.passes.base import ModulePass, PassInfo
from shifting_codes.utils.crypto import CryptoRandom

# Pool of standard non-C calling conventions that change ABI behaviour.
_CC_POOL = [
    llvm.CallConv.Fast,
    llvm.CallConv.Cold,
    llvm.CallConv.PreserveMost,
    llvm.CallConv.PreserveAll,
    llvm.CallConv.X86RegCall,
    llvm.CallConv.X86_64_SysV,
    llvm.CallConv.Win64,
]


@PassRegistry.register
class CustomCCPass(ModulePass):

    def __init__(self, rng: CryptoRandom | None = None):
        self.rng = rng or CryptoRandom()

    @classmethod
    def info(cls) -> PassInfo:
        return PassInfo(
            name="custom_cc",
            description="[Polaris] Randomly assign non-standard calling conventions",
            is_module_pass=True,
        )

    def run_on_module(self, mod: llvm.Module, ctx: llvm.Context, selected_functions: set[str] | None = None) -> bool:
        # Collect internal/private functions with bodies
        internal_funcs = []
        for func in mod.functions:
            if func.is_declaration:
                continue
            if func.linkage in (llvm.Linkage.Internal, llvm.Linkage.Private):
                internal_funcs.append(func)

        if not internal_funcs:
            return False

        for func in internal_funcs:
            cc = _CC_POOL[self.rng.get_range(len(_CC_POOL))]
            func.calling_conv = cc

            # Fix all call sites targeting this function
            func_name = func.name
            for caller in mod.functions:
                for bb in caller.basic_blocks:
                    for inst in bb.instructions:
                        if inst.opcode == llvm.Opcode.Call:
                            called = inst.get_operand(inst.num_operands - 1)
                            if hasattr(called, 'name') and called.name == func_name:
                                inst.instruction_call_conv = cc

        return True

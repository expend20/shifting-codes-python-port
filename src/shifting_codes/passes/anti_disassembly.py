"""Anti-Disassembly Pass — injects junk bytes via inline assembly.

Crafted x86 byte sequences exploit linear-sweep disassembler weaknesses.
They look like multi-byte instructions but include a hidden short jump
that skips junk bytes. The CPU executes correctly but disassemblers
(IDA, Ghidra, objdump) get desynchronized.

Only active when the module target triple contains "x86" or "x86_64".
"""

from __future__ import annotations

import llvm

from shifting_codes.passes import PassRegistry
from shifting_codes.passes.base import FunctionPass, PassInfo
from shifting_codes.utils.crypto import CryptoRandom


@PassRegistry.register
class AntiDisassemblyPass(FunctionPass):

    def __init__(self, rng: CryptoRandom | None = None, density: float = 0.3):
        self.rng = rng or CryptoRandom()
        self.density = max(0.0, min(1.0, density))

    @classmethod
    def info(cls) -> PassInfo:
        return PassInfo(
            name="anti_disassembly",
            description="[VMwhere] Inject anti-disassembly junk bytes (x86 only)",
        )

    def _make_asm_string(self) -> str:
        """Build an anti-disassembly byte pattern.

        Pattern: 0x48 0xB8 <r1> <r2> <r3> 0xEB 0x08 0xFF 0xFF 0x48 0x31 0xC0 0xEB 0xF7 0xE8

        The 0x48 0xB8 prefix makes disassemblers think it's a 10-byte
        movabs rax, imm64. The 0xEB 0x08 is a short jump +8 that skips
        the junk bytes. Random bytes add variation.
        """
        r1 = self.rng.get_range(256)
        r2 = self.rng.get_range(256)
        r3 = self.rng.get_range(256)
        return (
            f".byte 0x48, 0xB8, {r1:#04x}, {r2:#04x}, {r3:#04x}, "
            f"0xEB, 0x08, 0xFF, 0xFF, 0x48, 0x31, 0xC0, 0xEB, 0xF7, 0xE8"
        )

    def run_on_function(self, func: llvm.Function, ctx: llvm.Context) -> bool:
        # Check target triple — only inject on x86
        mod = func.module
        triple = mod.target_triple.lower() if mod.target_triple else ""
        if "x86" not in triple and "i386" not in triple and "i686" not in triple:
            return False

        # void() function type for the inline asm calls
        void_fn_ty = ctx.types.function(ctx.types.void, [])
        changed = False

        for bb in func.basic_blocks:
            instructions = list(bb.instructions)
            if not instructions:
                continue

            # Find first non-PHI instruction as insertion point
            first_non_phi = None
            non_phi_insts = []
            for inst in instructions:
                if inst.opcode == llvm.Opcode.PHI:
                    continue
                if first_non_phi is None:
                    first_non_phi = inst
                non_phi_insts.append(inst)

            if first_non_phi is None:
                continue

            # Always inject at block start (before first non-PHI)
            asm_str = self._make_asm_string()
            asm_val = llvm.get_inline_asm(
                void_fn_ty, asm_str, "~{eax}", True, False,
                llvm.InlineAsmDialect.ATT, False)
            with bb.create_builder() as b:
                b.position_before(first_non_phi)
                b.call(void_fn_ty, asm_val, [], "")
            changed = True

            # Randomly inject before other non-PHI, non-terminator instructions
            for inst in non_phi_insts:
                if inst == first_non_phi:
                    continue
                if inst.is_terminator:
                    continue
                if self.rng.get_range(1000) < int(self.density * 1000):
                    asm_str = self._make_asm_string()
                    asm_val = llvm.get_inline_asm(
                        void_fn_ty, asm_str, "~{eax}", True, False,
                        llvm.InlineAsmDialect.ATT, False)
                    with bb.create_builder() as b:
                        b.position_before(inst)
                        b.call(void_fn_ty, asm_val, [], "")

        return changed

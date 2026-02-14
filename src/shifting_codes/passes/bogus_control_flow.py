"""Bogus Control Flow Pass — port of Pluto BogusControlFlowPass.cpp.

Inserts opaque predicates (always-true conditions) before unconditional
branches, adding dead code paths to confuse static analysis.
"""

from __future__ import annotations

import llvm

from shifting_codes.passes import PassRegistry
from shifting_codes.passes.base import FunctionPass, PassInfo
from shifting_codes.utils.crypto import CryptoRandom


@PassRegistry.register
class BogusControlFlowPass(FunctionPass):

    def __init__(self, rng: CryptoRandom | None = None):
        self.rng = rng or CryptoRandom()
        self._bcf_counter = 0

    @classmethod
    def info(cls) -> PassInfo:
        return PassInfo(
            name="bogus_control_flow",
            description="Insert opaque predicates and bogus branches",
        )

    def run_on_function(self, func: llvm.Function, ctx: llvm.Context) -> bool:
        mod = func.module
        i32 = ctx.types.i32
        changed = False

        # Collect blocks with unconditional branches to transform
        blocks_to_transform = []
        for bb in func.basic_blocks:
            term = bb.terminator
            if term is None:
                continue
            if term.opcode == llvm.Opcode.Br:
                succs = list(term.successors)
                if len(succs) == 1:
                    blocks_to_transform.append(bb)

        for bb in blocks_to_transform:
            term = bb.terminator
            succs = list(term.successors)
            if len(succs) != 1:
                continue
            real_target = succs[0]

            self._bcf_counter += 1
            tag = self._bcf_counter

            # Create global variables for the opaque predicate
            x_gv = mod.add_global(i32, f"__bcf_x_{tag}")
            x_gv.initializer = i32.constant(0)
            x_gv.linkage = llvm.Linkage.Private

            y_gv = mod.add_global(i32, f"__bcf_y_{tag}")
            y_gv.initializer = i32.constant(0)
            y_gv.linkage = llvm.Linkage.Private

            # Create bogus block that branches back to the real target
            bogus_bb = func.append_basic_block(f"bcf.bogus.{tag}")
            with bogus_bb.create_builder() as builder:
                builder.br(real_target)

            # Fix PHI nodes in real_target: add incoming from bogus_bb
            # The bogus block is unreachable, so we use undef values
            for inst in real_target.instructions:
                if inst.opcode == llvm.Opcode.PHI:
                    inst.add_incoming(inst.type.undef(), bogus_bb)
                else:
                    break  # PHIs are always at the start of a block

            # Insert opaque predicate before the terminator:
            #   (y < 10) || (x * (x + 1) % 2 == 0)  — always true
            with bb.create_builder() as builder:
                builder.position_before(term)

                x_val = builder.load(i32, x_gv, "bcf.x")
                y_val = builder.load(i32, y_gv, "bcf.y")

                # cond1: y < 10
                cond1 = builder.icmp(
                    llvm.IntPredicate.SLT, y_val, i32.constant(10), "bcf.cmp1"
                )

                # cond2: x * (x + 1) % 2 == 0  (always true: product of consecutive ints is even)
                xp1 = builder.add(x_val, i32.constant(1), "bcf.xp1")
                xmul = builder.mul(x_val, xp1, "bcf.xmul")
                xmod = builder.urem(xmul, i32.constant(2), "bcf.xmod")
                cond2 = builder.icmp(
                    llvm.IntPredicate.EQ, xmod, i32.constant(0), "bcf.cmp2"
                )

                bogus_cond = builder.or_(cond1, cond2, "bcf.cond")
                builder.cond_br(bogus_cond, real_target, bogus_bb)

            # Remove original unconditional branch
            term.erase_from_parent()
            changed = True

        return changed

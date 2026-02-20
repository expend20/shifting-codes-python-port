"""Bogus Control Flow Pass — port of Pluto BogusControlFlowPass.cpp.

Splits each basic block into head/body/tail, clones the body block,
and reconnects them with opaque predicates (always-true conditions)
to confuse static analysis.
"""

from __future__ import annotations

import llvm

from shifting_codes.passes import PassRegistry
from shifting_codes.passes.base import FunctionPass, PassInfo
from shifting_codes.utils.crypto import CryptoRandom


def _safe_remap_operands(inst, value_map: dict):
    """Remap operands using try/except for nanobind cross-type __eq__ safety."""
    for i in range(inst.num_operands):
        op = inst.get_operand(i)
        try:
            if op in value_map:
                inst.set_operand(i, value_map[op])
        except TypeError:
            pass


def _clone_basic_block(body_bb: llvm.BasicBlock, func: llvm.Function,
                       ctx: llvm.Context, tag: int) -> llvm.BasicBlock:
    """Clone a basic block's instructions into a new block.

    Creates a new block with cloned copies of all instructions from body_bb.
    Operand references to values defined within body_bb are remapped to their
    cloned counterparts.
    """
    clone_bb = func.append_basic_block(f"bcf.clone.{tag}")

    # Map from original values to cloned values
    value_map: dict = {}

    with clone_bb.create_builder() as builder:
        for inst in body_bb.instructions:
            cloned = inst.instruction_clone()
            builder.insert_into_builder_with_name(cloned, "")
            value_map[inst] = cloned

    # Remap operands: replace references to original values with cloned ones
    for inst in clone_bb.instructions:
        _safe_remap_operands(inst, value_map)

    return clone_bb


def _create_bogus_cmp(builder, mod: llvm.Module, i32, tag: int, suffix: str):
    """Create opaque predicate: (y < 10 || x*(x+1) % 2 == 0) — always true."""
    x_gv = mod.add_global(i32, f"__bcf_x_{tag}_{suffix}")
    x_gv.initializer = i32.constant(0)
    x_gv.linkage = llvm.Linkage.Private

    y_gv = mod.add_global(i32, f"__bcf_y_{tag}_{suffix}")
    y_gv.initializer = i32.constant(0)
    y_gv.linkage = llvm.Linkage.Private

    x_val = builder.load(i32, x_gv, "bcf.x")
    y_val = builder.load(i32, y_gv, "bcf.y")

    # cond1: y < 10
    cond1 = builder.icmp(llvm.IntPredicate.SLT, y_val, i32.constant(10), "bcf.cmp1")

    # cond2: x * (x + 1) % 2 == 0  (always true)
    xp1 = builder.add(x_val, i32.constant(1), "bcf.xp1")
    xmul = builder.mul(x_val, xp1, "bcf.xmul")
    xmod = builder.urem(xmul, i32.constant(2), "bcf.xmod")
    cond2 = builder.icmp(llvm.IntPredicate.EQ, xmod, i32.constant(0), "bcf.cmp2")

    return builder.or_(cond1, cond2, "bcf.cond")


@PassRegistry.register
class BogusControlFlowPass(FunctionPass):

    def __init__(self, rng: CryptoRandom | None = None):
        self.rng = rng or CryptoRandom()
        self._bcf_counter = 0

    @classmethod
    def info(cls) -> PassInfo:
        return PassInfo(
            name="bogus_control_flow",
            description="[Pluto] Insert opaque predicates and bogus branches",
        )

    def run_on_function(self, func: llvm.Function, ctx: llvm.Context) -> bool:
        mod = func.module
        i32 = ctx.types.i32
        changed = False

        # Collect original blocks (snapshot — we'll be modifying the CFG)
        orig_blocks = list(func.basic_blocks)

        for bb in orig_blocks:
            term = bb.terminator
            if term is None:
                continue
            # Skip invoke and exception-handling blocks
            if term.opcode == llvm.Opcode.Invoke:
                continue

            self._bcf_counter += 1
            tag = self._bcf_counter

            # Step 1: Split into headBB | bodyBB | tailBB
            # Find the first non-PHI instruction to split at
            first_non_phi = None
            for inst in bb.instructions:
                if inst.opcode != llvm.Opcode.PHI:
                    first_non_phi = inst
                    break

            if first_non_phi is None:
                continue

            # If block has only a terminator, skip (nothing to split)
            if first_non_phi.is_terminator:
                continue

            # Split: headBB → bodyBB (at first non-PHI)
            body_bb = bb.split_basic_block(first_non_phi, f"bcf.body.{tag}")

            # Split: bodyBB → tailBB (at bodyBB's terminator)
            body_term = body_bb.terminator
            if body_term is None:
                continue
            tail_bb = body_bb.split_basic_block(body_term, f"bcf.tail.{tag}")

            # Step 2: Clone bodyBB
            clone_bb = _clone_basic_block(body_bb, func, ctx, tag)

            # Step 3: Rewire with bogus branches
            # 3.1: Remove unconditional branches from headBB, bodyBB, cloneBB
            head_term = bb.terminator
            body_term = body_bb.terminator
            clone_term = clone_bb.terminator

            head_term.erase_from_parent()
            body_term.erase_from_parent()
            clone_term.erase_from_parent()

            # 3.2: headBB → (cond1 ? bodyBB : cloneBB)
            with bb.create_builder() as builder:
                cond1 = _create_bogus_cmp(builder, mod, i32, tag, "head")
                builder.cond_br(cond1, body_bb, clone_bb)

            # 3.3: bodyBB → (cond2 ? tailBB : cloneBB)
            with body_bb.create_builder() as builder:
                cond2 = _create_bogus_cmp(builder, mod, i32, tag, "body")
                builder.cond_br(cond2, tail_bb, clone_bb)

            # 3.4: cloneBB → bodyBB (unconditional)
            with clone_bb.create_builder() as builder:
                builder.br(body_bb)

            # Fix PHI nodes: bodyBB now has cloneBB as an additional predecessor
            for inst in body_bb.instructions:
                if inst.opcode == llvm.Opcode.PHI:
                    inst.add_incoming(inst.type.undef(), clone_bb)
                else:
                    break

            changed = True

        return changed

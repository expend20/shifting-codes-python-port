"""Arithmetic Substitution Pass — port of Pluto Substitution.cpp.

Replaces binary arithmetic/logic ops with algebraically equivalent
but more complex expressions. 13 patterns total:
  4 Add, 3 Sub, 2 And, 2 Or, 2 Xor.
"""

from __future__ import annotations

import llvm

from shifting_codes.passes import PassRegistry
from shifting_codes.passes.base import FunctionPass, PassInfo
from shifting_codes.utils.crypto import CryptoRandom


@PassRegistry.register
class SubstitutionPass(FunctionPass):

    def __init__(self, rng: CryptoRandom | None = None):
        self.rng = rng or CryptoRandom()

    @classmethod
    def info(cls) -> PassInfo:
        return PassInfo(
            name="substitution",
            description="Arithmetic instruction substitution (13 Pluto patterns)",
        )

    def run_on_function(self, func: llvm.Function, ctx: llvm.Context) -> bool:
        changed = False
        for bb in func.basic_blocks:
            changed |= self._run_on_block(bb)
        return changed

    def _run_on_block(self, bb: llvm.BasicBlock) -> bool:
        substitutable = {
            llvm.Opcode.Add, llvm.Opcode.Sub,
            llvm.Opcode.And, llvm.Opcode.Or, llvm.Opcode.Xor,
        }

        to_transform = []
        for inst in bb.instructions:
            if inst.opcode in substitutable and inst.type.kind == llvm.TypeKind.Integer:
                if inst.num_operands == 2:
                    to_transform.append(inst)

        for inst in to_transform:
            a = inst.get_operand(0)
            b = inst.get_operand(1)

            with bb.create_builder() as builder:
                builder.position_before(inst)
                replacement = self._substitute(builder, inst.opcode, a, b)

            if replacement is not None:
                inst.replace_all_uses_with(replacement)
                inst.erase_from_parent()

        return len(to_transform) > 0

    def _substitute(
        self, builder: llvm.Builder, opcode: llvm.Opcode,
        a: llvm.Value, b: llvm.Value,
    ) -> llvm.Value | None:
        if opcode == llvm.Opcode.Add:
            return self._substitute_add(builder, a, b)
        elif opcode == llvm.Opcode.Sub:
            return self._substitute_sub(builder, a, b)
        elif opcode == llvm.Opcode.And:
            return self._substitute_and(builder, a, b)
        elif opcode == llvm.Opcode.Or:
            return self._substitute_or(builder, a, b)
        elif opcode == llvm.Opcode.Xor:
            return self._substitute_xor(builder, a, b)
        return None

    # ── Add (4 variants) ──────────────────────────────────────────────

    def _substitute_add(self, builder: llvm.Builder, a: llvm.Value, b: llvm.Value) -> llvm.Value:
        choice = self.rng.get_range(4)
        if choice == 0:
            return self._add_neg(builder, a, b)
        elif choice == 1:
            return self._add_double_neg(builder, a, b)
        elif choice == 2:
            return self._add_rand(builder, a, b)
        else:
            return self._add_rand2(builder, a, b)

    def _add_neg(self, builder: llvm.Builder, a: llvm.Value, b: llvm.Value) -> llvm.Value:
        """a + b → a - (-b)"""
        neg_b = builder.neg(b, "sub.neg")
        return builder.sub(a, neg_b, "sub.add")

    def _add_double_neg(self, builder: llvm.Builder, a: llvm.Value, b: llvm.Value) -> llvm.Value:
        """a + b → -(-a + (-b))"""
        neg_a = builder.neg(a, "sub.neg_a")
        neg_b = builder.neg(b, "sub.neg_b")
        s = builder.add(neg_a, neg_b, "sub.sum")
        return builder.neg(s, "sub.add")

    def _add_rand(self, builder: llvm.Builder, a: llvm.Value, b: llvm.Value) -> llvm.Value:
        """a + b → (a + r) + b - r"""
        r = a.type.constant(self.rng.get_uint32())
        t = builder.add(a, r, "sub.ar")
        t = builder.add(t, b, "sub.arb")
        return builder.sub(t, r, "sub.add")

    def _add_rand2(self, builder: llvm.Builder, a: llvm.Value, b: llvm.Value) -> llvm.Value:
        """a + b → (a - r) + b + r"""
        r = a.type.constant(self.rng.get_uint32())
        t = builder.sub(a, r, "sub.ar")
        t = builder.add(t, b, "sub.arb")
        return builder.add(t, r, "sub.add")

    # ── Sub (3 variants) ──────────────────────────────────────────────

    def _substitute_sub(self, builder: llvm.Builder, a: llvm.Value, b: llvm.Value) -> llvm.Value:
        choice = self.rng.get_range(3)
        if choice == 0:
            return self._sub_neg(builder, a, b)
        elif choice == 1:
            return self._sub_rand(builder, a, b)
        else:
            return self._sub_rand2(builder, a, b)

    def _sub_neg(self, builder: llvm.Builder, a: llvm.Value, b: llvm.Value) -> llvm.Value:
        """a - b → a + (-b)"""
        neg_b = builder.neg(b, "sub.neg")
        return builder.add(a, neg_b, "sub.sub")

    def _sub_rand(self, builder: llvm.Builder, a: llvm.Value, b: llvm.Value) -> llvm.Value:
        """a - b → (a + r) - b - r"""
        r = a.type.constant(self.rng.get_uint32())
        t = builder.add(a, r, "sub.ar")
        t = builder.sub(t, b, "sub.arb")
        return builder.sub(t, r, "sub.sub")

    def _sub_rand2(self, builder: llvm.Builder, a: llvm.Value, b: llvm.Value) -> llvm.Value:
        """a - b → (a - r) - b + r"""
        r = a.type.constant(self.rng.get_uint32())
        t = builder.sub(a, r, "sub.ar")
        t = builder.sub(t, b, "sub.arb")
        return builder.add(t, r, "sub.sub")

    # ── And (2 variants) ──────────────────────────────────────────────

    def _substitute_and(self, builder: llvm.Builder, a: llvm.Value, b: llvm.Value) -> llvm.Value:
        choice = self.rng.get_range(2)
        if choice == 0:
            return self._and_substitute(builder, a, b)
        else:
            return self._and_substitute_rand(builder, a, b)

    def _and_substitute(self, builder: llvm.Builder, a: llvm.Value, b: llvm.Value) -> llvm.Value:
        """a & b → (a ^ ~b) & a"""
        not_b = builder.not_(b, "sub.not_b")
        x = builder.xor(a, not_b, "sub.xor")
        return builder.and_(x, a, "sub.and")

    def _and_substitute_rand(self, builder: llvm.Builder, a: llvm.Value, b: llvm.Value) -> llvm.Value:
        """a & b → ~(~a | ~b) & (r | ~r)"""
        r = a.type.constant(self.rng.get_uint32())
        not_r = builder.not_(r, "sub.not_r")
        not_a = builder.not_(a, "sub.not_a")
        not_b = builder.not_(b, "sub.not_b")
        t = builder.or_(not_a, not_b, "sub.or_nots")
        t = builder.not_(t, "sub.nand")
        mask = builder.or_(r, not_r, "sub.mask")
        return builder.and_(t, mask, "sub.and")

    # ── Or (2 variants) ───────────────────────────────────────────────

    def _substitute_or(self, builder: llvm.Builder, a: llvm.Value, b: llvm.Value) -> llvm.Value:
        choice = self.rng.get_range(2)
        if choice == 0:
            return self._or_substitute(builder, a, b)
        else:
            return self._or_substitute_rand(builder, a, b)

    def _or_substitute(self, builder: llvm.Builder, a: llvm.Value, b: llvm.Value) -> llvm.Value:
        """a | b → (a & b) | (a ^ b)"""
        and_val = builder.and_(a, b, "sub.and")
        xor_val = builder.xor(a, b, "sub.xor")
        return builder.or_(and_val, xor_val, "sub.or")

    def _or_substitute_rand(self, builder: llvm.Builder, a: llvm.Value, b: llvm.Value) -> llvm.Value:
        """a | b → ~(~a & ~b) & (r | ~r)"""
        r = a.type.constant(self.rng.get_uint32())
        not_r = builder.not_(r, "sub.not_r")
        not_a = builder.not_(a, "sub.not_a")
        not_b = builder.not_(b, "sub.not_b")
        t = builder.and_(not_a, not_b, "sub.and_nots")
        t = builder.not_(t, "sub.nor")
        mask = builder.or_(r, not_r, "sub.mask")
        return builder.and_(t, mask, "sub.or")

    # ── Xor (2 variants) ──────────────────────────────────────────────

    def _substitute_xor(self, builder: llvm.Builder, a: llvm.Value, b: llvm.Value) -> llvm.Value:
        choice = self.rng.get_range(2)
        if choice == 0:
            return self._xor_substitute(builder, a, b)
        else:
            return self._xor_substitute_rand(builder, a, b)

    def _xor_substitute(self, builder: llvm.Builder, a: llvm.Value, b: llvm.Value) -> llvm.Value:
        """a ^ b → (~a & b) | (a & ~b)"""
        not_a = builder.not_(a, "sub.not_a")
        not_b = builder.not_(b, "sub.not_b")
        left = builder.and_(not_a, b, "sub.left")
        right = builder.and_(a, not_b, "sub.right")
        return builder.or_(left, right, "sub.xor")

    def _xor_substitute_rand(self, builder: llvm.Builder, a: llvm.Value, b: llvm.Value) -> llvm.Value:
        """a ^ b → (~a & r | a & ~r) ^ (~b & r | b & ~r)"""
        r = a.type.constant(self.rng.get_uint32())
        not_r = builder.not_(r, "sub.not_r")

        not_a = builder.not_(a, "sub.not_a")
        l1 = builder.and_(not_a, r, "sub.l1")
        l2 = builder.and_(a, not_r, "sub.l2")
        left = builder.or_(l1, l2, "sub.left")

        not_b = builder.not_(b, "sub.not_b")
        r1 = builder.and_(not_b, r, "sub.r1")
        r2 = builder.and_(b, not_r, "sub.r2")
        right = builder.or_(r1, r2, "sub.right")

        return builder.xor(left, right, "sub.xor")

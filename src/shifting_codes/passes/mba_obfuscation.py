"""Mixed Boolean-Arithmetic Obfuscation Pass — port of Pluto MBAObfuscation.cpp.

Uses Z3-generated linear MBA expressions to replace binary operations and
constants with equivalent but complex arithmetic.
"""

from __future__ import annotations

import llvm

from shifting_codes.passes import PassRegistry
from shifting_codes.passes.base import FunctionPass, PassInfo
from shifting_codes.utils.crypto import CryptoRandom
from shifting_codes.utils.mba import (
    TRUTH_TABLES,
    generate_linear_mba,
    generate_univariate_poly,
)

NUM_COEFFS = 5


@PassRegistry.register
class MBAObfuscationPass(FunctionPass):

    def __init__(self, rng: CryptoRandom | None = None):
        self.rng = rng or CryptoRandom()

    @classmethod
    def info(cls) -> PassInfo:
        return PassInfo(
            name="mba_obfuscation",
            description="Mixed Boolean-Arithmetic obfuscation via Z3",
        )

    def run_on_function(self, func: llvm.Function, ctx: llvm.Context) -> bool:
        changed = False
        for bb in func.basic_blocks:
            orig_insts = list(bb.instructions)
            for inst in orig_insts:
                opcode = inst.opcode
                if opcode in _BINARY_OPS and inst.type.kind == llvm.TypeKind.Integer:
                    if inst.num_operands == 2 and inst.type.int_width <= 64:
                        self._substitute_binary(inst, bb)
                        changed = True
                elif opcode in (llvm.Opcode.Store, llvm.Opcode.ICmp):
                    for i in range(inst.num_operands):
                        op = inst.get_operand(i)
                        if (op.type.kind == llvm.TypeKind.Integer
                                and op.is_constant and hasattr(op, 'const_zext_value')):
                            try:
                                bit_width = op.type.int_width
                                if bit_width <= 64:
                                    self._substitute_constant(inst, i, bb)
                                    changed = True
                            except Exception:
                                pass
        return changed

    def _substitute_binary(self, inst: llvm.Value, bb: llvm.BasicBlock) -> None:
        opcode = inst.opcode
        coeffs = generate_linear_mba(NUM_COEFFS, self.rng)

        # Adjust coefficients for the target operation
        if opcode == llvm.Opcode.Add:
            coeffs[2] += 1  # x
            coeffs[4] += 1  # y
        elif opcode == llvm.Opcode.Sub:
            coeffs[2] += 1  # x
            coeffs[4] -= 1  # -y
        elif opcode == llvm.Opcode.Xor:
            coeffs[5] += 1  # x ^ y
        elif opcode == llvm.Opcode.And:
            coeffs[0] += 1  # x & y
        elif opcode == llvm.Opcode.Or:
            coeffs[6] += 1  # x | y

        a = inst.get_operand(0)
        b = inst.get_operand(1)

        with bb.create_builder() as builder:
            builder.position_before(inst)
            mba_expr = self._insert_linear_mba(builder, coeffs, a, b, a.type)

            if a.type.int_width <= 32:
                mba_expr = self._insert_polynomial_mba(builder, mba_expr, a.type)

        inst.replace_all_uses_with(mba_expr)
        inst.erase_from_parent()

    def _substitute_constant(self, inst: llvm.Value, operand_idx: int, bb: llvm.BasicBlock) -> None:
        val = inst.get_operand(operand_idx)
        try:
            const_val = val.const_zext_value
        except Exception:
            return

        bit_width = val.type.int_width
        coeffs = generate_linear_mba(NUM_COEFFS, self.rng)
        coeffs[14] -= const_val

        # For constants, we need dummy x/y values
        mod = inst.block.parent.module if hasattr(inst.block, 'parent') else None
        if mod is None:
            return

        with bb.create_builder() as builder:
            builder.position_before(inst)

            ty = val.type
            # Create dummy global variables for x and y
            x_gv = mod.add_global(ty, f".mba.x.{self.rng.get_uint32()}")
            x_gv.initializer = ty.constant(self.rng.get_uint32())
            x_gv.linkage = llvm.Linkage.Private

            y_gv = mod.add_global(ty, f".mba.y.{self.rng.get_uint32()}")
            y_gv.initializer = ty.constant(self.rng.get_uint32())
            y_gv.linkage = llvm.Linkage.Private

            x = builder.load(ty, x_gv, "mba.x")
            y = builder.load(ty, y_gv, "mba.y")

            mba_expr = self._insert_linear_mba(builder, coeffs, x, y, ty)

            if bit_width <= 32:
                mba_expr = self._insert_polynomial_mba(builder, mba_expr, ty)

        inst.set_operand(operand_idx, mba_expr)

    def _insert_linear_mba(
        self, builder: llvm.Builder, coeffs: list[int],
        x: llvm.Value, y: llvm.Value, ty: llvm.Type,
    ) -> llvm.Value:
        """Build IR for: sum(coeffs[i] * bool_expr_i(x, y))."""
        result = ty.constant(0)

        for i in range(15):
            if coeffs[i] == 0:
                continue

            bool_expr = self._build_bool_expr(builder, i, x, y, ty)
            coeff_const = ty.constant(coeffs[i], sign_extend=True)
            term = builder.mul(coeff_const, bool_expr, "mba.term")
            result = builder.add(result, term, "mba.acc")

        return result

    def _build_bool_expr(
        self, builder: llvm.Builder, idx: int,
        x: llvm.Value, y: llvm.Value, ty: llvm.Type,
    ) -> llvm.Value:
        """Build the boolean expression for truth table index `idx`."""
        if idx == 0:    # x & y
            return builder.and_(x, y, "mba.and")
        elif idx == 1:  # x & ~y
            return builder.and_(x, builder.not_(y, "mba.ny"), "mba.andny")
        elif idx == 2:  # x
            return x
        elif idx == 3:  # ~x & y
            return builder.and_(builder.not_(x, "mba.nx"), y, "mba.nxand")
        elif idx == 4:  # y
            return y
        elif idx == 5:  # x ^ y
            return builder.xor(x, y, "mba.xor")
        elif idx == 6:  # x | y
            return builder.or_(x, y, "mba.or")
        elif idx == 7:  # ~(x | y)
            return builder.not_(builder.or_(x, y, "mba.or7"), "mba.nor")
        elif idx == 8:  # ~(x ^ y)
            return builder.not_(builder.xor(x, y, "mba.xor8"), "mba.xnor")
        elif idx == 9:  # ~y
            return builder.not_(y, "mba.ny9")
        elif idx == 10: # x | ~y
            return builder.or_(x, builder.not_(y, "mba.ny10"), "mba.orny")
        elif idx == 11: # ~x
            return builder.not_(x, "mba.nx11")
        elif idx == 12: # ~x | y
            return builder.or_(builder.not_(x, "mba.nx12"), y, "mba.nxory")
        elif idx == 13: # ~(x & y)
            return builder.not_(builder.and_(x, y, "mba.and13"), "mba.nand")
        elif idx == 14: # -1
            return ty.constant(-1, sign_extend=True)
        return ty.constant(0)

    def _insert_polynomial_mba(
        self, builder: llvm.Builder, linear_expr: llvm.Value, ty: llvm.Type,
    ) -> llvm.Value:
        """Wrap linear MBA in polynomial: result = a1 * (b1 * expr + b0) + a0."""
        bit_width = ty.int_width
        (a0, a1), (b0, b1) = generate_univariate_poly(bit_width, self.rng)

        b1_c = ty.constant(b1)
        b0_c = ty.constant(b0)
        a1_c = ty.constant(a1)
        a0_c = ty.constant(a0)

        expr = builder.mul(b1_c, linear_expr, "mba.poly.b1x")
        expr = builder.add(expr, b0_c, "mba.poly.b1x_b0")
        expr = builder.mul(a1_c, expr, "mba.poly.a1f")
        expr = builder.add(expr, a0_c, "mba.poly.result")
        return expr


_BINARY_OPS = {
    llvm.Opcode.Add, llvm.Opcode.Sub,
    llvm.Opcode.And, llvm.Opcode.Or, llvm.Opcode.Xor,
}

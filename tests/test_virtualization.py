"""Tests for the virtualization pass."""

import struct
import pytest
import llvm

from conftest import make_add_function, make_arith_function, make_branch_function

from shifting_codes.riscybusiness_vm.isa import (
    Opcode, Funct3Op64, Funct7Op64, Funct3Imm64, Funct3Branch,
    Funct3Load, Funct3Store, RegIndex,
    encode_r_type, encode_i_type, encode_s_type, encode_b_type,
    encode_u_type, encode_j_type,
    decode_opcode, decode_rd, decode_funct3, decode_rs1, decode_rs2,
    decode_funct7, decode_i_imm, decode_s_imm, decode_b_imm,
    decode_u_imm, decode_j_imm,
)
from shifting_codes.riscybusiness_vm.compiler import compile_function
from shifting_codes.passes.virtualization import VirtualizationPass


# ---------------------------------------------------------------------------
# ISA encoding/decoding tests
# ---------------------------------------------------------------------------


class TestISAEncoding:
    """Test instruction encoding and decoding roundtrips."""

    def test_r_type_add(self):
        """Encode ADD x1, x2, x3 and verify field extraction."""
        inst = encode_r_type(Opcode.OP64, rd=1, funct3=Funct3Op64.ADD,
                             rs1=2, rs2=3, funct7=Funct7Op64.NORMAL)
        assert decode_opcode(inst) == Opcode.OP64
        assert decode_rd(inst) == 1
        assert decode_funct3(inst) == Funct3Op64.ADD
        assert decode_rs1(inst) == 2
        assert decode_rs2(inst) == 3
        assert decode_funct7(inst) == Funct7Op64.NORMAL

    def test_r_type_sub(self):
        """Encode SUB x5, x6, x7."""
        inst = encode_r_type(Opcode.OP64, rd=5, funct3=Funct3Op64.ADD,
                             rs1=6, rs2=7, funct7=Funct7Op64.SUB_SRA)
        assert decode_opcode(inst) == Opcode.OP64
        assert decode_rd(inst) == 5
        assert decode_funct3(inst) == Funct3Op64.ADD
        assert decode_funct7(inst) == Funct7Op64.SUB_SRA

    def test_i_type_addi(self):
        """Encode ADDI x10, x11, 42 and verify."""
        inst = encode_i_type(Opcode.IMM64, rd=10, funct3=Funct3Imm64.ADDI,
                             rs1=11, imm12=42)
        assert decode_opcode(inst) == Opcode.IMM64
        assert decode_rd(inst) == 10
        assert decode_funct3(inst) == Funct3Imm64.ADDI
        assert decode_rs1(inst) == 11
        assert decode_i_imm(inst) == 42

    def test_i_type_negative_imm(self):
        """Encode ADDI with negative immediate."""
        inst = encode_i_type(Opcode.IMM64, rd=1, funct3=Funct3Imm64.ADDI,
                             rs1=2, imm12=(-8) & 0xFFF)
        assert decode_i_imm(inst) == -8

    def test_s_type(self):
        """Encode SD x5, 16(x2)."""
        inst = encode_s_type(Opcode.STORE, funct3=Funct3Store.SD,
                             rs1=2, rs2=5, imm12=16)
        assert decode_opcode(inst) == Opcode.STORE
        assert decode_funct3(inst) == Funct3Store.SD
        assert decode_rs1(inst) == 2
        assert decode_rs2(inst) == 5
        assert decode_s_imm(inst) == 16

    def test_s_type_negative(self):
        """Encode SD with negative offset."""
        inst = encode_s_type(Opcode.STORE, funct3=Funct3Store.SD,
                             rs1=2, rs2=5, imm12=(-16) & 0xFFF)
        assert decode_s_imm(inst) == -16

    def test_b_type_positive(self):
        """Encode BEQ x1, x2, +8."""
        inst = encode_b_type(Opcode.BRANCH, funct3=Funct3Branch.BEQ,
                             rs1=1, rs2=2, imm13=8)
        assert decode_opcode(inst) == Opcode.BRANCH
        assert decode_funct3(inst) == Funct3Branch.BEQ
        assert decode_rs1(inst) == 1
        assert decode_rs2(inst) == 2
        assert decode_b_imm(inst) == 8

    def test_b_type_negative(self):
        """Encode BNE x3, x4, -12."""
        inst = encode_b_type(Opcode.BRANCH, funct3=Funct3Branch.BNE,
                             rs1=3, rs2=4, imm13=(-12) & 0x1FFF)
        assert decode_b_imm(inst) == -12

    def test_u_type_lui(self):
        """Encode LUI x5, 0x12345."""
        inst = encode_u_type(Opcode.LUI, rd=5, imm20=0x12345)
        assert decode_opcode(inst) == Opcode.LUI
        assert decode_rd(inst) == 5
        assert decode_u_imm(inst) == 0x12345000

    def test_j_type_positive(self):
        """Encode JAL x0, +100."""
        inst = encode_j_type(Opcode.JAL, rd=0, imm21=100)
        assert decode_opcode(inst) == Opcode.JAL
        assert decode_rd(inst) == 0
        assert decode_j_imm(inst) == 100

    def test_j_type_negative(self):
        """Encode JAL x1, -20."""
        inst = encode_j_type(Opcode.JAL, rd=1, imm21=(-20) & 0x1FFFFF)
        assert decode_j_imm(inst) == -20

    def test_all_registers(self):
        """Verify all 32 registers can be encoded/decoded in rd/rs1/rs2."""
        for r in range(32):
            inst = encode_r_type(Opcode.OP64, rd=r, funct3=0, rs1=r, rs2=r, funct7=0)
            assert decode_rd(inst) == r
            assert decode_rs1(inst) == r
            assert decode_rs2(inst) == r


# ---------------------------------------------------------------------------
# Compiler tests
# ---------------------------------------------------------------------------


class TestCompiler:
    """Test bytecode compilation from LLVM IR."""

    def test_compile_add_function(self, ctx):
        """Compile a simple add function to bytecode."""
        with ctx.create_module("test") as mod:
            func = make_add_function(ctx, mod)
            bytecode, host_fns, _global_refs = compile_function(func)
            assert isinstance(bytecode, bytes)
            assert len(bytecode) > 0
            # Should be a multiple of 4 (32-bit instructions)
            assert len(bytecode) % 4 == 0
            assert host_fns == []  # no calls

    def test_compile_arith_function(self, ctx):
        """Compile arithmetic function with multiple ops."""
        with ctx.create_module("test") as mod:
            func = make_arith_function(ctx, mod)
            bytecode, host_fns, _global_refs = compile_function(func)
            assert isinstance(bytecode, bytes)
            assert len(bytecode) > 0
            assert len(bytecode) % 4 == 0
            assert host_fns == []

    def test_compile_function_with_call(self, ctx):
        """Compile a function that calls another function."""
        with ctx.create_module("test") as mod:
            i32 = ctx.types.i32
            ptr = ctx.types.ptr
            # Declare an external function (e.g. strlen)
            ext_ty = ctx.types.function(i32, [ptr])
            ext_fn = mod.add_function("strlen", ext_ty)
            # Create caller
            fn_ty = ctx.types.function(i32, [ptr])
            func = mod.add_function("test_caller", fn_ty)
            entry = func.append_basic_block("entry")
            with entry.create_builder() as b:
                result = b.call(ext_fn, [func.get_param(0)], "len")
                b.ret(result)
            bytecode, host_fns, _global_refs = compile_function(func)
            assert len(bytecode) > 0
            assert host_fns == ["strlen"]

    def test_compile_rejects_float(self, ctx):
        """Compiler rejects functions with float operations."""
        with ctx.create_module("test") as mod:
            f32 = ctx.types.f32
            fn_ty = ctx.types.function(f32, [f32, f32])
            func = mod.add_function("fadd", fn_ty)
            entry = func.append_basic_block("entry")
            with entry.create_builder() as b:
                result = b.fadd(func.get_param(0), func.get_param(1), "r")
                b.ret(result)
            with pytest.raises(ValueError, match="float|Float"):
                compile_function(func)


# ---------------------------------------------------------------------------
# Virtualization pass tests
# ---------------------------------------------------------------------------


class TestVirtualizationPass:
    """Test the full virtualization pass."""

    def test_pass_info(self):
        """VirtualizationPass has correct metadata."""
        info = VirtualizationPass.info()
        assert info.name == "virtualization"
        assert info.is_module_pass is True

    def test_virtualize_add_function(self, ctx, rng):
        """Apply virtualization to add function, verify IR is valid."""
        with ctx.create_module("test") as mod:
            func = make_add_function(ctx, mod)
            vpass = VirtualizationPass(rng=rng)
            changed = vpass.run_on_module(mod, ctx)
            assert changed is True
            # Verify the IR is well-formed
            assert mod.verify()

    def test_virtualize_arith_function(self, ctx, rng):
        """Apply virtualization to arith function, verify IR is valid."""
        with ctx.create_module("test") as mod:
            func = make_arith_function(ctx, mod)
            vpass = VirtualizationPass(rng=rng)
            changed = vpass.run_on_module(mod, ctx)
            assert changed is True
            assert mod.verify()

    def test_virtualize_preserves_signature(self, ctx, rng):
        """Function signature is preserved after virtualization."""
        with ctx.create_module("test") as mod:
            func = make_add_function(ctx, mod)
            original_name = func.name
            fn_ty = func.function_type
            original_ret = fn_ty.return_type.kind
            original_param_count = fn_ty.param_count

            vpass = VirtualizationPass(rng=rng)
            vpass.run_on_module(mod, ctx)

            # Find the function again
            virtualized = None
            for f in mod.functions:
                if f.name == original_name:
                    virtualized = f
                    break
            assert virtualized is not None
            vfn_ty = virtualized.function_type
            assert vfn_ty.return_type.kind == original_ret
            assert vfn_ty.param_count == original_param_count

    def test_skips_ineligible_float(self, ctx, rng):
        """Functions with float params are skipped."""
        with ctx.create_module("test") as mod:
            f32 = ctx.types.f32
            fn_ty = ctx.types.function(f32, [f32, f32])
            func = mod.add_function("fadd", fn_ty)
            entry = func.append_basic_block("entry")
            with entry.create_builder() as b:
                result = b.fadd(func.get_param(0), func.get_param(1), "r")
                b.ret(result)

            vpass = VirtualizationPass(rng=rng)
            changed = vpass.run_on_module(mod, ctx)
            assert changed is False

    def test_skips_declarations(self, ctx, rng):
        """Declarations (no body) are skipped."""
        with ctx.create_module("test") as mod:
            i32 = ctx.types.i32
            fn_ty = ctx.types.function(i32, [i32])
            mod.add_function("external_func", fn_ty)

            vpass = VirtualizationPass(rng=rng)
            changed = vpass.run_on_module(mod, ctx)
            assert changed is False

    def test_interpreter_built_once(self, ctx, rng):
        """Interpreter function is built once even with multiple eligible functions."""
        with ctx.create_module("test") as mod:
            make_add_function(ctx, mod)
            make_arith_function(ctx, mod)

            vpass = VirtualizationPass(rng=rng)
            vpass.run_on_module(mod, ctx)

            # Count __vm_interpret functions
            interp_count = sum(1 for f in mod.functions if f.name == "__vm_interpret")
            assert interp_count == 1
            assert mod.verify()

    def test_virtualize_function_with_call(self, ctx, rng):
        """Apply virtualization to a function containing calls, verify IR is valid."""
        with ctx.create_module("test") as mod:
            i32 = ctx.types.i32
            ptr = ctx.types.ptr
            # Declare external function
            ext_ty = ctx.types.function(i32, [ptr])
            mod.add_function("strlen", ext_ty)
            # Create a function that calls it
            fn_ty = ctx.types.function(i32, [ptr])
            func = mod.add_function("check", fn_ty)
            entry = func.append_basic_block("entry")
            with entry.create_builder() as b:
                result = b.call(mod.get_function("strlen"), [func.get_param(0)], "len")
                b.ret(result)
            vpass = VirtualizationPass(rng=rng)
            changed = vpass.run_on_module(mod, ctx)
            assert changed is True
            assert mod.verify()
            # Verify strlen is still in the module (used as host function)
            found_strlen = any(f.name == "strlen" for f in mod.functions)
            assert found_strlen

    def test_bytecode_globals_created(self, ctx, rng):
        """Bytecode is embedded as global constants."""
        with ctx.create_module("test") as mod:
            make_add_function(ctx, mod)
            vpass = VirtualizationPass(rng=rng)
            vpass.run_on_module(mod, ctx)

            # Check for bytecode global
            found = False
            for gv in mod.globals:
                if gv.name.startswith("__vm_bytecode_"):
                    found = True
                    break
            assert found

"""Tests for the Bogus Control Flow pass (Polaris modular arithmetic)."""

import llvm
import pytest

from conftest import make_branch_function
from shifting_codes.passes.bogus_control_flow import BogusControlFlowPass
from shifting_codes.utils.crypto import CryptoRandom


def test_bcf_adds_bogus_blocks(ctx, rng):
    """BCF should split non-entry blocks, clone body, and add bogus branches."""
    with ctx.create_module("test") as mod:
        make_branch_function(ctx, mod)
        func = mod.get_function("branch_func")

        blocks_before = len(list(func.basic_blocks))

        p = BogusControlFlowPass(rng=rng)
        changed = p.run_on_function(func, ctx)

        # branch_func has multiple blocks; non-entry blocks get processed
        assert changed

        blocks_after = len(list(func.basic_blocks))
        assert blocks_after > blocks_before

        assert mod.verify(), mod.get_verification_error()

        ir = mod.to_string()
        # Polaris: modular arithmetic allocas, not __bcf_x_ globals
        assert "bcf.var" in ir
        assert "bcf.clone" in ir


def test_bcf_skips_single_block_entry(ctx, rng):
    """BCF skips entry block, so single-block functions are unchanged."""
    i32 = ctx.types.i32
    with ctx.create_module("test") as mod:
        fn_ty = ctx.types.function(i32, [i32, i32])
        func = mod.add_function("add", fn_ty)
        entry = func.append_basic_block("entry")
        with entry.create_builder() as builder:
            result = builder.add(func.get_param(0), func.get_param(1), "r")
            builder.ret(result)

        p = BogusControlFlowPass(rng=rng)
        changed = p.run_on_function(func, ctx)

        # Single-block function: entry is skipped, nothing to process
        assert not changed


def test_bcf_modular_predicate(ctx, rng):
    """BCF should use i64 modular arithmetic predicates, not i32 globals."""
    with ctx.create_module("test") as mod:
        make_branch_function(ctx, mod)
        func = mod.get_function("branch_func")

        p = BogusControlFlowPass(rng=rng)
        p.run_on_function(func, ctx)

        assert mod.verify(), mod.get_verification_error()

        ir = mod.to_string()
        # Should have i64 allocas and icmp on i64 (not i32 global x/y)
        assert "alloca i64" in ir
        assert "icmp eq i64" in ir or "icmp ne i64" in ir
        # Should NOT have old Pluto-style globals
        assert "__bcf_x_" not in ir
        assert "__bcf_y_" not in ir


def test_bcf_creates_valid_opaque_predicate(ctx, rng):
    """The opaque predicate should produce valid IR."""
    i32 = ctx.types.i32
    with ctx.create_module("test") as mod:
        fn_ty = ctx.types.function(i32, [i32])
        func = mod.add_function("f", fn_ty)

        entry = func.append_basic_block("entry")
        second = func.append_basic_block("second")
        exit_bb = func.append_basic_block("exit")

        with entry.create_builder() as builder:
            builder.br(second)

            builder.position_at_end(second)
            val = builder.add(func.get_param(0), i32.constant(1), "v")
            builder.br(exit_bb)

            builder.position_at_end(exit_bb)
            builder.ret(val)

        p = BogusControlFlowPass(rng=rng)
        p.run_on_function(func, ctx)

        assert mod.verify(), mod.get_verification_error()


def test_bcf_deterministic(ctx):
    """Same seed should produce same BCF output."""
    results = []
    for _ in range(2):
        with ctx.create_module("test") as mod:
            make_branch_function(ctx, mod)
            p = BogusControlFlowPass(rng=CryptoRandom(seed=77))
            for func in mod.functions:
                if not func.is_declaration:
                    p.run_on_function(func, ctx)
            results.append(mod.to_string())

    assert results[0] == results[1]

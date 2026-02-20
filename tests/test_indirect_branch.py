"""Tests for the Indirect Branch pass."""

import llvm
import pytest

from conftest import make_branch_function, make_loop_function
from shifting_codes.passes.indirect_branch import IndirectBranchPass
from shifting_codes.utils.crypto import CryptoRandom


def test_indirect_branch_diamond(ctx, rng):
    """Conditional branches in diamond pattern should become indirect."""
    with ctx.create_module("test") as mod:
        make_branch_function(ctx, mod)
        original_ir = mod.to_string()

        p = IndirectBranchPass(rng=rng)
        changed = p.run_on_function(mod.get_function("branch_func"), ctx)

        assert changed
        assert mod.verify(), mod.get_verification_error()

        new_ir = mod.to_string()
        assert new_ir != original_ir
        assert "indirectbr" in new_ir
        assert "sibr.table" in new_ir


def test_indirect_branch_loop(ctx, rng):
    """Loop back-edge branches should become indirect."""
    with ctx.create_module("test") as mod:
        make_loop_function(ctx, mod)

        p = IndirectBranchPass(rng=rng)
        changed = p.run_on_function(mod.get_function("sum_to_n"), ctx)

        assert changed
        assert mod.verify(), mod.get_verification_error()
        assert "indirectbr" in mod.to_string()


def test_indirect_branch_single_block_noop(ctx, rng):
    """Function with one block should not be modified."""
    i32 = ctx.types.i32
    with ctx.create_module("test") as mod:
        fn_ty = ctx.types.function(i32, [i32])
        func = mod.add_function("single", fn_ty)
        entry = func.append_basic_block("entry")
        with entry.create_builder() as builder:
            builder.ret(func.get_param(0))

        p = IndirectBranchPass(rng=rng)
        changed = p.run_on_function(func, ctx)
        assert not changed


def test_indirect_branch_unconditional(ctx, rng):
    """Unconditional branches should also become indirect."""
    i32 = ctx.types.i32
    with ctx.create_module("test") as mod:
        fn_ty = ctx.types.function(i32, [i32])
        func = mod.add_function("uncond", fn_ty)
        entry = func.append_basic_block("entry")
        body = func.append_basic_block("body")

        with entry.create_builder() as builder:
            builder.br(body)
            builder.position_at_end(body)
            builder.ret(func.get_param(0))

        p = IndirectBranchPass(rng=rng)
        changed = p.run_on_function(func, ctx)

        assert changed
        assert mod.verify(), mod.get_verification_error()
        assert "indirectbr" in mod.to_string()


def test_indirect_branch_deterministic(ctx):
    """Same seed should produce same output."""
    results = []
    for _ in range(2):
        with ctx.create_module("test") as mod:
            make_branch_function(ctx, mod)
            p = IndirectBranchPass(rng=CryptoRandom(seed=99))
            p.run_on_function(mod.get_function("branch_func"), ctx)
            results.append(mod.to_string())

    assert results[0] == results[1]

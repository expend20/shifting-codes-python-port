"""Tests for the Control Flow Flattening pass."""

import llvm
import pytest

from conftest import make_branch_function, make_loop_function
from shifting_codes.passes.flattening import FlatteningPass
from shifting_codes.utils.crypto import CryptoRandom


def test_flattening_diamond(ctx, rng):
    """Flattening should transform a diamond-pattern function."""
    with ctx.create_module("test") as mod:
        make_branch_function(ctx, mod)
        func = mod.get_function("branch_func")

        original_ir = mod.to_string()

        p = FlatteningPass(rng=rng)
        changed = p.run_on_function(func, ctx)

        assert changed
        assert mod.verify(), mod.get_verification_error()

        new_ir = mod.to_string()
        assert new_ir != original_ir
        # Should contain dispatch infrastructure
        assert "cff.dispatch" in new_ir or "cff.state" in new_ir


def test_flattening_loop(ctx, rng):
    """Flattening should transform a loop-containing function."""
    with ctx.create_module("test") as mod:
        make_loop_function(ctx, mod)
        func = mod.get_function("sum_to_n")

        p = FlatteningPass(rng=rng)
        changed = p.run_on_function(func, ctx)

        assert changed
        assert mod.verify(), mod.get_verification_error()


def test_flattening_single_block_noop(ctx, rng):
    """Flattening should not transform a single-block function."""
    i32 = ctx.types.i32
    with ctx.create_module("test") as mod:
        fn_ty = ctx.types.function(i32, [i32])
        func = mod.add_function("f", fn_ty)
        entry = func.append_basic_block("entry")
        with entry.create_builder() as builder:
            builder.ret(func.get_param(0))

        p = FlatteningPass(rng=rng)
        changed = p.run_on_function(func, ctx)

        assert not changed
        assert mod.verify()


def test_flattening_deterministic(ctx):
    """Same seed should produce same flattened output."""
    results = []
    for _ in range(2):
        with ctx.create_module("test") as mod:
            make_branch_function(ctx, mod)
            p = FlatteningPass(rng=CryptoRandom(seed=55))
            for func in mod.functions:
                if not func.is_declaration:
                    p.run_on_function(func, ctx)
            results.append(mod.to_string())

    assert results[0] == results[1]

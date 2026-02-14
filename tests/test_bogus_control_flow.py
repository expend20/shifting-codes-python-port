"""Tests for the Bogus Control Flow pass."""

import llvm
import pytest

from conftest import make_add_function, make_branch_function
from shifting_codes.passes.bogus_control_flow import BogusControlFlowPass
from shifting_codes.utils.crypto import CryptoRandom


def test_bcf_adds_bogus_blocks(ctx, rng):
    """BCF should add new basic blocks with opaque predicates."""
    with ctx.create_module("test") as mod:
        make_add_function(ctx, mod)
        func = mod.get_function("add")

        blocks_before = len(list(func.basic_blocks))

        p = BogusControlFlowPass(rng=rng)
        changed = p.run_on_function(func, ctx)

        # add function has only one block with a ret, no unconditional br
        # so BCF should NOT transform it
        assert not changed

        assert mod.verify(), mod.get_verification_error()


def test_bcf_transforms_unconditional_branches(ctx, rng):
    """BCF should transform blocks ending with unconditional branches."""
    with ctx.create_module("test") as mod:
        make_branch_function(ctx, mod)
        func = mod.get_function("branch_func")

        original_ir = mod.to_string()
        blocks_before = len(list(func.basic_blocks))

        p = BogusControlFlowPass(rng=rng)
        changed = p.run_on_function(func, ctx)

        assert changed
        assert mod.verify(), mod.get_verification_error()

        blocks_after = len(list(func.basic_blocks))
        assert blocks_after > blocks_before

        new_ir = mod.to_string()
        # Should contain bcf global variables
        assert "__bcf_x_" in new_ir or "__bcf_y_" in new_ir


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

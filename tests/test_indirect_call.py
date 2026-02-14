"""Tests for the Indirect Call pass."""

import llvm
import pytest

from shifting_codes.passes.indirect_call import IndirectCallPass
from shifting_codes.utils.crypto import CryptoRandom


def _populate_module_with_internal_calls(ctx, mod):
    """Populate a module with internal functions that call each other."""
    i32 = ctx.types.i32

    # Internal helper function
    helper_ty = ctx.types.function(i32, [i32])
    helper = mod.add_function("helper", helper_ty)
    helper.linkage = llvm.Linkage.Internal
    entry = helper.append_basic_block("entry")
    with entry.create_builder() as builder:
        result = builder.add(helper.get_param(0), i32.constant(1), "r")
        builder.ret(result)

    # Another internal function
    helper2_ty = ctx.types.function(i32, [i32, i32])
    helper2 = mod.add_function("helper2", helper2_ty)
    helper2.linkage = llvm.Linkage.Internal
    entry2 = helper2.append_basic_block("entry")
    with entry2.create_builder() as builder:
        result = builder.mul(helper2.get_param(0), helper2.get_param(1), "r")
        builder.ret(result)

    # Main function that calls both helpers
    main_ty = ctx.types.function(i32, [i32])
    main_func = mod.add_function("main_func", main_ty)
    main_entry = main_func.append_basic_block("entry")
    with main_entry.create_builder() as builder:
        call1 = builder.call(helper, [main_func.get_param(0)], "c1")
        call2 = builder.call(helper2, [call1, i32.constant(3)], "c2")
        builder.ret(call2)


def test_indirect_call_replaces_direct_calls(ctx, rng):
    """Indirect call pass should make calls indirect."""
    with ctx.create_module("test") as mod:
        _populate_module_with_internal_calls(ctx, mod)
        original_ir = mod.to_string()

        p = IndirectCallPass(rng=rng)
        changed = p.run_on_module(mod, ctx)

        assert changed
        assert mod.verify(), mod.get_verification_error()

        new_ir = mod.to_string()
        assert new_ir != original_ir
        # Should contain indirect call globals
        assert ".indcall." in new_ir


def test_indirect_call_no_internals(ctx, rng):
    """If there are no internal functions, pass should be no-op."""
    i32 = ctx.types.i32
    with ctx.create_module("test") as mod:
        fn_ty = ctx.types.function(i32, [i32])
        func = mod.add_function("f", fn_ty)
        entry = func.append_basic_block("entry")
        with entry.create_builder() as builder:
            builder.ret(func.get_param(0))

        p = IndirectCallPass(rng=rng)
        changed = p.run_on_module(mod, ctx)
        assert not changed


def test_indirect_call_deterministic(ctx):
    """Same seed should produce same output."""
    results = []
    for _ in range(2):
        with ctx.create_module("test") as mod:
            _populate_module_with_internal_calls(ctx, mod)
            p = IndirectCallPass(rng=CryptoRandom(seed=66))
            p.run_on_module(mod, ctx)
            results.append(mod.to_string())

    assert results[0] == results[1]

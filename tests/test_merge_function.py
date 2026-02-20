"""Tests for the Merge Function pass."""

import llvm
import pytest

from shifting_codes.passes.merge_function import MergeFunctionPass
from shifting_codes.utils.crypto import CryptoRandom


def _create_two_void_internal_functions(ctx, mod):
    """Create two void internal functions and a main that calls both."""
    i32 = ctx.types.i32
    void_ty = ctx.types.void

    gv = mod.add_global(i32, "result_a")
    gv.initializer = i32.constant(0)
    gv.linkage = llvm.Linkage.Internal

    fn_a_ty = ctx.types.function(void_ty, [i32])
    fn_a = mod.add_function("helper_a", fn_a_ty)
    fn_a.linkage = llvm.Linkage.Internal
    entry_a = fn_a.append_basic_block("entry")
    with entry_a.create_builder() as b:
        val = b.add(fn_a.get_param(0), i32.constant(1), "inc")
        b.store(val, gv)
        b.ret_void()

    gv2 = mod.add_global(i32, "result_b")
    gv2.initializer = i32.constant(0)
    gv2.linkage = llvm.Linkage.Internal

    fn_b_ty = ctx.types.function(void_ty, [i32, i32])
    fn_b = mod.add_function("helper_b", fn_b_ty)
    fn_b.linkage = llvm.Linkage.Internal
    entry_b = fn_b.append_basic_block("entry")
    with entry_b.create_builder() as b:
        val = b.mul(fn_b.get_param(0), fn_b.get_param(1), "prod")
        b.store(val, gv2)
        b.ret_void()

    fn_main_ty = ctx.types.function(void_ty, [i32])
    fn_main = mod.add_function("main_func", fn_main_ty)
    entry = fn_main.append_basic_block("entry")
    with entry.create_builder() as b:
        b.call(fn_a, [fn_main.get_param(0)], "")
        b.call(fn_b, [fn_main.get_param(0), i32.constant(5)], "")
        b.ret_void()


def _create_two_nonvoid_external_functions(ctx, mod):
    """Create two external int-returning functions and a caller."""
    i32 = ctx.types.i32
    ptr_ty = ctx.types.ptr

    fn_a_ty = ctx.types.function(i32, [ptr_ty])
    fn_a = mod.add_function("check_serial", fn_a_ty)
    entry = fn_a.append_basic_block("entry")
    with entry.create_builder() as b:
        b.ret(i32.constant(1))

    fn_b_ty = ctx.types.function(i32, [ptr_ty])
    fn_b = mod.add_function("derive_tier", fn_b_ty)
    entry = fn_b.append_basic_block("entry")
    with entry.create_builder() as b:
        b.ret(i32.constant(2))


def test_merge_function_two_void(ctx, rng):
    """Two void internal functions should be merged into a switch dispatcher."""
    with ctx.create_module("test") as mod:
        _create_two_void_internal_functions(ctx, mod)
        original_ir = mod.to_string()

        p = MergeFunctionPass(rng=rng)
        changed = p.run_on_module(mod, ctx)

        assert changed
        assert mod.verify(), mod.get_verification_error()

        new_ir = mod.to_string()
        assert new_ir != original_ir
        assert "__merged_function" in new_ir
        assert "switch" in new_ir


def test_merge_function_call_sites_replaced(ctx, rng):
    """Call sites should be replaced with calls to merged function."""
    with ctx.create_module("test") as mod:
        _create_two_void_internal_functions(ctx, mod)

        p = MergeFunctionPass(rng=rng)
        p.run_on_module(mod, ctx)

        ir = mod.to_string()
        assert mod.verify(), mod.get_verification_error()
        assert "call void @__merged_function" in ir


def test_merge_function_nonvoid_external(ctx, rng):
    """External non-void functions should be wrapped and merged."""
    with ctx.create_module("test") as mod:
        _create_two_nonvoid_external_functions(ctx, mod)
        original_ir = mod.to_string()

        p = MergeFunctionPass(rng=rng)
        changed = p.run_on_module(mod, ctx)

        assert changed
        assert mod.verify(), mod.get_verification_error()

        ir = mod.to_string()
        assert ir != original_ir
        # Original function names preserved as stubs
        assert "define i32 @check_serial" in ir
        assert "define i32 @derive_tier" in ir
        # Stubs use return-value-through-pointer pattern
        assert "retval" in ir
        assert "retload" in ir
        # Merged dispatcher exists
        assert "__merged_function" in ir
        assert "switch" in ir


def test_merge_function_single_noop(ctx, rng):
    """If there's only one function, no merge should happen."""
    i32 = ctx.types.i32
    with ctx.create_module("test") as mod:
        fn_ty = ctx.types.function(i32, [i32])
        func = mod.add_function("only_one", fn_ty)
        entry = func.append_basic_block("entry")
        with entry.create_builder() as b:
            b.ret(func.get_param(0))

        p = MergeFunctionPass(rng=rng)
        changed = p.run_on_module(mod, ctx)
        assert not changed


def test_merge_function_deterministic(ctx):
    """Same seed should produce same output."""
    results = []
    for _ in range(2):
        with ctx.create_module("test") as mod:
            _create_two_void_internal_functions(ctx, mod)
            p = MergeFunctionPass(rng=CryptoRandom(seed=77))
            p.run_on_module(mod, ctx)
            results.append(mod.to_string())

    assert results[0] == results[1]

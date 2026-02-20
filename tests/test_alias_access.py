"""Tests for the Alias Access pass."""

import llvm
import pytest

from shifting_codes.passes.alias_access import AliasAccessPass
from shifting_codes.utils.crypto import CryptoRandom


def _make_function_with_allocas(ctx, mod):
    """Create a function with multiple stack allocas and uses."""
    i32 = ctx.types.i32
    fn_ty = ctx.types.function(i32, [i32, i32])
    func = mod.add_function("alloca_func", fn_ty)
    entry = func.append_basic_block("entry")
    with entry.create_builder() as b:
        x = b.alloca(i32, name="x")
        y = b.alloca(i32, name="y")
        b.store(func.get_param(0), x)
        b.store(func.get_param(1), y)
        vx = b.load(i32, x, "vx")
        vy = b.load(i32, y, "vy")
        result = b.add(vx, vy, "result")
        b.ret(result)
    return func


def test_alias_access_transforms_allocas(ctx, rng):
    """Allocas should be replaced with struct-based indirection."""
    with ctx.create_module("test") as mod:
        func = _make_function_with_allocas(ctx, mod)
        original_ir = mod.to_string()

        p = AliasAccessPass(rng=rng)
        changed = p.run_on_function(func, ctx)

        assert changed
        assert mod.verify(), mod.get_verification_error()

        new_ir = mod.to_string()
        assert new_ir != original_ir
        # Should contain struct allocas
        assert "aa.raw" in new_ir or "aa.trans" in new_ir


def test_alias_access_no_allocas_noop(ctx, rng):
    """Function with no allocas should not be modified."""
    i32 = ctx.types.i32
    with ctx.create_module("test") as mod:
        fn_ty = ctx.types.function(i32, [i32])
        func = mod.add_function("no_alloca", fn_ty)
        entry = func.append_basic_block("entry")
        with entry.create_builder() as b:
            result = b.add(func.get_param(0), i32.constant(1), "r")
            b.ret(result)

        p = AliasAccessPass(rng=rng)
        changed = p.run_on_function(func, ctx)
        assert not changed


def test_alias_access_getter_functions_created(ctx, rng):
    """Getter functions should be created in the module."""
    with ctx.create_module("test") as mod:
        func = _make_function_with_allocas(ctx, mod)

        p = AliasAccessPass(rng=rng)
        p.run_on_function(func, ctx)

        ir = mod.to_string()
        assert mod.verify(), mod.get_verification_error()
        assert "__obfu_aa_getter_" in ir


def test_alias_access_struct_gep_in_ir(ctx, rng):
    """Transformed IR should contain GEP instructions for struct access."""
    with ctx.create_module("test") as mod:
        func = _make_function_with_allocas(ctx, mod)

        p = AliasAccessPass(rng=rng)
        p.run_on_function(func, ctx)

        ir = mod.to_string()
        assert mod.verify(), mod.get_verification_error()
        assert "getelementptr" in ir


def test_alias_access_deterministic(ctx):
    """Same seed should produce same output."""
    results = []
    for _ in range(2):
        with ctx.create_module("test") as mod:
            _make_function_with_allocas(ctx, mod)
            p = AliasAccessPass(rng=CryptoRandom(seed=55))
            func = mod.get_function("alloca_func")
            p.run_on_function(func, ctx)
            results.append(mod.to_string())

    assert results[0] == results[1]

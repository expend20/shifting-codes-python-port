"""Tests for the Substitution pass."""

import llvm
import pytest

from conftest import make_add_function, make_arith_function
from shifting_codes.passes.substitution import SubstitutionPass
from shifting_codes.utils.crypto import CryptoRandom


def test_substitution_modifies_add(ctx, rng):
    """Substitution pass should transform add instructions."""
    with ctx.create_module("test") as mod:
        make_add_function(ctx, mod)
        original_ir = mod.to_string()

        p = SubstitutionPass(rng=rng)
        changed = False
        for func in mod.functions:
            if not func.is_declaration:
                changed |= p.run_on_function(func, ctx)

        assert changed
        assert mod.verify(), mod.get_verification_error()

        new_ir = mod.to_string()
        assert new_ir != original_ir


def test_substitution_all_ops(ctx, rng):
    """Substitution pass should transform add, sub, and, or, xor."""
    with ctx.create_module("test") as mod:
        make_arith_function(ctx, mod)
        original_ir = mod.to_string()

        p = SubstitutionPass(rng=rng)
        for func in mod.functions:
            if not func.is_declaration:
                p.run_on_function(func, ctx)

        assert mod.verify(), mod.get_verification_error()

        new_ir = mod.to_string()
        assert new_ir != original_ir


def test_substitution_deterministic(ctx):
    """Same seed should produce same output."""
    results = []
    for _ in range(2):
        with ctx.create_module("test") as mod:
            make_arith_function(ctx, mod)
            p = SubstitutionPass(rng=CryptoRandom(seed=123))
            for func in mod.functions:
                if not func.is_declaration:
                    p.run_on_function(func, ctx)
            results.append(mod.to_string())

    assert results[0] == results[1]


def test_substitution_each_pattern(ctx):
    """Test each individual substitution variant produces valid IR."""
    i32 = ctx.types.i32
    opcodes_and_names = [
        (lambda b, a, x: b.add(a, x, "r"), "add"),
        (lambda b, a, x: b.sub(a, x, "r"), "sub"),
        (lambda b, a, x: b.and_(a, x, "r"), "and"),
        (lambda b, a, x: b.or_(a, x, "r"), "or"),
        (lambda b, a, x: b.xor(a, x, "r"), "xor"),
    ]

    for builder_fn, name in opcodes_and_names:
        # Run the pass 10 times to exercise different random variants
        for seed in range(10):
            with ctx.create_module(f"test_{name}_{seed}") as mod:
                fn_ty = ctx.types.function(i32, [i32, i32])
                func = mod.add_function("f", fn_ty)
                entry = func.append_basic_block("entry")
                with entry.create_builder() as builder:
                    result = builder_fn(builder, func.get_param(0), func.get_param(1))
                    builder.ret(result)

                p = SubstitutionPass(rng=CryptoRandom(seed=seed))
                p.run_on_function(func, ctx)
                assert mod.verify(), f"{name} seed={seed}: {mod.get_verification_error()}"

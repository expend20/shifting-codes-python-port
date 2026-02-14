"""Tests for the MBA Obfuscation pass."""

import llvm
import pytest

from conftest import make_add_function, make_arith_function
from shifting_codes.passes.mba_obfuscation import MBAObfuscationPass
from shifting_codes.utils.crypto import CryptoRandom
from shifting_codes.utils.mba import generate_linear_mba, clear_cache


def test_mba_coefficients_satisfy_constraints():
    """Generated coefficients must satisfy truth table constraints."""
    from shifting_codes.utils.mba import TRUTH_TABLES

    clear_cache()
    rng = CryptoRandom(seed=42)
    coeffs = generate_linear_mba(5, rng)

    # For each of the 4 input combinations, the linear combination must be 0
    for j in range(4):
        total = sum(coeffs[i] * TRUTH_TABLES[i][j] for i in range(15))
        assert total == 0, f"Constraint failed for input combo {j}: sum={total}"


def test_mba_nonzero_coefficients():
    """At least one coefficient should be non-zero."""
    clear_cache()
    rng = CryptoRandom(seed=42)
    coeffs = generate_linear_mba(5, rng)
    assert any(c != 0 for c in coeffs)


def test_mba_pass_transforms_binary_ops(ctx, rng):
    """MBA pass should transform binary operations."""
    clear_cache()
    with ctx.create_module("test") as mod:
        make_add_function(ctx, mod)
        original_ir = mod.to_string()

        p = MBAObfuscationPass(rng=rng)
        for func in mod.functions:
            if not func.is_declaration:
                p.run_on_function(func, ctx)

        assert mod.verify(), mod.get_verification_error()
        new_ir = mod.to_string()
        assert new_ir != original_ir


def test_mba_pass_all_ops(ctx, rng):
    """MBA pass should handle all 5 binary op types."""
    clear_cache()
    with ctx.create_module("test") as mod:
        make_arith_function(ctx, mod)
        original_ir = mod.to_string()

        p = MBAObfuscationPass(rng=rng)
        for func in mod.functions:
            if not func.is_declaration:
                p.run_on_function(func, ctx)

        assert mod.verify(), mod.get_verification_error()
        new_ir = mod.to_string()
        assert new_ir != original_ir


def test_mba_deterministic(ctx):
    """Same seed should produce same MBA output."""
    clear_cache()
    results = []
    for _ in range(2):
        clear_cache()
        with ctx.create_module("test") as mod:
            make_add_function(ctx, mod)
            p = MBAObfuscationPass(rng=CryptoRandom(seed=99))
            for func in mod.functions:
                if not func.is_declaration:
                    p.run_on_function(func, ctx)
            results.append(mod.to_string())

    assert results[0] == results[1]

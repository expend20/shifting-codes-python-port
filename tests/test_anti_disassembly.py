"""Tests for the Anti-Disassembly pass."""

import llvm
import pytest

from shifting_codes.passes.anti_disassembly import AntiDisassemblyPass
from shifting_codes.utils.crypto import CryptoRandom
from conftest import make_add_function, make_arith_function, make_branch_function


def _set_x86_triple(mod):
    """Set an x86_64 target triple on the module."""
    mod.target_triple = "x86_64-pc-linux-gnu"


def test_anti_disassembly_basic(ctx, rng):
    """Should inject inline asm into a function with x86 triple."""
    with ctx.create_module("test") as mod:
        _set_x86_triple(mod)
        func = make_add_function(ctx, mod)
        original_ir = mod.to_string()

        p = AntiDisassemblyPass(rng=rng)
        changed = p.run_on_function(func, ctx)

        assert changed
        assert mod.verify(), mod.get_verification_error()

        new_ir = mod.to_string()
        assert new_ir != original_ir
        # Should contain inline asm with the anti-disasm pattern
        assert ".byte 0x48, 0xB8" in new_ir


def test_anti_disassembly_non_x86_noop(ctx, rng):
    """Setting a non-x86 triple should result in no modifications."""
    with ctx.create_module("test") as mod:
        mod.target_triple = "aarch64-linux-gnu"
        func = make_add_function(ctx, mod)

        p = AntiDisassemblyPass(rng=rng)
        changed = p.run_on_function(func, ctx)

        assert not changed


def test_anti_disassembly_empty_triple_noop(ctx, rng):
    """Empty target triple should result in no modifications."""
    with ctx.create_module("test") as mod:
        func = make_add_function(ctx, mod)

        p = AntiDisassemblyPass(rng=rng)
        changed = p.run_on_function(func, ctx)

        assert not changed


def test_anti_disassembly_phi_skip(ctx, rng):
    """Inline asm should not be inserted before PHI nodes."""
    with ctx.create_module("test") as mod:
        _set_x86_triple(mod)
        func = make_branch_function(ctx, mod)

        p = AntiDisassemblyPass(rng=rng, density=1.0)
        changed = p.run_on_function(func, ctx)

        assert changed
        assert mod.verify(), mod.get_verification_error()

        # The merge block has a PHI — asm should come after it
        ir = mod.to_string()
        assert ".byte 0x48, 0xB8" in ir


def test_anti_disassembly_density_zero(ctx, rng):
    """density=0 should only inject at block starts, not before other instructions."""
    with ctx.create_module("test") as mod:
        _set_x86_triple(mod)
        func = make_add_function(ctx, mod)

        p = AntiDisassemblyPass(rng=rng, density=0.0)
        changed = p.run_on_function(func, ctx)

        assert changed
        assert mod.verify(), mod.get_verification_error()

        ir = mod.to_string()
        # Should have exactly 1 inline asm call (1 basic block, 1 at start)
        assert ir.count(".byte 0x48, 0xB8") == 1


def test_anti_disassembly_density_one(ctx, rng):
    """density=1 should inject before every eligible instruction."""
    with ctx.create_module("test") as mod:
        _set_x86_triple(mod)
        # arith function has 1 block with: add, sub, xor, or, and, ret
        # 5 non-terminator instructions, 1 terminator (ret)
        func = make_arith_function(ctx, mod)

        p = AntiDisassemblyPass(rng=rng, density=1.0)
        changed = p.run_on_function(func, ctx)

        assert changed
        assert mod.verify(), mod.get_verification_error()

        ir = mod.to_string()
        # Block start injection = 1 (before first non-PHI: add)
        # Remaining non-PHI, non-terminator: sub, xor, or, and = 4
        # Total = 5
        assert ir.count(".byte 0x48, 0xB8") == 5


def test_anti_disassembly_deterministic():
    """Same seed should produce same random bytes in asm."""
    results = []
    for _ in range(2):
        with llvm.create_context() as ctx:
            with ctx.create_module("test") as mod:
                mod.target_triple = "x86_64-pc-linux-gnu"
                func = make_add_function(ctx, mod)

                p = AntiDisassemblyPass(
                    rng=CryptoRandom(seed=88), density=1.0)
                p.run_on_function(func, ctx)
                results.append(mod.to_string())

    assert results[0] == results[1]


def test_anti_disassembly_i386_triple(ctx, rng):
    """i386 triple should also be recognized as x86."""
    with ctx.create_module("test") as mod:
        mod.target_triple = "i386-pc-linux-gnu"
        func = make_add_function(ctx, mod)

        p = AntiDisassemblyPass(rng=rng)
        changed = p.run_on_function(func, ctx)

        assert changed
        assert mod.verify(), mod.get_verification_error()

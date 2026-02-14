"""Tests for the Global Encryption pass."""

import llvm
import pytest

from shifting_codes.passes.global_encryption import GlobalEncryptionPass
from shifting_codes.utils.crypto import CryptoRandom


def test_global_encryption_encrypts_internals(ctx, rng):
    """Should encrypt internal integer globals."""
    i32 = ctx.types.i32
    with ctx.create_module("test") as mod:
        gv = mod.add_global(i32, "secret_value")
        gv.initializer = i32.constant(42)
        gv.linkage = llvm.Linkage.Internal

        fn_ty = ctx.types.function(i32, [])
        func = mod.add_function("user_func", fn_ty)
        entry = func.append_basic_block("entry")
        with entry.create_builder() as builder:
            val = builder.load(i32, gv, "v")
            builder.ret(val)

        original_ir = mod.to_string()

        p = GlobalEncryptionPass(rng=rng)
        changed = p.run_on_module(mod, ctx)

        assert changed
        assert mod.verify(), mod.get_verification_error()

        new_ir = mod.to_string()
        assert new_ir != original_ir
        # Should contain XOR decryption
        assert "ge.dec" in new_ir


def test_global_encryption_skips_external(ctx, rng):
    """External globals should not be encrypted."""
    i32 = ctx.types.i32
    with ctx.create_module("test") as mod:
        gv = mod.add_global(i32, "ext_var")
        gv.initializer = i32.constant(99)
        gv.linkage = llvm.Linkage.External

        fn_ty = ctx.types.function(i32, [])
        func = mod.add_function("f", fn_ty)
        entry = func.append_basic_block("entry")
        with entry.create_builder() as builder:
            builder.ret(i32.constant(0))

        p = GlobalEncryptionPass(rng=rng)
        changed = p.run_on_module(mod, ctx)

        # No internal globals to encrypt
        assert not changed


def test_global_encryption_deterministic():
    """Same seed should produce same encrypted output."""
    results = []
    for _ in range(2):
        with llvm.create_context() as ctx:
            i32 = ctx.types.i32
            with ctx.create_module("test") as mod:
                gv = mod.add_global(i32, "secret_value")
                gv.initializer = i32.constant(42)
                gv.linkage = llvm.Linkage.Internal

                fn_ty = ctx.types.function(i32, [])
                func = mod.add_function("user_func", fn_ty)
                entry = func.append_basic_block("entry")
                with entry.create_builder() as builder:
                    val = builder.load(i32, gv, "v")
                    builder.ret(val)

                p = GlobalEncryptionPass(rng=CryptoRandom(seed=88))
                p.run_on_module(mod, ctx)
                results.append(mod.to_string())

    assert results[0] == results[1]

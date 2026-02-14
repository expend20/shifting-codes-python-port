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


def test_global_encryption_encrypts_array(ctx, rng):
    """Should encrypt internal integer-array globals and insert GEP decrypt."""
    i32 = ctx.types.i32
    with ctx.create_module("test") as mod:
        arr_ty = ctx.types.array(i32, 4)
        gv = mod.add_global(arr_ty, "secret_arr")
        elems = [i32.constant(10), i32.constant(20), i32.constant(30), i32.constant(40)]
        gv.initializer = llvm.const_array(i32, elems)
        gv.linkage = llvm.Linkage.Internal

        fn_ty = ctx.types.function(i32, [])
        func = mod.add_function("user_func", fn_ty)
        entry = func.append_basic_block("entry")
        i64 = ctx.types.i64
        with entry.create_builder() as builder:
            ptr = builder.gep(arr_ty, gv, [i64.constant(0), i64.constant(0)], "p")
            val = builder.load(i32, ptr, "v")
            builder.ret(val)

        p = GlobalEncryptionPass(rng=rng)
        changed = p.run_on_module(mod, ctx)

        assert changed
        assert mod.verify(), mod.get_verification_error()

        ir = mod.to_string()
        # Should contain XOR-based array decryption
        # (GEP names may be constant-folded away by LLVM)
        assert "ge.arr.dec" in ir


def test_global_encryption_encrypts_linkonce_odr_strings(ctx, rng):
    """Should encrypt linkonce_odr string globals (Windows/MSVC string pooling)."""
    i8 = ctx.types.i8
    i32 = ctx.types.i32
    with ctx.create_module("test") as mod:
        # Simulate a clang/MSVC-style string constant: [N x i8] with linkonce_odr
        text = b"Serial accepted\x00"
        arr_ty = ctx.types.array(i8, len(text))
        gv = mod.add_global(arr_ty, "str_secret")
        gv.initializer = llvm.const_array(i8, [i8.constant(b) for b in text])
        gv.linkage = llvm.Linkage.LinkOnceODR

        fn_ty = ctx.types.function(i32, [])
        func = mod.add_function("main", fn_ty)
        entry = func.append_basic_block("entry")
        with entry.create_builder() as builder:
            builder.ret(i32.constant(0))

        p = GlobalEncryptionPass(rng=rng)
        changed = p.run_on_module(mod, ctx)

        assert changed
        assert mod.verify(), mod.get_verification_error()

        ir = mod.to_string()
        # Original plaintext must be gone from the initializer
        assert "Serial accepted" not in ir
        # Decryption XOR loop must be present
        assert "ge.arr.dec" in ir
        # Linkage must be demoted to internal (can't linker-merge encrypted data)
        for g in mod.globals:
            if g.name == "str_secret":
                assert g.linkage == llvm.Linkage.Internal
                break


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

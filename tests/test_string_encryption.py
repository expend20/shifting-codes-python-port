"""Tests for the String Encryption pass."""

import llvm
import pytest

from shifting_codes.passes.string_encryption import StringEncryptionPass
from shifting_codes.utils.crypto import CryptoRandom


def _make_string_module(ctx, mod, text=b"Hello, World!\x00"):
    """Create a module with a string global and a function that uses it."""
    i8 = ctx.types.i8
    i32 = ctx.types.i32
    ptr = ctx.types.ptr

    arr_ty = ctx.types.array(i8, len(text))
    gv = mod.add_global(arr_ty, "my_string")
    gv.initializer = llvm.const_array(i8, [i8.constant(b) for b in text])
    gv.linkage = llvm.Linkage.Internal
    gv.set_constant(True)

    fn_ty = ctx.types.function(ptr, [])
    func = mod.add_function("get_string", fn_ty)
    entry = func.append_basic_block("entry")
    with entry.create_builder() as builder:
        p = builder.gep(i8, gv, [i32.constant(0)], "sptr")
        builder.ret(p)

    return gv, func


def test_string_encryption_basic(ctx, rng):
    """Should encrypt an internal string global."""
    with ctx.create_module("test") as mod:
        gv, func = _make_string_module(ctx, mod)
        original_ir = mod.to_string()

        p = StringEncryptionPass(rng=rng)
        changed = p.run_on_module(mod, ctx)

        assert changed
        assert mod.verify(), mod.get_verification_error()

        new_ir = mod.to_string()
        assert new_ir != original_ir
        # Original plaintext must be gone
        assert "Hello, World!" not in new_ir
        # Per-use stack copy + decrypt must be present
        assert "se.copy" in new_ir


def test_string_encryption_multiple_strings(ctx, rng):
    """All string globals should get encrypted with different keys."""
    i8 = ctx.types.i8
    i32 = ctx.types.i32
    ptr = ctx.types.ptr

    with ctx.create_module("test") as mod:
        texts = [b"secret_one\x00", b"secret_two\x00"]
        gvs = []
        for idx, text in enumerate(texts):
            arr_ty = ctx.types.array(i8, len(text))
            gv = mod.add_global(arr_ty, f"str_{idx}")
            gv.initializer = llvm.const_array(i8, [i8.constant(b) for b in text])
            gv.linkage = llvm.Linkage.Internal
            gv.set_constant(True)
            gvs.append(gv)

        # Each global is used by a separate function to ensure real uses
        fn_ty = ctx.types.function(ptr, [])
        func1 = mod.add_function("get_str1", fn_ty)
        entry1 = func1.append_basic_block("entry")
        with entry1.create_builder() as builder:
            val = builder.load(i8, gvs[0], "v")
            builder.ret(gvs[0])

        func2 = mod.add_function("get_str2", fn_ty)
        entry2 = func2.append_basic_block("entry")
        with entry2.create_builder() as builder:
            val = builder.load(i8, gvs[1], "v")
            builder.ret(gvs[1])

        p = StringEncryptionPass(rng=rng)
        changed = p.run_on_module(mod, ctx)

        assert changed
        assert mod.verify(), mod.get_verification_error()

        ir = mod.to_string()
        assert "secret_one" not in ir
        assert "secret_two" not in ir


def test_string_encryption_skips_integer_globals(ctx, rng):
    """Integer globals should not be encrypted by StringEncryptionPass."""
    i8 = ctx.types.i8
    i32 = ctx.types.i32
    ptr = ctx.types.ptr

    with ctx.create_module("test") as mod:
        # Add an integer global (not a string)
        gv_int = mod.add_global(i32, "int_val")
        gv_int.initializer = i32.constant(42)
        gv_int.linkage = llvm.Linkage.Internal
        gv_int.set_constant(True)

        fn_ty = ctx.types.function(i32, [])
        func = mod.add_function("f", fn_ty)
        entry = func.append_basic_block("entry")
        with entry.create_builder() as builder:
            val = builder.load(i32, gv_int, "v")
            builder.ret(val)

        p = StringEncryptionPass(rng=rng)
        changed = p.run_on_module(mod, ctx)

        # No string globals to encrypt
        assert not changed


def test_string_encryption_skips_external(ctx, rng):
    """External globals should not be encrypted."""
    i8 = ctx.types.i8
    i32 = ctx.types.i32
    ptr = ctx.types.ptr

    with ctx.create_module("test") as mod:
        text = b"external\x00"
        arr_ty = ctx.types.array(i8, len(text))
        gv = mod.add_global(arr_ty, "ext_str")
        gv.initializer = llvm.const_array(i8, [i8.constant(b) for b in text])
        gv.linkage = llvm.Linkage.External
        gv.set_constant(True)

        fn_ty = ctx.types.function(ptr, [])
        func = mod.add_function("f", fn_ty)
        entry = func.append_basic_block("entry")
        with entry.create_builder() as builder:
            p = builder.gep(i8, gv, [i32.constant(0)], "p")
            builder.ret(p)

        p = StringEncryptionPass(rng=rng)
        changed = p.run_on_module(mod, ctx)

        assert not changed


def test_string_encryption_deterministic():
    """Same seed should produce same encrypted output."""
    results = []
    for _ in range(2):
        with llvm.create_context() as ctx:
            i8 = ctx.types.i8
            i32 = ctx.types.i32
            ptr = ctx.types.ptr
            with ctx.create_module("test") as mod:
                text = b"deterministic\x00"
                arr_ty = ctx.types.array(i8, len(text))
                gv = mod.add_global(arr_ty, "det_str")
                gv.initializer = llvm.const_array(i8,
                    [i8.constant(b) for b in text])
                gv.linkage = llvm.Linkage.Internal
                gv.set_constant(True)

                fn_ty = ctx.types.function(ptr, [])
                func = mod.add_function("f", fn_ty)
                entry = func.append_basic_block("entry")
                with entry.create_builder() as builder:
                    p = builder.gep(i8, gv, [i32.constant(0)], "p")
                    builder.ret(p)

                p = StringEncryptionPass(rng=CryptoRandom(seed=88))
                p.run_on_module(mod, ctx)
                results.append(mod.to_string())

    assert results[0] == results[1]


def test_string_encryption_per_function_decryption(ctx, rng):
    """Each function using the string should get its own stack copy + decrypt."""
    i8 = ctx.types.i8
    i32 = ctx.types.i32
    ptr = ctx.types.ptr

    with ctx.create_module("test") as mod:
        text = b"shared\x00"
        arr_ty = ctx.types.array(i8, len(text))
        gv = mod.add_global(arr_ty, "shared_str")
        gv.initializer = llvm.const_array(i8, [i8.constant(b) for b in text])
        gv.linkage = llvm.Linkage.Internal
        gv.set_constant(True)

        # First function using the string
        fn_ty = ctx.types.function(ptr, [])
        func1 = mod.add_function("use1", fn_ty)
        entry1 = func1.append_basic_block("entry")
        with entry1.create_builder() as builder:
            p1 = builder.gep(i8, gv, [i32.constant(0)], "p1")
            builder.ret(p1)

        # Second function using the same string
        func2 = mod.add_function("use2", fn_ty)
        entry2 = func2.append_basic_block("entry")
        with entry2.create_builder() as builder:
            p2 = builder.gep(i8, gv, [i32.constant(0)], "p2")
            builder.ret(p2)

        p = StringEncryptionPass(rng=rng)
        changed = p.run_on_module(mod, ctx)

        assert changed
        assert mod.verify(), mod.get_verification_error()

        ir = mod.to_string()
        # Should have per-function decryption — one se.copy per function
        assert ir.count("se.copy") >= 2


def test_string_encryption_linkonce_odr(ctx, rng):
    """LinkOnceODR strings should be encrypted and demoted to Internal."""
    i8 = ctx.types.i8
    i32 = ctx.types.i32
    ptr = ctx.types.ptr

    with ctx.create_module("test") as mod:
        text = b"linkonce string\x00"
        arr_ty = ctx.types.array(i8, len(text))
        gv = mod.add_global(arr_ty, "lo_str")
        gv.initializer = llvm.const_array(i8, [i8.constant(b) for b in text])
        gv.linkage = llvm.Linkage.LinkOnceODR
        gv.set_constant(True)

        fn_ty = ctx.types.function(ptr, [])
        func = mod.add_function("f", fn_ty)
        entry = func.append_basic_block("entry")
        with entry.create_builder() as builder:
            p = builder.gep(i8, gv, [i32.constant(0)], "p")
            builder.ret(p)

        p = StringEncryptionPass(rng=rng)
        changed = p.run_on_module(mod, ctx)

        assert changed
        assert mod.verify(), mod.get_verification_error()

        # Linkage should be demoted
        for g in mod.globals:
            if g.name == "lo_str":
                assert g.linkage == llvm.Linkage.Internal
                break

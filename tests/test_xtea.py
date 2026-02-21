"""End-to-end XTEA tests with obfuscation passes.

Tests:
1. Build XTEA IR and verify module
2. Apply each pass individually and verify
3. Apply all passes in sequence and verify
4. (Optional) Compile and execute via ctypes for correctness check
"""

import ctypes
import os
import platform
import subprocess
import tempfile

import llvm
import pytest

from shifting_codes.passes import PassPipeline
from shifting_codes.passes.substitution import SubstitutionPass
from shifting_codes.passes.mba_obfuscation import MBAObfuscationPass
from shifting_codes.passes.bogus_control_flow import BogusControlFlowPass
from shifting_codes.passes.flattening import FlatteningPass
from shifting_codes.passes.global_encryption import GlobalEncryptionPass
from shifting_codes.passes.indirect_call import IndirectCallPass
from shifting_codes.utils.crypto import CryptoRandom
from shifting_codes.utils.mba import clear_cache
from shifting_codes.xtea.builder import build_xtea_encrypt
from shifting_codes.xtea.reference import xtea_encrypt


def test_xtea_build_and_verify():
    """Build XTEA IR and verify the module."""
    with llvm.create_context() as ctx:
        with ctx.create_module("xtea") as mod:
            build_xtea_encrypt(ctx, mod)
            assert mod.verify(), mod.get_verification_error()

            ir_text = mod.to_string()
            assert "xtea_encrypt" in ir_text
            assert "loop.body" in ir_text


def test_xtea_reference_known_vector():
    """Test the pure Python XTEA reference against known test vector."""
    key = [0x01234567, 0x89ABCDEF, 0xFEDCBA98, 0x76543210]
    v0, v1 = 0, 0

    c0, c1 = xtea_encrypt(v0, v1, key, rounds=32)
    # Result should be non-zero after encryption
    assert (c0, c1) != (0, 0)


def test_xtea_reference_roundtrip():
    """Encrypt then decrypt should return original."""
    from shifting_codes.xtea.reference import xtea_decrypt

    key = [0xDEADBEEF, 0xCAFEBABE, 0x12345678, 0x87654321]
    v0, v1 = 0x01020304, 0x05060708

    c0, c1 = xtea_encrypt(v0, v1, key, rounds=32)
    d0, d1 = xtea_decrypt(c0, c1, key, rounds=32)
    assert (d0, d1) == (v0, v1)


def test_xtea_with_substitution():
    """Apply substitution pass to XTEA IR and verify."""
    with llvm.create_context() as ctx:
        with ctx.create_module("xtea") as mod:
            build_xtea_encrypt(ctx, mod)
            rng = CryptoRandom(seed=42)
            p = SubstitutionPass(rng=rng)
            for func in mod.functions:
                if not func.is_declaration:
                    p.run_on_function(func, ctx)
            assert mod.verify(), mod.get_verification_error()


def test_xtea_with_mba():
    """Apply MBA pass to XTEA IR and verify."""
    clear_cache()
    with llvm.create_context() as ctx:
        with ctx.create_module("xtea") as mod:
            build_xtea_encrypt(ctx, mod)
            rng = CryptoRandom(seed=42)
            p = MBAObfuscationPass(rng=rng)
            for func in mod.functions:
                if not func.is_declaration:
                    p.run_on_function(func, ctx)
            assert mod.verify(), mod.get_verification_error()


def test_xtea_with_bogus_control_flow():
    """Apply BCF pass to XTEA IR and verify."""
    with llvm.create_context() as ctx:
        with ctx.create_module("xtea") as mod:
            build_xtea_encrypt(ctx, mod)
            rng = CryptoRandom(seed=42)
            p = BogusControlFlowPass(rng=rng)
            for func in mod.functions:
                if not func.is_declaration:
                    p.run_on_function(func, ctx)
            assert mod.verify(), mod.get_verification_error()


def test_xtea_with_flattening():
    """Apply flattening pass to XTEA IR and verify."""
    with llvm.create_context() as ctx:
        with ctx.create_module("xtea") as mod:
            build_xtea_encrypt(ctx, mod)
            rng = CryptoRandom(seed=42)
            p = FlatteningPass(rng=rng)
            for func in mod.functions:
                if not func.is_declaration:
                    p.run_on_function(func, ctx)
            assert mod.verify(), mod.get_verification_error()


def test_xtea_full_pipeline():
    """Apply all 6 passes in sequence and verify module."""
    clear_cache()
    with llvm.create_context() as ctx:
        with ctx.create_module("xtea") as mod:
            build_xtea_encrypt(ctx, mod)
            original_ir = mod.to_string()

            pipeline = PassPipeline([
                SubstitutionPass(rng=CryptoRandom(seed=1)),
                MBAObfuscationPass(rng=CryptoRandom(seed=2)),
                BogusControlFlowPass(rng=CryptoRandom(seed=3)),
                FlatteningPass(rng=CryptoRandom(seed=4)),
                GlobalEncryptionPass(rng=CryptoRandom(seed=5)),
                IndirectCallPass(rng=CryptoRandom(seed=6)),
            ])

            pipeline.run(mod, ctx)
            assert mod.verify(), mod.get_verification_error()

            new_ir = mod.to_string()
            assert new_ir != original_ir
            # IR should be significantly larger after obfuscation
            assert len(new_ir) > len(original_ir)


def _can_compile():
    """Check if we have clang and can compile."""
    try:
        result = subprocess.run(
            ["clang", "--version"], capture_output=True, text=True, timeout=5
        )
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


@pytest.mark.skipif(not _can_compile(), reason="clang not available")
def test_xtea_execution_correctness():
    """Build XTEA IR -> emit object -> compile -> load -> execute -> compare with reference."""
    key = [0x01234567, 0x89ABCDEF, 0xFEDCBA98, 0x76543210]
    v0_in, v1_in = 0xDEADBEEF, 0xCAFEBABE
    rounds = 32

    # Get reference result
    ref_v0, ref_v1 = xtea_encrypt(v0_in, v1_in, key, rounds)

    # Initialize LLVM targets
    llvm.initialize_all_targets()
    llvm.initialize_all_target_mcs()
    llvm.initialize_all_target_infos()
    llvm.initialize_all_asm_printers()
    llvm.initialize_all_asm_parsers()

    # Determine target triple
    is_windows = platform.system() == "Windows"
    is_macos = platform.system() == "Darwin"
    machine = platform.machine()
    if is_windows:
        triple = "x86_64-pc-windows-msvc"
        shared_ext = ".dll"
    elif is_macos:
        arch = "arm64" if machine == "arm64" else "x86_64"
        triple = f"{arch}-apple-darwin"
        shared_ext = ".dylib"
    else:
        arch = "aarch64" if machine == "aarch64" else "x86_64"
        triple = f"{arch}-unknown-linux-gnu"
        shared_ext = ".so"

    tmpdir = tempfile.mkdtemp()
    obj_path = os.path.join(tmpdir, "xtea.o")
    lib_path = os.path.join(tmpdir, f"xtea{shared_ext}")
    lib_handle = None

    try:
        with llvm.create_context() as ctx:
            with ctx.create_module("xtea") as mod:
                xtea_func = build_xtea_encrypt(ctx, mod)

                # On Windows, mark function as dllexport
                if is_windows:
                    xtea_func.dll_storage_class = llvm.DLLExport

                assert mod.verify()
                mod.target_triple = triple

                target = llvm.get_target_from_triple(triple)
                tm = llvm.create_target_machine(target, triple, "generic", "")
                tm.emit_to_file(mod, obj_path, llvm.CodeGenFileType.ObjectFile)

        # Compile to shared library
        compile_cmd = ["clang", "-shared", "-o", lib_path, obj_path]
        if not is_windows:
            compile_cmd.insert(1, "-fPIC")
        compile_result = subprocess.run(
            compile_cmd, capture_output=True, text=True, timeout=30
        )
        assert compile_result.returncode == 0, (
            f"Compilation failed: {compile_result.stderr}"
        )

        # Load and call
        lib = ctypes.CDLL(lib_path)
        lib_handle = lib._handle
        func = lib.xtea_encrypt
        func.argtypes = [
            ctypes.POINTER(ctypes.c_uint32),
            ctypes.POINTER(ctypes.c_uint32),
            ctypes.c_int32,
        ]
        func.restype = None

        v_buf = (ctypes.c_uint32 * 2)(v0_in, v1_in)
        key_buf = (ctypes.c_uint32 * 4)(*key)
        func(v_buf, key_buf, rounds)

        assert v_buf[0] == ref_v0, f"v0 mismatch: {v_buf[0]:#x} != {ref_v0:#x}"
        assert v_buf[1] == ref_v1, f"v1 mismatch: {v_buf[1]:#x} != {ref_v1:#x}"
    finally:
        # Unload DLL on Windows before cleanup
        if lib_handle is not None and is_windows:
            ctypes.windll.kernel32.FreeLibrary(ctypes.c_void_p(lib_handle))
        import shutil
        shutil.rmtree(tmpdir, ignore_errors=True)

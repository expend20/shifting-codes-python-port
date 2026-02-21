"""Virtualization pass execution tests.

Tests that virtualized functions produce correct results when compiled
and executed via ctypes. Covers:
- Basic VM execution correctness
- Obfuscation applied before virtualization (bytecode growth)
- Obfuscation applied after virtualization (interpreter growth)
- Combined before + after obfuscation
"""

import ctypes
import os
import platform
import shutil
import subprocess
import tempfile

import llvm
import pytest

from conftest import make_add_function, make_branch_function

from shifting_codes.passes.virtualization import VirtualizationPass
from shifting_codes.passes.substitution import SubstitutionPass
from shifting_codes.passes.flattening import FlatteningPass
from shifting_codes.passes.bogus_control_flow import BogusControlFlowPass
from shifting_codes.passes.mba_obfuscation import MBAObfuscationPass
from shifting_codes.utils.crypto import CryptoRandom
from shifting_codes.utils.mba import clear_cache


# ---------------------------------------------------------------------------
# Platform setup
# ---------------------------------------------------------------------------

_IS_WINDOWS = platform.system() == "Windows"
_IS_MACOS = platform.system() == "Darwin"
_MACHINE = platform.machine()

if _IS_WINDOWS:
    _TRIPLE = "x86_64-pc-windows-msvc"
    _SHARED_EXT = ".dll"
elif _IS_MACOS:
    _arch = "arm64" if _MACHINE == "arm64" else "x86_64"
    _TRIPLE = f"{_arch}-apple-darwin"
    _SHARED_EXT = ".dylib"
else:
    _arch = "aarch64" if _MACHINE == "aarch64" else "x86_64"
    _TRIPLE = f"{_arch}-unknown-linux-gnu"
    _SHARED_EXT = ".so"


def _can_compile():
    """Check if clang is available for compilation."""
    try:
        result = subprocess.run(
            ["clang", "--version"], capture_output=True, text=True, timeout=5
        )
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


_HAS_CLANG = _can_compile()

# Initialize LLVM targets once at module load
llvm.initialize_all_targets()
llvm.initialize_all_target_mcs()
llvm.initialize_all_target_infos()
llvm.initialize_all_asm_printers()
llvm.initialize_all_asm_parsers()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _emit_object(mod, func_name, label):
    """Emit module to object file. Returns (tmpdir, obj_path).

    Must be called while the module is still alive (inside its context manager).
    Sets target triple and dllexport as needed.
    """
    if _IS_WINDOWS:
        for f in mod.functions:
            if f.name == func_name:
                f.dll_storage_class = llvm.DLLExport
                break

    mod.target_triple = _TRIPLE
    target = llvm.get_target_from_triple(_TRIPLE)
    reloc = llvm.RelocMode.PIC if not _IS_WINDOWS else llvm.RelocMode.Default
    tm = llvm.create_target_machine(target, _TRIPLE, "generic", "",
                                    reloc_mode=reloc)

    tmpdir = tempfile.mkdtemp(prefix=f"vm_exec_{label}_")
    obj_path = os.path.join(tmpdir, f"{label}.o")
    tm.emit_to_file(mod, obj_path, llvm.CodeGenFileType.ObjectFile)
    return tmpdir, obj_path


def _link_and_run(tmpdir, obj_path, func_name, argtypes, restype, args, label):
    """Link object file into shared library, load, call, return result."""
    lib_path = os.path.join(tmpdir, f"{label}{_SHARED_EXT}")
    lib_handle = None

    try:
        compile_cmd = ["clang", "-shared", "-O0", "-o", lib_path, obj_path]
        if not _IS_WINDOWS:
            compile_cmd.insert(1, "-fPIC")
        result = subprocess.run(
            compile_cmd, capture_output=True, text=True, timeout=60
        )
        assert result.returncode == 0, f"Compilation failed: {result.stderr}"

        lib = ctypes.CDLL(lib_path)
        lib_handle = lib._handle
        func = getattr(lib, func_name)
        func.argtypes = argtypes
        func.restype = restype

        return func(*args)
    finally:
        if lib_handle is not None and _IS_WINDOWS:
            ctypes.windll.kernel32.FreeLibrary(ctypes.c_void_p(lib_handle))
        shutil.rmtree(tmpdir, ignore_errors=True)


def _get_bytecode_size(mod, func_name):
    """Find __vm_bytecode_<func_name> global and return its array length."""
    target_name = f"__vm_bytecode_{func_name}"
    for gv in mod.globals:
        if gv.name == target_name:
            return gv.global_value_type.array_length
    return 0


def _count_function_instructions(mod, func_name):
    """Count total instructions in a function."""
    for f in mod.functions:
        if f.name == func_name:
            count = 0
            for bb in f.basic_blocks:
                for _ in bb.instructions:
                    count += 1
            return count
    return 0


# ---------------------------------------------------------------------------
# Test class 1: Basic VM execution correctness
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not _HAS_CLANG, reason="clang not available")
class TestVMExecution:
    """Test that virtualized functions produce correct results."""

    def test_add_execution(self):
        with llvm.create_context() as ctx:
            with ctx.create_module("test_vm") as mod:
                make_add_function(ctx, mod)
                rng = CryptoRandom(seed=42)
                VirtualizationPass(rng=rng).run_on_module(mod, ctx)
                assert mod.verify(), mod.get_verification_error()
                tmpdir, obj_path = _emit_object(mod, "add", "add")

        result = _link_and_run(
            tmpdir, obj_path, "add",
            [ctypes.c_int32, ctypes.c_int32], ctypes.c_int32,
            (3, 5), "add"
        )
        assert result == 8

    def test_add_negative(self):
        with llvm.create_context() as ctx:
            with ctx.create_module("test_vm") as mod:
                make_add_function(ctx, mod)
                rng = CryptoRandom(seed=42)
                VirtualizationPass(rng=rng).run_on_module(mod, ctx)
                assert mod.verify(), mod.get_verification_error()
                tmpdir, obj_path = _emit_object(mod, "add", "add_neg")

        result = _link_and_run(
            tmpdir, obj_path, "add",
            [ctypes.c_int32, ctypes.c_int32], ctypes.c_int32,
            (-1, 1), "add_neg"
        )
        assert result == 0

    def test_branch_true_path(self):
        with llvm.create_context() as ctx:
            with ctx.create_module("test_vm") as mod:
                make_branch_function(ctx, mod)
                rng = CryptoRandom(seed=42)
                VirtualizationPass(rng=rng).run_on_module(mod, ctx)
                assert mod.verify(), mod.get_verification_error()
                tmpdir, obj_path = _emit_object(mod, "branch_func", "branch_true")

        result = _link_and_run(
            tmpdir, obj_path, "branch_func",
            [ctypes.c_int32, ctypes.c_int32], ctypes.c_int32,
            (7, 3), "branch_true"
        )
        assert result == 10

    def test_branch_false_path(self):
        with llvm.create_context() as ctx:
            with ctx.create_module("test_vm") as mod:
                make_branch_function(ctx, mod)
                rng = CryptoRandom(seed=42)
                VirtualizationPass(rng=rng).run_on_module(mod, ctx)
                assert mod.verify(), mod.get_verification_error()
                tmpdir, obj_path = _emit_object(mod, "branch_func", "branch_false")

        # a=2, b=5: 2 > 5 is false, so result = a - b = 2 - 5 = -3
        result = _link_and_run(
            tmpdir, obj_path, "branch_func",
            [ctypes.c_int32, ctypes.c_int32], ctypes.c_int32,
            (2, 5), "branch_false"
        )
        assert result == ctypes.c_int32(-3).value


# ---------------------------------------------------------------------------
# Test class 2: Obfuscation before VM (bytecode growth)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not _HAS_CLANG, reason="clang not available")
class TestObfuscationBeforeVM:
    """Apply obfuscation passes to functions before virtualization.

    The obfuscated IR is more complex, producing larger bytecode.
    Results must still be correct.
    """

    def test_substitution_before_vm(self):
        with llvm.create_context() as ctx:
            # Get baseline bytecode size
            with ctx.create_module("baseline") as bmod:
                make_add_function(ctx, bmod)
                VirtualizationPass(rng=CryptoRandom(seed=99)).run_on_module(bmod, ctx)
                baseline = _get_bytecode_size(bmod, "add")

            with ctx.create_module("test_vm") as mod:
                make_add_function(ctx, mod)
                rng = CryptoRandom(seed=42)
                for f in mod.functions:
                    if not f.is_declaration:
                        SubstitutionPass(rng=rng).run_on_function(f, ctx)
                assert mod.verify(), mod.get_verification_error()
                VirtualizationPass(rng=rng).run_on_module(mod, ctx)
                assert mod.verify(), mod.get_verification_error()

                new_size = _get_bytecode_size(mod, "add")
                assert new_size > baseline, (
                    f"Bytecode should grow: {new_size} <= {baseline}"
                )
                tmpdir, obj_path = _emit_object(mod, "add", "sub_before_vm")

        result = _link_and_run(
            tmpdir, obj_path, "add",
            [ctypes.c_int32, ctypes.c_int32], ctypes.c_int32,
            (3, 5), "sub_before_vm"
        )
        assert result == 8

    def test_mba_before_vm(self):
        clear_cache()
        with llvm.create_context() as ctx:
            with ctx.create_module("baseline") as bmod:
                make_add_function(ctx, bmod)
                VirtualizationPass(rng=CryptoRandom(seed=99)).run_on_module(bmod, ctx)
                baseline = _get_bytecode_size(bmod, "add")

            with ctx.create_module("test_vm") as mod:
                make_add_function(ctx, mod)
                rng = CryptoRandom(seed=42)
                for f in mod.functions:
                    if not f.is_declaration:
                        MBAObfuscationPass(rng=rng).run_on_function(f, ctx)
                assert mod.verify(), mod.get_verification_error()
                VirtualizationPass(rng=rng).run_on_module(mod, ctx)
                assert mod.verify(), mod.get_verification_error()

                new_size = _get_bytecode_size(mod, "add")
                assert new_size > baseline, (
                    f"Bytecode should grow: {new_size} <= {baseline}"
                )
                tmpdir, obj_path = _emit_object(mod, "add", "mba_before_vm")

        result = _link_and_run(
            tmpdir, obj_path, "add",
            [ctypes.c_int32, ctypes.c_int32], ctypes.c_int32,
            (3, 5), "mba_before_vm"
        )
        assert result == 8

    def test_bcf_before_vm(self):
        with llvm.create_context() as ctx:
            with ctx.create_module("baseline") as bmod:
                make_add_function(ctx, bmod)
                VirtualizationPass(rng=CryptoRandom(seed=99)).run_on_module(bmod, ctx)
                baseline = _get_bytecode_size(bmod, "add")

            with ctx.create_module("test_vm") as mod:
                make_add_function(ctx, mod)
                rng = CryptoRandom(seed=42)
                for f in mod.functions:
                    if not f.is_declaration:
                        BogusControlFlowPass(rng=rng).run_on_function(f, ctx)
                assert mod.verify(), mod.get_verification_error()
                VirtualizationPass(rng=rng).run_on_module(mod, ctx)
                assert mod.verify(), mod.get_verification_error()

                new_size = _get_bytecode_size(mod, "add")
                assert new_size > baseline, (
                    f"Bytecode should grow: {new_size} <= {baseline}"
                )
                tmpdir, obj_path = _emit_object(mod, "add", "bcf_before_vm")

        result = _link_and_run(
            tmpdir, obj_path, "add",
            [ctypes.c_int32, ctypes.c_int32], ctypes.c_int32,
            (3, 5), "bcf_before_vm"
        )
        assert result == 8

    def test_flattening_before_vm(self):
        # Use branch_func (4 blocks) since flattening skips single-block functions
        with llvm.create_context() as ctx:
            with ctx.create_module("baseline") as bmod:
                make_branch_function(ctx, bmod)
                VirtualizationPass(rng=CryptoRandom(seed=99)).run_on_module(bmod, ctx)
                baseline = _get_bytecode_size(bmod, "branch_func")

            with ctx.create_module("test_vm") as mod:
                make_branch_function(ctx, mod)
                rng = CryptoRandom(seed=42)
                for f in mod.functions:
                    if not f.is_declaration:
                        FlatteningPass(rng=rng).run_on_function(f, ctx)
                assert mod.verify(), mod.get_verification_error()
                VirtualizationPass(rng=rng).run_on_module(mod, ctx)
                assert mod.verify(), mod.get_verification_error()

                new_size = _get_bytecode_size(mod, "branch_func")
                assert new_size > baseline, (
                    f"Bytecode should grow: {new_size} <= {baseline}"
                )
                tmpdir, obj_path = _emit_object(mod, "branch_func", "flat_before_vm")

        result = _link_and_run(
            tmpdir, obj_path, "branch_func",
            [ctypes.c_int32, ctypes.c_int32], ctypes.c_int32,
            (7, 3), "flat_before_vm"
        )
        assert result == 10


# ---------------------------------------------------------------------------
# Test class 3: Obfuscation after VM (interpreter growth)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not _HAS_CLANG, reason="clang not available")
class TestObfuscationAfterVM:
    """Apply obfuscation passes after virtualization.

    The passes transform __vm_interpret, making it larger but functionally
    equivalent. Results must still be correct.
    """

    def test_substitution_after_vm(self):
        with llvm.create_context() as ctx:
            # Get baseline interpreter size
            with ctx.create_module("baseline") as bmod:
                make_add_function(ctx, bmod)
                VirtualizationPass(rng=CryptoRandom(seed=99)).run_on_module(bmod, ctx)
                baseline = _count_function_instructions(bmod, "__vm_interpret")

            with ctx.create_module("test_vm") as mod:
                make_add_function(ctx, mod)
                rng = CryptoRandom(seed=42)
                VirtualizationPass(rng=rng).run_on_module(mod, ctx)
                assert mod.verify(), mod.get_verification_error()

                for f in mod.functions:
                    if f.name == "__vm_interpret":
                        SubstitutionPass(rng=rng).run_on_function(f, ctx)
                        break
                assert mod.verify(), mod.get_verification_error()

                new_count = _count_function_instructions(mod, "__vm_interpret")
                assert new_count > baseline, (
                    f"Interpreter should grow: {new_count} <= {baseline}"
                )
                tmpdir, obj_path = _emit_object(mod, "add", "sub_after_vm")

        result = _link_and_run(
            tmpdir, obj_path, "add",
            [ctypes.c_int32, ctypes.c_int32], ctypes.c_int32,
            (3, 5), "sub_after_vm"
        )
        assert result == 8

    def test_flattening_after_vm(self):
        with llvm.create_context() as ctx:
            with ctx.create_module("baseline") as bmod:
                make_add_function(ctx, bmod)
                VirtualizationPass(rng=CryptoRandom(seed=99)).run_on_module(bmod, ctx)
                baseline = _count_function_instructions(bmod, "__vm_interpret")

            with ctx.create_module("test_vm") as mod:
                make_add_function(ctx, mod)
                rng = CryptoRandom(seed=42)
                VirtualizationPass(rng=rng).run_on_module(mod, ctx)
                assert mod.verify(), mod.get_verification_error()

                for f in mod.functions:
                    if f.name == "__vm_interpret":
                        FlatteningPass(rng=rng).run_on_function(f, ctx)
                        break
                assert mod.verify(), mod.get_verification_error()

                new_count = _count_function_instructions(mod, "__vm_interpret")
                assert new_count > baseline, (
                    f"Interpreter should grow: {new_count} <= {baseline}"
                )
                tmpdir, obj_path = _emit_object(mod, "add", "flat_after_vm")

        result = _link_and_run(
            tmpdir, obj_path, "add",
            [ctypes.c_int32, ctypes.c_int32], ctypes.c_int32,
            (3, 5), "flat_after_vm"
        )
        assert result == 8


# ---------------------------------------------------------------------------
# Test class 4: Obfuscation both before and after VM
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not _HAS_CLANG, reason="clang not available")
class TestObfuscationBothBeforeAndAfterVM:
    """Apply obfuscation before AND after virtualization.

    Pre-VM obfuscation grows bytecode; post-VM obfuscation grows the
    interpreter. Both effects combine. Results must still be correct.
    """

    def test_sub_before_flat_after(self):
        with llvm.create_context() as ctx:
            # Get baselines
            with ctx.create_module("baseline") as bmod:
                make_add_function(ctx, bmod)
                VirtualizationPass(rng=CryptoRandom(seed=99)).run_on_module(bmod, ctx)
                baseline_bc = _get_bytecode_size(bmod, "add")
                baseline_interp = _count_function_instructions(
                    bmod, "__vm_interpret"
                )

            with ctx.create_module("test_vm") as mod:
                make_add_function(ctx, mod)
                rng = CryptoRandom(seed=42)

                # Pre-VM: substitution on add
                for f in mod.functions:
                    if not f.is_declaration:
                        SubstitutionPass(rng=rng).run_on_function(f, ctx)
                assert mod.verify(), mod.get_verification_error()

                # VM
                VirtualizationPass(rng=rng).run_on_module(mod, ctx)
                assert mod.verify(), mod.get_verification_error()

                # Post-VM: flattening on __vm_interpret
                for f in mod.functions:
                    if f.name == "__vm_interpret":
                        FlatteningPass(rng=rng).run_on_function(f, ctx)
                        break
                assert mod.verify(), mod.get_verification_error()

                # Verify growth
                new_bc = _get_bytecode_size(mod, "add")
                new_interp = _count_function_instructions(mod, "__vm_interpret")
                assert new_bc > baseline_bc, (
                    f"Bytecode should grow: {new_bc} <= {baseline_bc}"
                )
                assert new_interp > baseline_interp, (
                    f"Interpreter should grow: {new_interp} <= {baseline_interp}"
                )
                tmpdir, obj_path = _emit_object(mod, "add", "sub_flat_both")

        result = _link_and_run(
            tmpdir, obj_path, "add",
            [ctypes.c_int32, ctypes.c_int32], ctypes.c_int32,
            (3, 5), "sub_flat_both"
        )
        assert result == 8

    def test_mba_before_sub_after(self):
        clear_cache()
        with llvm.create_context() as ctx:
            # Get baselines
            with ctx.create_module("baseline") as bmod:
                make_add_function(ctx, bmod)
                VirtualizationPass(rng=CryptoRandom(seed=99)).run_on_module(bmod, ctx)
                baseline_bc = _get_bytecode_size(bmod, "add")
                baseline_interp = _count_function_instructions(
                    bmod, "__vm_interpret"
                )

            with ctx.create_module("test_vm") as mod:
                make_add_function(ctx, mod)
                rng = CryptoRandom(seed=42)

                # Pre-VM: MBA on add
                for f in mod.functions:
                    if not f.is_declaration:
                        MBAObfuscationPass(rng=rng).run_on_function(f, ctx)
                assert mod.verify(), mod.get_verification_error()

                # VM
                VirtualizationPass(rng=rng).run_on_module(mod, ctx)
                assert mod.verify(), mod.get_verification_error()

                # Post-VM: substitution on __vm_interpret
                for f in mod.functions:
                    if f.name == "__vm_interpret":
                        SubstitutionPass(rng=rng).run_on_function(f, ctx)
                        break
                assert mod.verify(), mod.get_verification_error()

                # Verify growth
                new_bc = _get_bytecode_size(mod, "add")
                new_interp = _count_function_instructions(mod, "__vm_interpret")
                assert new_bc > baseline_bc, (
                    f"Bytecode should grow: {new_bc} <= {baseline_bc}"
                )
                assert new_interp > baseline_interp, (
                    f"Interpreter should grow: {new_interp} <= {baseline_interp}"
                )
                tmpdir, obj_path = _emit_object(mod, "add", "mba_sub_both")

        result = _link_and_run(
            tmpdir, obj_path, "add",
            [ctypes.c_int32, ctypes.c_int32], ctypes.c_int32,
            (3, 5), "mba_sub_both"
        )
        assert result == 8

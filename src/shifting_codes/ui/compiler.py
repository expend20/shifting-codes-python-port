"""Backend logic for compiling LLVM IR and executing functions via ctypes."""

from __future__ import annotations

import ctypes
import os
import platform
import re
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass, field

import llvm
from PyQt6.QtCore import QThread, pyqtSignal


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class IRFunction:
    """A function discovered from IR text."""
    name: str
    return_type: str
    param_types: list[str]
    param_names: list[str]


@dataclass
class ArgValue:
    """A concrete argument value to pass to a function."""
    param_name: str
    type_str: str
    value: int | bytes
    buffer_size: int = 0  # only for ptr args


@dataclass
class CompileResult:
    success: bool
    lib_path: str = ""
    log: str = ""
    error: str = ""


@dataclass
class RunResult:
    success: bool
    return_value: int | float | None = None
    output_buffers: dict[str, bytes] = field(default_factory=dict)
    elapsed_ms: float = 0.0
    error: str = ""


# ---------------------------------------------------------------------------
# Clang discovery
# ---------------------------------------------------------------------------

_clang_cache: tuple[bool, str, str] | None = None  # (available, info, clang_path)


def _find_vs_clang() -> str | None:
    """Search for clang bundled with Visual Studio on Windows."""
    if platform.system() != "Windows":
        return None

    # Try vswhere first (most reliable)
    vswhere = os.path.join(
        os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
        "Microsoft Visual Studio", "Installer", "vswhere.exe",
    )
    vs_roots: list[str] = []
    if os.path.isfile(vswhere):
        try:
            result = subprocess.run(
                [vswhere, "-latest", "-property", "installationPath"],
                capture_output=True, text=True, timeout=5,
            )
            if result.returncode == 0:
                path = result.stdout.strip()
                if path:
                    vs_roots.append(path)
        except (subprocess.TimeoutExpired, OSError):
            pass

    # Fallback: probe well-known edition directories
    for prog_dir in (os.environ.get("ProgramFiles", r"C:\Program Files"),):
        for edition in ("Enterprise", "Professional", "Community", "BuildTools"):
            for year in ("2022", "2019"):
                vs_roots.append(os.path.join(prog_dir, "Microsoft Visual Studio", year, edition))

    # Search each VS root for clang (prefer x64)
    for root in vs_roots:
        for sub in ("VC/Tools/Llvm/x64/bin", "VC/Tools/Llvm/bin"):
            candidate = os.path.join(root, sub, "clang.exe")
            if os.path.isfile(candidate):
                return candidate
    return None


def _try_clang(clang_path: str) -> tuple[bool, str]:
    """Run clang --version and return (success, first_line)."""
    try:
        result = subprocess.run(
            [clang_path, "--version"], capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0:
            return True, result.stdout.strip().split("\n")[0]
        return False, result.stderr.strip()
    except FileNotFoundError:
        return False, f"{clang_path} not found"
    except subprocess.TimeoutExpired:
        return False, "clang --version timed out"


def check_clang() -> tuple[bool, str]:
    """Check if clang is available. Returns (available, version_string).

    Searches PATH first, then Visual Studio installations on Windows.
    """
    global _clang_cache
    if _clang_cache is not None:
        return _clang_cache[0], _clang_cache[1]

    # 1. Try PATH
    ok, info = _try_clang("clang")
    if ok:
        _clang_cache = (True, info, "clang")
        return True, info

    # 2. Try Visual Studio bundled clang (Windows only)
    vs_clang = _find_vs_clang()
    if vs_clang:
        ok, info = _try_clang(vs_clang)
        if ok:
            _clang_cache = (True, f"{info} (VS: {vs_clang})", vs_clang)
            return True, _clang_cache[1]

    _clang_cache = (False, "clang not found on PATH or in Visual Studio", "")
    return False, _clang_cache[1]


def get_clang_path() -> str:
    """Return the resolved clang executable path. Call check_clang() first."""
    if _clang_cache is None:
        check_clang()
    return _clang_cache[2] if _clang_cache else "clang"


# ---------------------------------------------------------------------------
# IR function discovery
# ---------------------------------------------------------------------------

_DEFINE_RE = re.compile(
    r"define\s+(?:dso_local\s+)?(?:(?:internal|private|external|linkonce_odr|weak)\s+)?"
    r"(\w+)"                         # return type (group 1)
    r"\s+@([\w.$]+)"                 # function name (group 2)
    r"\s*\(([^)]*)\)",               # param list (group 3)
)

_PARAM_RE = re.compile(
    r"(\w+(?:\s*\*)*)"              # type
    r"(?:\s+%(\w[\w.]*))?",         # optional name
)


def discover_functions(ir_text: str) -> list[IRFunction]:
    """Parse LLVM IR text to discover defined functions."""
    functions = []
    for m in _DEFINE_RE.finditer(ir_text):
        ret_type = m.group(1)
        name = m.group(2)
        params_str = m.group(3).strip()

        param_types: list[str] = []
        param_names: list[str] = []

        if params_str and params_str != "...":
            for i, part in enumerate(params_str.split(",")):
                part = part.strip()
                # Remove attributes like noundef, signext, etc.
                tokens = part.split()
                # Find the type token (first one that looks like a type)
                ptype = ""
                pname = ""
                for t in tokens:
                    if t.startswith("%"):
                        pname = t.lstrip("%")
                    elif t in ("noundef", "signext", "zeroext", "readonly",
                               "writeonly", "nocapture", "nonnull", "align",
                               "dereferenceable", "inreg", "byval", "sret"):
                        continue
                    elif re.match(r"^(i\d+|ptr|void|float|double|half|\[.*\]|\{.*\})$", t):
                        ptype = t
                    elif re.match(r"^\d+$", t):
                        # alignment value after 'align'
                        continue
                if not pname:
                    pname = f"arg{i}"
                if ptype:
                    param_types.append(ptype)
                    param_names.append(pname)

        functions.append(IRFunction(
            name=name,
            return_type=ret_type,
            param_types=param_types,
            param_names=param_names,
        ))
    return functions


# ---------------------------------------------------------------------------
# Type mapping
# ---------------------------------------------------------------------------

_LLVM_TO_CTYPE = {
    "i1": ctypes.c_bool,
    "i8": ctypes.c_int8,
    "i16": ctypes.c_int16,
    "i32": ctypes.c_int32,
    "i64": ctypes.c_int64,
    "float": ctypes.c_float,
    "double": ctypes.c_double,
    "ptr": ctypes.c_void_p,
}


def _ctype_for(type_str: str):
    """Map LLVM IR type string to ctypes type."""
    ct = _LLVM_TO_CTYPE.get(type_str)
    if ct is None:
        raise ValueError(f"Unsupported LLVM type: {type_str}")
    return ct


# ---------------------------------------------------------------------------
# Compile IR to shared library
# ---------------------------------------------------------------------------

def compile_ir(ir_text: str, tmpdir: str) -> CompileResult:
    """Compile IR text to a shared library via LLVM + clang."""
    log_lines: list[str] = []
    is_windows = platform.system() == "Windows"
    triple = "x86_64-pc-windows-msvc" if is_windows else "x86_64-unknown-linux-gnu"
    shared_ext = ".dll" if is_windows else ".so"

    obj_path = os.path.join(tmpdir, "out.o")
    lib_path = os.path.join(tmpdir, f"out{shared_ext}")

    try:
        # Initialize LLVM
        llvm.initialize_all_targets()
        llvm.initialize_all_target_mcs()
        llvm.initialize_all_target_infos()
        llvm.initialize_all_asm_printers()
        llvm.initialize_all_asm_parsers()

        # Parse IR and emit object file
        with llvm.create_context() as ctx:
            with ctx.parse_ir(ir_text) as mod:
                mod.target_triple = triple

                # On Windows, mark all non-declaration functions as dllexport
                if is_windows:
                    for func in mod.functions:
                        if not func.is_declaration:
                            func.dll_storage_class = llvm.DLLExport

                if not mod.verify():
                    return CompileResult(
                        success=False,
                        error=f"IR verification failed: {mod.get_verification_error()}",
                    )

                log_lines.append(f"Target: {triple}")
                target = llvm.get_target_from_triple(triple)
                tm = llvm.create_target_machine(target, triple, "generic", "")
                tm.emit_to_file(mod, obj_path, llvm.CodeGenFileType.ObjectFile)
                log_lines.append(f"Object emitted: {obj_path}")

        # Link with clang
        clang = get_clang_path()
        compile_cmd = [clang, "-shared", "-o", lib_path, obj_path]
        if not is_windows:
            compile_cmd.insert(1, "-fPIC")

        result = subprocess.run(
            compile_cmd, capture_output=True, text=True, timeout=30
        )
        if result.returncode != 0:
            return CompileResult(
                success=False,
                log="\n".join(log_lines),
                error=f"Clang linking failed:\n{result.stderr}",
            )

        log_lines.append(f"Shared library: {lib_path}")
        return CompileResult(success=True, lib_path=lib_path, log="\n".join(log_lines))

    except Exception as e:
        return CompileResult(
            success=False,
            log="\n".join(log_lines),
            error=str(e),
        )


# ---------------------------------------------------------------------------
# Run a function from a shared library
# ---------------------------------------------------------------------------

def run_function(lib_path: str, func: IRFunction, args: list[ArgValue]) -> RunResult:
    """Load a shared library and call the specified function."""
    is_windows = platform.system() == "Windows"
    lib = None
    lib_handle = None

    try:
        lib = ctypes.CDLL(lib_path)
        lib_handle = lib._handle

        cfunc = getattr(lib, func.name)

        # Build argtypes
        argtypes = []
        for pt in func.param_types:
            argtypes.append(_ctype_for(pt))
        cfunc.argtypes = argtypes

        # Build restype
        if func.return_type == "void":
            cfunc.restype = None
        else:
            cfunc.restype = _ctype_for(func.return_type)

        # Prepare argument values and ptr buffers
        call_args = []
        ptr_buffers: dict[str, ctypes.Array] = {}

        for av in args:
            if av.type_str == "ptr":
                # Create a byte buffer
                buf_data = av.value if isinstance(av.value, bytes) else b"\x00" * av.buffer_size
                buf = (ctypes.c_uint8 * len(buf_data))(*buf_data)
                ptr_buffers[av.param_name] = buf
                call_args.append(ctypes.cast(buf, ctypes.c_void_p))
            else:
                ct = _ctype_for(av.type_str)
                call_args.append(ct(av.value))

        start = time.perf_counter()
        ret = cfunc(*call_args)
        elapsed = (time.perf_counter() - start) * 1000

        # Read back pointer buffers
        output_buffers = {}
        for name, buf in ptr_buffers.items():
            output_buffers[name] = bytes(buf)

        return RunResult(
            success=True,
            return_value=ret,
            output_buffers=output_buffers,
            elapsed_ms=elapsed,
        )

    except Exception as e:
        return RunResult(success=False, error=str(e))
    finally:
        if lib_handle is not None and is_windows:
            ctypes.windll.kernel32.FreeLibrary(ctypes.c_void_p(lib_handle))


# ---------------------------------------------------------------------------
# Worker thread
# ---------------------------------------------------------------------------

@dataclass
class ExportResult:
    success: bool
    output_path: str = ""
    log: str = ""
    error: str = ""


def export_object(ir_text: str, output_path: str) -> ExportResult:
    """Compile IR text directly to an object file at *output_path*."""
    log_lines: list[str] = []
    is_windows = platform.system() == "Windows"
    triple = "x86_64-pc-windows-msvc" if is_windows else "x86_64-unknown-linux-gnu"

    try:
        llvm.initialize_all_targets()
        llvm.initialize_all_target_mcs()
        llvm.initialize_all_target_infos()
        llvm.initialize_all_asm_printers()
        llvm.initialize_all_asm_parsers()

        with llvm.create_context() as ctx:
            with ctx.parse_ir(ir_text) as mod:
                mod.target_triple = triple

                if not mod.verify():
                    return ExportResult(
                        success=False,
                        error=f"IR verification failed: {mod.get_verification_error()}",
                    )

                log_lines.append(f"Target: {triple}")
                target = llvm.get_target_from_triple(triple)
                tm = llvm.create_target_machine(target, triple, "generic", "")
                tm.emit_to_file(mod, output_path, llvm.CodeGenFileType.ObjectFile)
                log_lines.append(f"Object emitted: {output_path}")

        return ExportResult(
            success=True, output_path=output_path, log="\n".join(log_lines)
        )

    except Exception as e:
        return ExportResult(
            success=False, log="\n".join(log_lines), error=str(e)
        )


def export_executable(ir_text: str, output_path: str) -> ExportResult:
    """Compile IR to an object file, then link into an executable."""
    log_lines: list[str] = []
    is_windows = platform.system() == "Windows"

    # Check for main function in IR
    if "define" in ir_text and "@main" not in ir_text:
        log_lines.append("Warning: no @main function found in IR — linking may fail")

    tmpdir = tempfile.mkdtemp(prefix="shifting_codes_export_")
    obj_ext = ".obj" if is_windows else ".o"
    obj_path = os.path.join(tmpdir, f"out{obj_ext}")

    try:
        # Step 1: emit object file to temp dir
        obj_result = export_object(ir_text, obj_path)
        if not obj_result.success:
            return ExportResult(
                success=False,
                log=obj_result.log,
                error=f"Object emission failed: {obj_result.error}",
            )
        log_lines.append(obj_result.log)

        # Step 2: link with clang
        clang = get_clang_path()
        link_cmd = [clang, "-o", output_path, obj_path]
        log_lines.append(f"Linking: {' '.join(link_cmd)}")

        result = subprocess.run(
            link_cmd, capture_output=True, text=True, timeout=30,
        )
        if result.returncode != 0:
            return ExportResult(
                success=False,
                log="\n".join(log_lines),
                error=f"Linking failed:\n{result.stderr}",
            )

        log_lines.append(f"Executable: {output_path}")
        return ExportResult(
            success=True, output_path=output_path, log="\n".join(log_lines)
        )

    except Exception as e:
        return ExportResult(
            success=False, log="\n".join(log_lines), error=str(e)
        )
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


class ExportWorker(QThread):
    """Worker thread for exporting object files or executables."""

    log = pyqtSignal(str)
    finished = pyqtSignal(object)  # ExportResult
    error = pyqtSignal(str)

    def __init__(
        self,
        ir_text: str,
        output_path: str,
        export_type: str,  # "object" or "executable"
        parent=None,
    ):
        super().__init__(parent)
        self.ir_text = ir_text
        self.output_path = output_path
        self.export_type = export_type

    def run(self):
        try:
            if self.export_type == "object":
                self.log.emit("Emitting object file...")
                result = export_object(self.ir_text, self.output_path)
            else:
                self.log.emit("Compiling executable...")
                result = export_executable(self.ir_text, self.output_path)

            if result.log:
                self.log.emit(result.log)
            if result.success:
                self.log.emit(f"Export complete: {result.output_path}")
            self.finished.emit(result)
        except Exception as e:
            self.error.emit(str(e))


class CompileRunWorker(QThread):
    """Worker thread that compiles and runs IR off the UI thread."""

    log = pyqtSignal(str)
    finished = pyqtSignal(object, object)  # (obfuscated RunResult, original RunResult | None)
    error = pyqtSignal(str)

    def __init__(
        self,
        obfuscated_ir: str,
        func: IRFunction,
        args: list[ArgValue],
        original_ir: str | None = None,
        compare: bool = False,
        parent=None,
    ):
        super().__init__(parent)
        self.obfuscated_ir = obfuscated_ir
        self.func = func
        self.args = args
        self.original_ir = original_ir
        self.compare = compare

    def run(self):
        tmpdir = tempfile.mkdtemp(prefix="shifting_codes_")
        try:
            orig_result = None

            # Compile & run original if comparing
            if self.compare and self.original_ir:
                self.log.emit("Compiling original IR...")
                orig_dir = os.path.join(tmpdir, "original")
                os.makedirs(orig_dir)
                cr = compile_ir(self.original_ir, orig_dir)
                if not cr.success:
                    self.error.emit(f"Original compile failed: {cr.error}")
                    return
                self.log.emit(f"Original compiled. Running {self.func.name}()...")
                orig_result = run_function(cr.lib_path, self.func, self.args)
                if not orig_result.success:
                    self.error.emit(f"Original run failed: {orig_result.error}")
                    return
                self.log.emit(f"Original ran in {orig_result.elapsed_ms:.2f}ms")

            # Compile & run obfuscated
            self.log.emit("Compiling obfuscated IR...")
            obf_dir = os.path.join(tmpdir, "obfuscated")
            os.makedirs(obf_dir)
            cr = compile_ir(self.obfuscated_ir, obf_dir)
            if not cr.success:
                self.error.emit(f"Obfuscated compile failed: {cr.error}")
                return
            self.log.emit(f"Obfuscated compiled. Running {self.func.name}()...")
            obf_result = run_function(cr.lib_path, self.func, self.args)
            if not obf_result.success:
                self.error.emit(f"Obfuscated run failed: {obf_result.error}")
                return
            self.log.emit(f"Obfuscated ran in {obf_result.elapsed_ms:.2f}ms")

            self.finished.emit(obf_result, orig_result)

        except Exception as e:
            self.error.emit(str(e))
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

"""Parse C/C++ source for function annotations and compile to LLVM IR."""

from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass

from shifting_codes.ui.compiler import get_clang_path


@dataclass
class AnnotatedFunction:
    """A function found in C/C++ source code."""
    name: str
    line_number: int
    annotated: bool


# Matches C function definitions: return_type name(params) {
# Captures the function name in group 1.
_FUNC_DEF_RE = re.compile(
    r"^[ \t]*"
    r"(?:(?:static|inline|extern|const|volatile|unsigned|signed|long|short|struct|enum|union)\s+)*"
    r"(?:\w+(?:\s*\*)*)\s+"  # return type
    r"(\w+)"                  # function name (group 1)
    r"\s*\([^)]*\)"           # parameter list
    r"\s*\{",                 # opening brace
    re.MULTILINE,
)

_KEYWORDS = {"if", "while", "for", "switch", "else", "do", "return", "sizeof", "typeof"}

_ANNOTATION_RE = re.compile(r"@obfuscate")


def parse_annotations(source_text: str) -> list[AnnotatedFunction]:
    """Parse C/C++ source text for function definitions and @obfuscate annotations.

    Scans up to 3 lines above each function definition for ``// @obfuscate``
    or ``/* @obfuscate */`` comments.
    """
    lines = source_text.splitlines()
    results: list[AnnotatedFunction] = []

    for m in _FUNC_DEF_RE.finditer(source_text):
        name = m.group(1)
        if name in _KEYWORDS:
            continue

        # Find the line number (1-based)
        line_number = source_text[:m.start()].count("\n") + 1

        # Scan up to 3 lines above for @obfuscate annotation
        annotated = False
        start_line = max(0, line_number - 1 - 3)  # line_number is 1-based, so -1 for index, -3 for lookback
        end_line = line_number - 1  # exclusive, the function def line itself
        for i in range(start_line, end_line):
            if i < len(lines) and _ANNOTATION_RE.search(lines[i]):
                annotated = True
                break

        results.append(AnnotatedFunction(name=name, line_number=line_number, annotated=annotated))

    return results


def compile_c_to_ir(
    source_path: str,
    clang_path: str | None = None,
) -> tuple[bool, str, str]:
    """Compile a C/C++ source file to LLVM IR text.

    Returns:
        (success, ir_text_or_error, warnings)
    """
    if clang_path is None:
        clang_path = get_clang_path()

    ext = os.path.splitext(source_path)[1].lower()
    cmd = [clang_path, "-S", "-emit-llvm", "-O0", "-o", "-", source_path]

    if ext in (".cpp", ".cc", ".cxx", ".c++"):
        cmd.insert(1, "-std=c++17")

    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=30,
        )
        if result.returncode != 0:
            return False, result.stderr.strip(), ""
        return True, result.stdout, result.stderr.strip()
    except FileNotFoundError:
        return False, f"clang not found: {clang_path}", ""
    except subprocess.TimeoutExpired:
        return False, "Compilation timed out (30s)", ""

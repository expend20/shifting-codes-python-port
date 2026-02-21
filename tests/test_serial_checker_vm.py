"""Temporary diagnostic test: Pluto BCF + Virtualization on check_serial only.

Run with:
    CMAKE_PREFIX_PATH="C:\\llvm\\clang+llvm-21.1.0-x86_64-pc-windows-msvc" python -m uv run pytest tests/test_serial_checker_vm.py -v -s
"""

from __future__ import annotations

import os
import tempfile

import llvm

from shifting_codes.passes import PassPipeline
from shifting_codes.passes.bogus_control_flow_pluto import PlutoBogusControlFlowPass
from shifting_codes.passes.virtualization import VirtualizationPass
from shifting_codes.samples import get_serial_checker_source
from shifting_codes.ui.source_parser import compile_c_to_ir
from shifting_codes.utils.crypto import CryptoRandom


def _compile_sample() -> str:
    """Compile the serial checker sample to LLVM IR text."""
    source = get_serial_checker_source()
    tmp_fd, tmp_path = tempfile.mkstemp(suffix=".c", prefix="serial_demo_")
    try:
        os.write(tmp_fd, source.encode())
        os.close(tmp_fd)
        success, ir_or_error, _ = compile_c_to_ir(tmp_path)
        assert success, f"Failed to compile: {ir_or_error}"
        return ir_or_error
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


def test_bcf_then_vm_check_serial_only():
    """Apply Pluto BCF + Virtualization targeting only check_serial.

    Prints the full output IR so we can inspect what happened.
    """
    ir_text = _compile_sample()

    selected = {"check_serial"}

    with llvm.create_context() as ctx:
        with ctx.parse_ir(ir_text) as mod:
            rng = CryptoRandom(seed=42)

            pipeline = PassPipeline()
            pipeline.add(PlutoBogusControlFlowPass(rng=rng))
            pipeline.add(VirtualizationPass(rng=rng))
            pipeline.run(mod, ctx, selected_functions=selected)

            err = mod.get_verification_error()
            assert mod.verify(), f"Module verification failed: {err}"

            result_ir = mod.to_string()

    # ---- Diagnostic output ----
    print("\n" + "=" * 72)
    print("OUTPUT IR  (Pluto BCF + Virtualization on check_serial only)")
    print("=" * 72)
    print(result_ir)
    print("=" * 72)

    # check_serial should be virtualized (its body replaced with VM stub)
    assert "__vm_bytecode_check_serial" in result_ir, \
        "Expected __vm_bytecode_check_serial in output"
    assert "__vm_interpret" in result_ir, \
        "Expected __vm_interpret in output"

    # derive_license_tier, tier_name, main should NOT be virtualized
    assert "__vm_bytecode_derive_license_tier" not in result_ir, \
        "derive_license_tier should NOT have been virtualized"
    assert "__vm_bytecode_main" not in result_ir, \
        "main should NOT have been virtualized"
    assert "__vm_bytecode_tier_name" not in result_ir, \
        "tier_name should NOT have been virtualized"

    # The original functions should still exist (not deleted)
    func_names = set()
    with llvm.create_context() as ctx2:
        with ctx2.parse_ir(result_ir) as mod2:
            for f in mod2.functions:
                func_names.add(f.name)

    assert "derive_license_tier" in func_names
    assert "main" in func_names

"""Tests verifying that virtualization works AFTER obfuscation passes.

Each test applies one or more obfuscation passes first, then applies
VirtualizationPass, and verifies:
1. The pass returns changed=True (function was successfully virtualized)
2. The bytecode global (@__vm_bytecode_*) was created
3. The interpreter function (@__vm_interpret) exists
4. The module passes verification (mod.verify())
"""

from __future__ import annotations

import pytest
import llvm

from conftest import make_add_function, make_arith_function, make_branch_function

from shifting_codes.passes.substitution import SubstitutionPass
from shifting_codes.passes.mba_obfuscation import MBAObfuscationPass
from shifting_codes.passes.bogus_control_flow import BogusControlFlowPass
from shifting_codes.passes.flattening import FlatteningPass
from shifting_codes.passes.virtualization import VirtualizationPass
from shifting_codes.utils.crypto import CryptoRandom


def _has_bytecode_global(mod: llvm.Module, func_name: str) -> bool:
    """Check if bytecode global exists for a given function name."""
    target = f"__vm_bytecode_{func_name}"
    for gv in mod.globals:
        if gv.name == target:
            return True
    return False


def _has_interpreter(mod: llvm.Module) -> bool:
    """Check if the VM interpreter function exists."""
    for f in mod.functions:
        if f.name == "__vm_interpret":
            return True
    return False


def _has_vm_stub(func: llvm.Function) -> bool:
    """Check if a function body is a VM interpreter stub.

    A stub calls @__vm_interpret and does NOT have the original logic.
    """
    for bb in func.basic_blocks:
        for inst in bb.instructions:
            if inst.opcode == llvm.Opcode.Call:
                try:
                    callee = inst.called_value
                    if callee.name == "__vm_interpret":
                        return True
                except (AttributeError, RuntimeError):
                    pass
    return False


def _apply_function_pass(pass_cls, func, ctx, rng):
    """Apply a FunctionPass to a single function."""
    p = pass_cls(rng=rng)
    return p.run_on_function(func, ctx)


# -------------------------------------------------------------------------
# Tests: individual passes followed by virtualization
# -------------------------------------------------------------------------


class TestSubstitutionThenVirtualization:
    """Substitution replaces binary ops with equivalent expressions."""

    def test_add_function(self, ctx, rng):
        with ctx.create_module("test") as mod:
            func = make_add_function(ctx, mod)
            _apply_function_pass(SubstitutionPass, func, ctx, rng)
            vm = VirtualizationPass(rng=rng)
            changed = vm.run_on_module(mod, ctx)
            assert changed, "VirtualizationPass should virtualize after substitution"
            assert _has_bytecode_global(mod, "add")
            assert _has_interpreter(mod)
            assert _has_vm_stub(func)
            mod.verify()

    def test_arith_function(self, ctx, rng):
        with ctx.create_module("test") as mod:
            func = make_arith_function(ctx, mod)
            _apply_function_pass(SubstitutionPass, func, ctx, rng)
            vm = VirtualizationPass(rng=rng)
            changed = vm.run_on_module(mod, ctx)
            assert changed, "VirtualizationPass should virtualize after substitution"
            assert _has_bytecode_global(mod, "arith")
            assert _has_vm_stub(func)
            mod.verify()

    def test_branch_function(self, ctx, rng):
        with ctx.create_module("test") as mod:
            func = make_branch_function(ctx, mod)
            _apply_function_pass(SubstitutionPass, func, ctx, rng)
            vm = VirtualizationPass(rng=rng)
            changed = vm.run_on_module(mod, ctx)
            assert changed, "VirtualizationPass should virtualize after substitution"
            assert _has_bytecode_global(mod, "branch_func")
            assert _has_vm_stub(func)
            mod.verify()


class TestFlatteningThenVirtualization:
    """Flattening converts control flow to switch-based dispatch."""

    def test_branch_function(self, ctx, rng):
        with ctx.create_module("test") as mod:
            func = make_branch_function(ctx, mod)
            _apply_function_pass(FlatteningPass, func, ctx, rng)
            vm = VirtualizationPass(rng=rng)
            changed = vm.run_on_module(mod, ctx)
            assert changed, "VirtualizationPass should virtualize after flattening"
            assert _has_bytecode_global(mod, "branch_func")
            assert _has_vm_stub(func)
            mod.verify()

    def test_add_function(self, ctx, rng):
        with ctx.create_module("test") as mod:
            func = make_add_function(ctx, mod)
            _apply_function_pass(FlatteningPass, func, ctx, rng)
            vm = VirtualizationPass(rng=rng)
            changed = vm.run_on_module(mod, ctx)
            assert changed, "VirtualizationPass should virtualize after flattening"
            assert _has_bytecode_global(mod, "add")
            assert _has_vm_stub(func)
            mod.verify()


class TestBCFThenVirtualization:
    """Bogus Control Flow adds opaque predicates using URem."""

    def test_add_function(self, ctx, rng):
        with ctx.create_module("test") as mod:
            func = make_add_function(ctx, mod)
            _apply_function_pass(BogusControlFlowPass, func, ctx, rng)
            vm = VirtualizationPass(rng=rng)
            changed = vm.run_on_module(mod, ctx)
            assert changed, "VirtualizationPass should virtualize after BCF"
            assert _has_bytecode_global(mod, "add")
            assert _has_vm_stub(func)
            mod.verify()

    def test_branch_function(self, ctx, rng):
        with ctx.create_module("test") as mod:
            func = make_branch_function(ctx, mod)
            _apply_function_pass(BogusControlFlowPass, func, ctx, rng)
            vm = VirtualizationPass(rng=rng)
            changed = vm.run_on_module(mod, ctx)
            assert changed, "VirtualizationPass should virtualize after BCF"
            assert _has_bytecode_global(mod, "branch_func")
            assert _has_vm_stub(func)
            mod.verify()


class TestMBAThenVirtualization:
    """MBA obfuscation expands binary ops into complex expressions."""

    def test_add_function(self, ctx, rng):
        with ctx.create_module("test") as mod:
            func = make_add_function(ctx, mod)
            _apply_function_pass(MBAObfuscationPass, func, ctx, rng)
            vm = VirtualizationPass(rng=rng)
            changed = vm.run_on_module(mod, ctx)
            assert changed, "VirtualizationPass should virtualize after MBA"
            assert _has_bytecode_global(mod, "add")
            assert _has_vm_stub(func)
            mod.verify()

    def test_arith_function(self, ctx, rng):
        with ctx.create_module("test") as mod:
            func = make_arith_function(ctx, mod)
            _apply_function_pass(MBAObfuscationPass, func, ctx, rng)
            vm = VirtualizationPass(rng=rng)
            changed = vm.run_on_module(mod, ctx)
            assert changed, "VirtualizationPass should virtualize after MBA"
            assert _has_bytecode_global(mod, "arith")
            assert _has_vm_stub(func)
            mod.verify()


class TestCombinedPassesThenVirtualization:
    """Multiple obfuscation passes applied before virtualization."""

    def test_sub_then_flat_then_vm(self, ctx, rng):
        with ctx.create_module("test") as mod:
            func = make_branch_function(ctx, mod)
            _apply_function_pass(SubstitutionPass, func, ctx, rng)
            _apply_function_pass(FlatteningPass, func, ctx, rng)
            vm = VirtualizationPass(rng=rng)
            changed = vm.run_on_module(mod, ctx)
            assert changed, "Should virtualize after Sub+Flat"
            assert _has_bytecode_global(mod, "branch_func")
            assert _has_vm_stub(func)
            mod.verify()

    def test_bcf_then_flat_then_vm(self, ctx, rng):
        with ctx.create_module("test") as mod:
            func = make_branch_function(ctx, mod)
            _apply_function_pass(BogusControlFlowPass, func, ctx, rng)
            _apply_function_pass(FlatteningPass, func, ctx, rng)
            vm = VirtualizationPass(rng=rng)
            changed = vm.run_on_module(mod, ctx)
            assert changed, "Should virtualize after BCF+Flat"
            assert _has_bytecode_global(mod, "branch_func")
            assert _has_vm_stub(func)
            mod.verify()

    def test_sub_then_bcf_then_vm(self, ctx, rng):
        with ctx.create_module("test") as mod:
            func = make_add_function(ctx, mod)
            _apply_function_pass(SubstitutionPass, func, ctx, rng)
            _apply_function_pass(BogusControlFlowPass, func, ctx, rng)
            vm = VirtualizationPass(rng=rng)
            changed = vm.run_on_module(mod, ctx)
            assert changed, "Should virtualize after Sub+BCF"
            assert _has_bytecode_global(mod, "add")
            assert _has_vm_stub(func)
            mod.verify()

    def test_mba_then_flat_then_vm(self, ctx, rng):
        with ctx.create_module("test") as mod:
            func = make_branch_function(ctx, mod)
            _apply_function_pass(MBAObfuscationPass, func, ctx, rng)
            _apply_function_pass(FlatteningPass, func, ctx, rng)
            vm = VirtualizationPass(rng=rng)
            changed = vm.run_on_module(mod, ctx)
            assert changed, "Should virtualize after MBA+Flat"
            assert _has_bytecode_global(mod, "branch_func")
            assert _has_vm_stub(func)
            mod.verify()

    def test_all_passes_then_vm(self, ctx, rng):
        """Sub + MBA + BCF + Flattening → Virtualization."""
        with ctx.create_module("test") as mod:
            func = make_add_function(ctx, mod)
            _apply_function_pass(SubstitutionPass, func, ctx, rng)
            _apply_function_pass(MBAObfuscationPass, func, ctx, rng)
            _apply_function_pass(BogusControlFlowPass, func, ctx, rng)
            _apply_function_pass(FlatteningPass, func, ctx, rng)
            vm = VirtualizationPass(rng=rng)
            changed = vm.run_on_module(mod, ctx)
            assert changed, "Should virtualize after all passes"
            assert _has_bytecode_global(mod, "add")
            assert _has_vm_stub(func)
            mod.verify()

"""Tests for the Custom Calling Convention pass."""

import llvm
import pytest

from shifting_codes.passes.custom_cc import CustomCCPass
from shifting_codes.utils.crypto import CryptoRandom


def _create_module_with_internal_funcs(ctx, mod):
    """Create a module with internal functions and call sites."""
    i32 = ctx.types.i32

    # Internal function
    helper_ty = ctx.types.function(i32, [i32])
    helper = mod.add_function("helper", helper_ty)
    helper.linkage = llvm.Linkage.Internal
    entry = helper.append_basic_block("entry")
    with entry.create_builder() as b:
        result = b.add(helper.get_param(0), i32.constant(1), "r")
        b.ret(result)

    # Another internal function
    helper2_ty = ctx.types.function(i32, [i32, i32])
    helper2 = mod.add_function("helper2", helper2_ty)
    helper2.linkage = llvm.Linkage.Internal
    entry2 = helper2.append_basic_block("entry")
    with entry2.create_builder() as b:
        result = b.mul(helper2.get_param(0), helper2.get_param(1), "r")
        b.ret(result)

    # Public function that calls both
    main_ty = ctx.types.function(i32, [i32])
    main_func = mod.add_function("main_func", main_ty)
    main_entry = main_func.append_basic_block("entry")
    with main_entry.create_builder() as b:
        c1 = b.call(helper, [main_func.get_param(0)], "c1")
        c2 = b.call(helper2, [c1, i32.constant(3)], "c2")
        b.ret(c2)


def test_custom_cc_changes_convention(ctx, rng):
    """Internal functions should get non-C calling conventions."""
    with ctx.create_module("test") as mod:
        _create_module_with_internal_funcs(ctx, mod)

        p = CustomCCPass(rng=rng)
        changed = p.run_on_module(mod, ctx)

        assert changed
        assert mod.verify(), mod.get_verification_error()

        ir = mod.to_string()
        # At least one non-C calling convention should appear
        assert any(cc in ir for cc in [
            "fastcc", "coldcc", "preserve_mostcc", "preserve_allcc",
            "x86_regcallcc", "x86_64_sysvcc", "win64cc",
        ])


def test_custom_cc_call_sites_match(ctx, rng):
    """Call sites should use same CC as the function they call."""
    with ctx.create_module("test") as mod:
        _create_module_with_internal_funcs(ctx, mod)

        p = CustomCCPass(rng=rng)
        p.run_on_module(mod, ctx)

        assert mod.verify(), mod.get_verification_error()

        # Check that each internal function's CC matches its call site CC
        for func in mod.functions:
            if func.is_declaration:
                continue
            if func.linkage not in (llvm.Linkage.Internal, llvm.Linkage.Private):
                continue
            func_cc = func.calling_conv
            func_name = func.name
            # Find call sites
            for caller in mod.functions:
                for bb in caller.basic_blocks:
                    for inst in bb.instructions:
                        if inst.opcode == llvm.Opcode.Call:
                            called = inst.get_operand(inst.num_operands - 1)
                            if hasattr(called, 'name') and called.name == func_name:
                                assert inst.instruction_call_conv == func_cc


def test_custom_cc_no_internals_noop(ctx, rng):
    """If no internal functions exist, pass should be no-op."""
    i32 = ctx.types.i32
    with ctx.create_module("test") as mod:
        fn_ty = ctx.types.function(i32, [i32])
        func = mod.add_function("public_func", fn_ty)
        entry = func.append_basic_block("entry")
        with entry.create_builder() as b:
            b.ret(func.get_param(0))

        p = CustomCCPass(rng=rng)
        changed = p.run_on_module(mod, ctx)
        assert not changed


def test_custom_cc_deterministic(ctx):
    """Same seed should produce same output."""
    results = []
    for _ in range(2):
        with ctx.create_module("test") as mod:
            _create_module_with_internal_funcs(ctx, mod)
            p = CustomCCPass(rng=CryptoRandom(seed=88))
            p.run_on_module(mod, ctx)
            results.append(mod.to_string())

    assert results[0] == results[1]

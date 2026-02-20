"""Tests for selective function pipeline."""

import llvm
import pytest

from shifting_codes.passes import PassPipeline
from shifting_codes.passes.substitution import SubstitutionPass
from shifting_codes.passes.global_encryption import GlobalEncryptionPass
from shifting_codes.utils.crypto import CryptoRandom
from conftest import make_add_function


@pytest.fixture
def two_func_module(ctx):
    """Create a module with two functions: 'add' and 'sub_func'."""
    mod = ctx.create_module("test")
    mod_obj = mod.__enter__()

    i32 = ctx.types.i32
    fn_ty = ctx.types.function(i32, [i32, i32])

    # Function 'add'
    make_add_function(ctx, mod_obj)

    # Function 'sub_func': i32 @sub_func(i32 %a, i32 %b) { ret a - b }
    func2 = mod_obj.add_function("sub_func", fn_ty)
    func2.get_param(0).name = "a"
    func2.get_param(1).name = "b"
    entry = func2.append_basic_block("entry")
    with entry.create_builder() as builder:
        result = builder.sub(func2.get_param(0), func2.get_param(1), "result")
        builder.ret(result)

    yield mod_obj
    mod.__exit__(None, None, None)


def _get_func_ir(mod, name):
    """Get the IR text of a specific function from a module."""
    full_ir = mod.to_string()
    # Find the function definition
    lines = full_ir.splitlines()
    in_func = False
    func_lines = []
    for line in lines:
        if f"@{name}(" in line and "define" in line:
            in_func = True
        if in_func:
            func_lines.append(line)
            if line.strip() == "}":
                break
    return "\n".join(func_lines)


def test_selected_functions_only(ctx, two_func_module, rng):
    """Only named functions receive FunctionPass."""
    mod = two_func_module

    # Capture original IR for both functions
    add_before = _get_func_ir(mod, "add")
    sub_before = _get_func_ir(mod, "sub_func")

    pipeline = PassPipeline()
    pipeline.add(SubstitutionPass(rng=rng))

    # Only apply to 'add'
    changed = pipeline.run(mod, ctx, selected_functions={"add"})

    add_after = _get_func_ir(mod, "add")
    sub_after = _get_func_ir(mod, "sub_func")

    # add should be modified, sub_func should be unchanged
    assert add_after != add_before, "add should have been obfuscated"
    assert sub_after == sub_before, "sub_func should be unchanged"
    assert changed is True


def test_none_means_all(ctx, two_func_module, rng):
    """selected_functions=None preserves old behavior — all functions processed."""
    mod = two_func_module

    add_before = _get_func_ir(mod, "add")
    sub_before = _get_func_ir(mod, "sub_func")

    pipeline = PassPipeline()
    pipeline.add(SubstitutionPass(rng=rng))

    pipeline.run(mod, ctx, selected_functions=None)

    add_after = _get_func_ir(mod, "add")
    sub_after = _get_func_ir(mod, "sub_func")

    # Both should be modified
    assert add_after != add_before, "add should have been obfuscated"
    assert sub_after != sub_before, "sub_func should have been obfuscated"


def test_empty_set_skips_all(ctx, two_func_module, rng):
    """Empty set means no FunctionPasses run."""
    mod = two_func_module

    add_before = _get_func_ir(mod, "add")
    sub_before = _get_func_ir(mod, "sub_func")

    pipeline = PassPipeline()
    pipeline.add(SubstitutionPass(rng=rng))

    changed = pipeline.run(mod, ctx, selected_functions=set())

    add_after = _get_func_ir(mod, "add")
    sub_after = _get_func_ir(mod, "sub_func")

    # Neither should be modified
    assert add_after == add_before, "add should be unchanged"
    assert sub_after == sub_before, "sub_func should be unchanged"
    assert changed is False


def test_module_pass_ignores_selection(ctx, rng):
    """ModulePasses (GlobalEncryption) run regardless of selected_functions."""
    with ctx.create_module("test") as mod:
        i32 = ctx.types.i32

        # Create a global variable to encrypt
        gv = mod.add_global(i32, "secret_value")
        gv.initializer = i32.constant(42)
        gv.linkage = llvm.Linkage.Internal
        gv.set_constant(True)

        # Create a function that uses the global
        fn_ty = ctx.types.function(i32, [])
        func = mod.add_function("get_secret", fn_ty)
        entry = func.append_basic_block("entry")
        with entry.create_builder() as builder:
            val = builder.load(i32, gv, "val")
            builder.ret(val)

        ir_before = mod.to_string()

        pipeline = PassPipeline()
        pipeline.add(GlobalEncryptionPass(rng=rng))

        # Even with empty selected_functions, module pass should still run
        changed = pipeline.run(mod, ctx, selected_functions=set())

        ir_after = mod.to_string()
        assert ir_after != ir_before, "GlobalEncryption should have modified the module"
        assert changed is True

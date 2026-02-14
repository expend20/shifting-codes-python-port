"""Shared test fixtures."""

import pytest
import llvm

from shifting_codes.utils.crypto import CryptoRandom


@pytest.fixture
def ctx():
    """Provide an LLVM context that is cleaned up after the test."""
    with llvm.create_context() as c:
        yield c


@pytest.fixture
def rng():
    """Provide a seeded CryptoRandom for deterministic tests."""
    return CryptoRandom(seed=42)


def make_add_function(ctx: llvm.Context, mod: llvm.Module) -> llvm.Function:
    """Create a simple function: i32 @add(i32 %a, i32 %b) { ret a + b }"""
    i32 = ctx.types.i32
    fn_ty = ctx.types.function(i32, [i32, i32])
    func = mod.add_function("add", fn_ty)
    func.get_param(0).name = "a"
    func.get_param(1).name = "b"

    entry = func.append_basic_block("entry")
    with entry.create_builder() as builder:
        result = builder.add(func.get_param(0), func.get_param(1), "result")
        builder.ret(result)
    return func


def make_arith_function(ctx: llvm.Context, mod: llvm.Module) -> llvm.Function:
    """Create a function with multiple binary ops: add, sub, and, or, xor."""
    i32 = ctx.types.i32
    fn_ty = ctx.types.function(i32, [i32, i32])
    func = mod.add_function("arith", fn_ty)
    a = func.get_param(0)
    b = func.get_param(1)
    a.name = "a"
    b.name = "b"

    entry = func.append_basic_block("entry")
    with entry.create_builder() as builder:
        s = builder.add(a, b, "s")
        d = builder.sub(s, b, "d")
        x = builder.xor(d, a, "x")
        o = builder.or_(x, b, "o")
        r = builder.and_(o, a, "r")
        builder.ret(r)
    return func


def make_branch_function(ctx: llvm.Context, mod: llvm.Module) -> llvm.Function:
    """Create a function with conditional branching (diamond pattern)."""
    i32 = ctx.types.i32
    i1 = ctx.types.i1
    fn_ty = ctx.types.function(i32, [i32, i32])
    func = mod.add_function("branch_func", fn_ty)
    a = func.get_param(0)
    b = func.get_param(1)
    a.name = "a"
    b.name = "b"

    entry = func.append_basic_block("entry")
    if_true = func.append_basic_block("if_true")
    if_false = func.append_basic_block("if_false")
    merge = func.append_basic_block("merge")

    with entry.create_builder() as builder:
        cond = builder.icmp(llvm.IntPredicate.SGT, a, b, "cond")
        builder.cond_br(cond, if_true, if_false)

        builder.position_at_end(if_true)
        val_true = builder.add(a, b, "val_true")
        builder.br(merge)

        builder.position_at_end(if_false)
        val_false = builder.sub(a, b, "val_false")
        builder.br(merge)

        builder.position_at_end(merge)
        phi = builder.phi(i32, "result")
        phi.add_incoming(val_true, if_true)
        phi.add_incoming(val_false, if_false)
        builder.ret(phi)

    return func


def make_loop_function(ctx: llvm.Context, mod: llvm.Module) -> llvm.Function:
    """Create a function with a simple loop (sum 1..n)."""
    i32 = ctx.types.i32
    fn_ty = ctx.types.function(i32, [i32])
    func = mod.add_function("sum_to_n", fn_ty)
    n = func.get_param(0)
    n.name = "n"

    entry = func.append_basic_block("entry")
    loop = func.append_basic_block("loop")
    exit_bb = func.append_basic_block("exit")

    with entry.create_builder() as builder:
        builder.br(loop)

        builder.position_at_end(loop)
        i_phi = builder.phi(i32, "i")
        sum_phi = builder.phi(i32, "sum")
        new_sum = builder.add(sum_phi, i_phi, "new_sum")
        new_i = builder.add(i_phi, i32.constant(1), "new_i")
        loop_cond = builder.icmp(llvm.IntPredicate.SLE, new_i, n, "loop_cond")
        builder.cond_br(loop_cond, loop, exit_bb)

        i_phi.add_incoming(i32.constant(1), entry)
        i_phi.add_incoming(new_i, loop)
        sum_phi.add_incoming(i32.constant(0), entry)
        sum_phi.add_incoming(new_sum, loop)

        builder.position_at_end(exit_bb)
        builder.ret(new_sum)

    return func

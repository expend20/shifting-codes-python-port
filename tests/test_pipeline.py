"""Tests for the PassPipeline orchestration."""

import llvm
import pytest

from conftest import make_arith_function, make_branch_function
from shifting_codes.passes import PassPipeline
from shifting_codes.passes.substitution import SubstitutionPass
from shifting_codes.passes.bogus_control_flow import BogusControlFlowPass
from shifting_codes.passes.flattening import FlatteningPass
from shifting_codes.utils.crypto import CryptoRandom


def test_pipeline_empty(ctx):
    """Empty pipeline should not modify module."""
    with ctx.create_module("test") as mod:
        make_arith_function(ctx, mod)
        original = mod.to_string()

        pipeline = PassPipeline()
        changed = pipeline.run(mod, ctx)

        assert not changed
        assert mod.to_string() == original


def test_pipeline_single_pass(ctx, rng):
    """Pipeline with one pass should run it."""
    with ctx.create_module("test") as mod:
        make_arith_function(ctx, mod)
        original = mod.to_string()

        pipeline = PassPipeline([SubstitutionPass(rng=rng)])
        changed = pipeline.run(mod, ctx)

        assert changed
        assert mod.verify(), mod.get_verification_error()
        assert mod.to_string() != original


def test_pipeline_multiple_passes(ctx):
    """Pipeline with multiple passes should run them in order."""
    with ctx.create_module("test") as mod:
        make_branch_function(ctx, mod)

        pipeline = PassPipeline([
            SubstitutionPass(rng=CryptoRandom(seed=1)),
            BogusControlFlowPass(rng=CryptoRandom(seed=2)),
        ])
        changed = pipeline.run(mod, ctx)

        assert changed
        assert mod.verify(), mod.get_verification_error()


def test_pipeline_add_pass(ctx, rng):
    """Pipeline.add should append passes."""
    with ctx.create_module("test") as mod:
        make_branch_function(ctx, mod)

        pipeline = PassPipeline()
        pipeline.add(SubstitutionPass(rng=rng))
        assert len(pipeline.passes) == 1

        pipeline.add(BogusControlFlowPass(rng=rng))
        assert len(pipeline.passes) == 2

        pipeline.run(mod, ctx)
        assert mod.verify(), mod.get_verification_error()

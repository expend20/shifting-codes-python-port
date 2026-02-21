# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

Python port of Pluto LLVM obfuscation passes using llvm-nanobind bindings. Six passes transform LLVM IR to obfuscate code: Substitution, MBA Obfuscation, Bogus Control Flow, Flattening, Global Encryption, and Indirect Call.

## Commands

```bash
# Run all tests
python -m uv run pytest tests/ -v

# Run a single test file
python -m uv run pytest tests/test_substitution.py -v

# Run a single test by name
python -m uv run pytest tests/test_substitution.py -k "test_add_substitution" -v

# Run UI (requires llvm-nanobind built)
python -m uv run python -m shifting_codes.ui.app
```

## Dependencies

- **llvm-nanobind**: Built from source via git by default; override to local path in `pyproject.toml` for development
- **z3-solver**: Constraint solving for MBA coefficient generation
- **PyQt6**: GUI framework (UI not yet tested)
- Python 3.12+ required, managed with UV + hatchling build backend

## Architecture

### Pass System

All passes inherit from `FunctionPass` or `ModulePass` (in `src/shifting_codes/passes/base.py`) and are auto-registered via `@PassRegistry.register` decorator. Each pass implements `run_on_function(func, ctx)` or `run_on_module(mod, ctx)` returning a bool indicating modification.

Passes are composed via `PassPipeline` (in `src/shifting_codes/passes/__init__.py`):
```python
pipeline = PassPipeline()
pipeline.add(SubstitutionPass(rng=CryptoRandom(seed=42)))
pipeline.run(mod, ctx)
```

**FunctionPasses:** Substitution, MBAObfuscation, BogusControlFlow, Flattening
**ModulePasses:** GlobalEncryption, IndirectCall

### Utilities (`src/shifting_codes/utils/`)

- **`crypto.py`** — `CryptoRandom`: wraps `secrets` (production) or `random.Random(seed)` (testing). All passes accept an `rng` parameter for determinism.
- **`mba.py`** — Z3-based MBA coefficient generation with result caching. Generates linear (15 truth tables) and univariate polynomial expressions.
- **`ir_helpers.py`** — PHI/register demotion to stack (`demote_phi_to_stack`, `demote_regs_to_stack`), used by Flattening pass.

### XTEA (`src/shifting_codes/xtea/`)

Reference XTEA cipher implementation (pure Python) plus an LLVM IR builder that constructs the same cipher using the nanobind Builder API. Used for end-to-end testing: build IR → apply all passes → compile → execute via ctypes → verify against reference.

### Test Fixtures (`tests/conftest.py`)

- `ctx`: Fresh LLVM context per test
- `rng`: Seeded `CryptoRandom(seed=42)` for deterministic tests
- Helper functions: `make_add_function()`, `make_arith_function()`, `make_branch_function()`, `make_loop_function()`

## llvm-nanobind API Pitfalls

- `ctx.types.ptr`, `ctx.types.i32`, `ctx.types.void` are **properties** (not methods)
- `ctx.create_module("name")` returns a context manager: `with ctx.create_module("name") as mod:`
- `inst.block` for parent block (not `.parent`)
- `gv.global_value_type` for content type (not `gv.type` which returns pointer type)
- `call_inst.called_value` is read-only — to change call target, rebuild the call instruction
- `builder.call(func, args, name)` for direct calls; `builder.call(func_ty, ptr, args, name)` for indirect calls
- `mod.target_triple = "..."` (not `mod.triple`)
- `func.dll_storage_class = llvm.DLLExport` required for Windows DLL exports
- Integer constants must be masked to bit width: `key & ((1 << vtype.int_width) - 1)`
- ConstantDataArray element access via `get_operand()` crashes — avoid array encryption
- PHI nodes need `inst.add_incoming(value, pred_bb)` when new predecessors are added
- Z3 non-determinism: bound coefficients (`-10 <= X[i] <= 10`) and set `smt.random_seed`

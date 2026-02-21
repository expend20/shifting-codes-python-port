# Shifting Codes

Python port of [Pluto](https://github.com/bluesadi/Pluto), [Polaris](https://github.com/za233/Polaris-Obfuscator/), [riscy-business](https://github.com/thesecretclub/riscy-business), and [VMwhere](https://github.com/MrRoy09/VMwhere) LLVM obfuscation passes using [llvm-nanobind](https://github.com/LLVMParty/llvm-nanobind) bindings, with a PyQt6 visualization UI.

![](assets/UI-showcase.gif)

## Passes

### [Pluto](https://github.com/bluesadi/Pluto) (6 passes)

| Pass | Type | Description |
|------|------|-------------|
| **Substitution** | Function | Replaces arithmetic operations with equivalent but obscure sequences |
| **MBA Obfuscation** | Function | Applies Mixed Boolean-Arithmetic transformations using Z3-generated coefficients |
| **Bogus Control Flow** | Function | Inserts opaque predicates and dead code paths |
| **Flattening** | Function | Transforms control flow into a switch-based dispatch loop |
| **Global Encryption** | Module | XOR-encrypts global variable initializers with runtime decryption stubs |
| **Indirect Call** | Module | Replaces direct function calls with indirect calls through function pointers |

### [Polaris](https://github.com/za233/Polaris-Obfuscator/) (8 passes)

Upgraded versions of four Pluto passes plus four new passes:

| Pass | Type | Description |
|------|------|-------------|
| **Bogus Control Flow** | Function | Modular-arithmetic opaque predicates (upgraded from Pluto's trivial predicates) |
| **Flattening** | Function | Switch-based dispatch with dominance-based state encryption (upgraded from plaintext) |
| **Global Encryption** | Module | Use-based discovery with per-function decryption via shared helper (upgraded from single-site inline) |
| **Indirect Call** | Module | Per-call-site globals with add/subtract pointer masking (upgraded from shared GV, no masking) |
| **Indirect Branch** | Function | Replaces direct branches with indirect jumps through obfuscated jump tables |
| **Alias Access** | Function | Obscures local variable access through pointer aliasing and multi-level struct indirection |
| **Custom CC** | Module | Randomly assigns non-standard calling conventions to internal functions |
| **Merge Function** | Module | Merges multiple functions into a single switch-based dispatcher |

### [VMwhere](https://github.com/MrRoy09/VMwhere) (2 passes)

| Pass | Type | Description |
|------|------|-------------|
| **String Encryption** | Module | XOR-encrypts string constant globals (`[N x i8]`) with per-function stack-local decryption at runtime |
| **Anti-Disassembly** | Function | Injects crafted x86 inline assembly that desynchronizes linear-sweep disassemblers (IDA, Ghidra, objdump) |

### [riscy-business](https://github.com/thesecretclub/riscy-business) (1 pass)

| Pass | Type | Description |
|------|------|-------------|
| **Virtualization** | Module | Translates functions to RISC-V inspired bytecode and replaces them with an embedded interpreter (Phase 1: integer arithmetic) |

## Prerequisites

- **Python 3.12+**
- **[UV](https://docs.astral.sh/uv/)** package manager
- **LLVM 21** development libraries installed (see [llvm-nanobind](https://github.com/expend20/llvm-nanobind) for platform-specific instructions)

## Installation

1. **Install UV** (if not already installed):

   ```bash
   pip install uv
   ```

2. **Install the project** (builds llvm-nanobind from source automatically):

   ```bash
   uv sync
   ```

   For local development with a local llvm-nanobind checkout, override the source in `pyproject.toml`:

   ```toml
   [tool.uv.sources]
   llvm-nanobind = { path = "../llvm-nanobind", editable = true }
   ```

## Usage

Pluto passes:

```python
from shifting_codes.passes import PassPipeline
from shifting_codes.passes.substitution import SubstitutionPass
from shifting_codes.passes.mba_obfuscation import MBAObfuscationPass
from shifting_codes.passes.bogus_control_flow_pluto import PlutoBogusControlFlowPass
from shifting_codes.passes.flattening_pluto import PlutoFlatteningPass
from shifting_codes.passes.global_encryption_pluto import PlutoGlobalEncryptionPass
from shifting_codes.passes.indirect_call_pluto import PlutoIndirectCallPass
from shifting_codes.utils.crypto import CryptoRandom

rng = CryptoRandom(seed=42)

pipeline = PassPipeline()
pipeline.add(SubstitutionPass(rng=rng))
pipeline.add(MBAObfuscationPass(rng=rng))
pipeline.add(PlutoBogusControlFlowPass(rng=rng))
pipeline.add(PlutoFlatteningPass(rng=rng))
pipeline.add(PlutoGlobalEncryptionPass(rng=rng))
pipeline.add(PlutoIndirectCallPass(rng=rng))

pipeline.run(mod, ctx)
```

Polaris passes:

```python
from shifting_codes.passes import PassPipeline
from shifting_codes.passes.substitution import SubstitutionPass
from shifting_codes.passes.mba_obfuscation import MBAObfuscationPass
from shifting_codes.passes.bogus_control_flow import BogusControlFlowPass
from shifting_codes.passes.flattening import FlatteningPass
from shifting_codes.passes.global_encryption import GlobalEncryptionPass
from shifting_codes.passes.indirect_call import IndirectCallPass
from shifting_codes.passes.indirect_branch import IndirectBranchPass
from shifting_codes.passes.alias_access import AliasAccessPass
from shifting_codes.passes.custom_cc import CustomCCPass
from shifting_codes.passes.merge_function import MergeFunctionPass
from shifting_codes.utils.crypto import CryptoRandom

rng = CryptoRandom(seed=42)

pipeline = PassPipeline()
pipeline.add(SubstitutionPass(rng=rng))
pipeline.add(MBAObfuscationPass(rng=rng))
pipeline.add(BogusControlFlowPass(rng=rng))
pipeline.add(FlatteningPass(rng=rng))
pipeline.add(GlobalEncryptionPass(rng=rng))
pipeline.add(IndirectCallPass(rng=rng))
pipeline.add(IndirectBranchPass(rng=rng))
pipeline.add(AliasAccessPass(rng=rng))
pipeline.add(CustomCCPass(rng=rng))
pipeline.add(MergeFunctionPass(rng=rng))

pipeline.run(mod, ctx)
```

VMwhere passes:

```python
from shifting_codes.passes import PassPipeline
from shifting_codes.passes.string_encryption import StringEncryptionPass
from shifting_codes.passes.anti_disassembly import AntiDisassemblyPass
from shifting_codes.utils.crypto import CryptoRandom

rng = CryptoRandom(seed=42)

pipeline = PassPipeline()
pipeline.add(StringEncryptionPass(rng=rng))
pipeline.add(AntiDisassemblyPass(rng=rng, density=0.3))  # density: 0.0-1.0

pipeline.run(mod, ctx)
```

Virtualization pass (riscy-business):

```python
from shifting_codes.passes import PassPipeline
from shifting_codes.passes.virtualization import VirtualizationPass
from shifting_codes.utils.crypto import CryptoRandom

rng = CryptoRandom(seed=42)

pipeline = PassPipeline()
pipeline.add(VirtualizationPass(rng=rng))

pipeline.run(mod, ctx)
```

Passes are registered via `@PassRegistry.register` and can be looked up by name:

```python
from shifting_codes.passes import PassRegistry

cls = PassRegistry.get("substitution")
all_passes = PassRegistry.all_passes()
```

## Running Tests

```bash
# All tests
python -m uv run pytest tests/ -v

# Single test file
python -m uv run pytest tests/test_substitution.py -v

# Single test by name
python -m uv run pytest tests/test_substitution.py -k "test_add_substitution" -v
```

## UI

Launch the PyQt6 visualization GUI:

```bash
python -m uv run python -m shifting_codes.ui.app
```

## Project Structure

```
src/shifting_codes/
  passes/            # Obfuscation passes (base classes, registry, pipeline)
  utils/             # Shared utilities (crypto RNG, MBA solver, IR helpers)
  riscybusiness_vm/  # RISC-V VM: ISA definition, bytecode compiler, interpreter builder
  xtea/              # XTEA cipher — pure Python reference + LLVM IR builder
  ui/                # PyQt6 GUI for visualizing pass transformations
vendor/
  riscy-business/    # Git submodule — RISC-V VM reference (opcodes, encryption, shuffling)
tests/               # pytest test suite
```

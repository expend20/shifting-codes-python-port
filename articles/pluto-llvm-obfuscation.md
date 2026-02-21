---
title: "Hiding in Plain Sight: Code Obfuscation with Pluto"
date: "2026-02-21"
description: "How Pluto transforms LLVM IR into something a reverse engineer will hate, and how to use it from Python today"
tags: ["pluto", "llvm", "obfuscation", "reverse-engineering", "python"]
---

If you've ever wondered how commercial software resists reverse engineering —
those baffling disassembler dumps that look like someone spilled alphabet soup
into a logic circuit — the answer is often *compiler-level obfuscation*.
And if you've wanted to do the same thing to your own code without a
six-figure budget, you're in the right place.

This article walks through how [Pluto](https://github.com/bluesadi/Pluto),
an LLVM obfuscation framework based on the ideas of
[OLLVM](https://github.com/obfuscator-llvm/obfuscator) (itself targeting
LLVM 4.0, which tells you how long this lineage goes back), works under
the hood, why it won't compile today without a time machine, and how
**Shifting.Codes** brings its techniques to modern LLVM — from Python.

---

## What Is Pluto?

[Pluto](https://github.com/bluesadi/Pluto) is a suite of LLVM compiler passes
written in C++ by **bluesadi**. It works by transforming LLVM IR — the
intermediate representation that sits between your source code and machine
instructions — into semantically equivalent but much harder to understand code.

Pluto is no longer actively maintained — the last commit is from 2022 — but
the code is clean, well-commented, and an excellent reference for anyone
interested in compiler-level program transformation. It deserves more stars
than it has.

*(Its successor, [Polaris](https://github.com/za233/Polaris-Obfuscator), pushes
the techniques further — we'll cover it in the next article.)*

### The Passes

Pluto implements several independent *passes*, each applying a different
obfuscation strategy:

| Pass | What It Does |
|------|-------------|
| **Instruction Substitution** | Replaces simple arithmetic with algebraically equivalent but painful alternatives |
| **Bogus Control Flow** | Injects fake branch paths guarded by conditions that always evaluate the same way |
| **Control Flow Flattening** | Converts a function's call graph into a state-machine dispatch loop |
| **MBA Obfuscation** | Wraps operations in Mixed Boolean-Arithmetic expressions that Z3 can verify but humans cannot |
| **Global Encryption** | XOR-encrypts global variables at compile time and injects runtime decryption stubs |
| **Indirect Call** | Turns direct `call foo()` into pointer-table lookups |

Each pass is independent, so you can apply them in any combination. Stack
them all together and the output is something a reverse engineer will need
strong coffee and a therapist to unravel.

---

## The Problem: Pluto Is Frozen in 2022

Here is the catch: Pluto targets **LLVM 14.0.6**, which is baked into the
repository itself. LLVM's internal C++ API is not stable between major
versions. By LLVM 21, enough has shifted that Pluto simply will not build
without non-trivial porting work. The repository is archived and nobody is
coming to fix it.

So Pluto sits on GitHub like a beautifully crafted vintage car — admirable,
educational, and definitively not going anywhere under its own power today.

---

## Enter llvm-nanobind

[llvm-nanobind](https://github.com/LLVMParty/llvm-nanobind) provides
Python bindings for the LLVM C++ API using the
[nanobind](https://github.com/wjakob/nanobind) binding library. Rather than
wrapping a C-stable interface, it exposes the real LLVM API — modules,
functions, basic blocks, instructions, builders — directly to Python.

The authors of llvm-nanobind did something genuinely hard: they maintained
parity with a moving target (LLVM's unstable internal API) and made it
accessible from a language that has no business being this close to a
compiler backend. It is a remarkable piece of engineering.

With llvm-nanobind, you can write code like this in Python:

```python
with ctx.create_module("example") as mod:
    i32 = ctx.types.i32
    fn_ty = ctx.types.function(i32, [i32, i32])
    func = mod.add_function("add", fn_ty)

    entry = func.append_basic_block("entry")
    with entry.create_builder() as b:
        result = b.add(func.get_param(0), func.get_param(1), "result")
        b.ret(result)
```

That is real LLVM IR construction from Python. No C++ required.

---

## Shifting.Codes: Pluto in Python

**Shifting.Codes** is a Python port of Pluto using llvm-nanobind. It
implements all six of Pluto's passes with the same semantics as the original
C++ — substitution, MBA, bogus control flow, flattening, global encryption,
and indirect call.

### Architecture

All passes inherit from `FunctionPass` or `ModulePass` and are registered
automatically via a decorator:

```python
@PassRegistry.register
class SubstitutionPass(FunctionPass):
    def run_on_function(self, func: llvm.Function, ctx: llvm.Context) -> bool:
        ...
```

Passes are composed into a `PassPipeline`:

```python
from shifting_codes.passes import PassPipeline
from shifting_codes.passes.substitution import SubstitutionPass
from shifting_codes.passes.flattening import FlatteningPass

pipeline = PassPipeline()
pipeline.add(SubstitutionPass())
pipeline.add(FlatteningPass())
pipeline.run(mod, ctx)
```

The six passes map directly to Pluto's originals: substitution, MBA, bogus
control flow, flattening, global encryption, and indirect call.

---

## Before and After: What the IR Actually Looks Like

*The IR samples below are lightly simplified for readability — actual output
uses random constants and generated names — but the instruction sequences and
structure match the Python implementation exactly.*

### Instruction Substitution

A trivial add function before the substitution pass:

```llvm
define i32 @add(i32 %a, i32 %b) {
entry:
  %result = add i32 %a, %b
  ret i32 %result
}
```

After substitution (pattern: `a + b = (a + r) + b - r` with a random `r`):

```llvm
define i32 @add(i32 %a, i32 %b) {
entry:
  %sub.ar  = add i32 %a, -1447186197
  %sub.arb = add i32 %sub.ar, %b
  %sub.add = sub i32 %sub.arb, -1447186197
  ret i32 %sub.add
}
```

Same result. Just... exhausting to look at. And this is the *mildest* pass.

---

### Bogus Control Flow

Before BCF, a simple conditional function:

```llvm
define i32 @classify(i32 %x) {
entry:
  %cond = icmp sgt i32 %x, 0
  br i1 %cond, label %pos, label %neg

pos:
  %r1 = mul i32 %x, 2
  ret i32 %r1

neg:
  %r2 = sub i32 0, %x
  ret i32 %r2
}
```

After BCF (modular arithmetic opaque predicates):

```llvm
define i32 @classify(i32 %x) {
entry:
  %bcf.var  = alloca i64
  %bcf.var0 = alloca i64
  store i64 1009, ptr %bcf.var    ; randomly chosen prime — same value in both slots
  store i64 1009, ptr %bcf.var0   ; invariant: bcf.var always == bcf.var0
  %cond = icmp sgt i32 %x, 0
  br i1 %cond, label %pos, label %neg

pos:                               ; head: opaque branch — body always taken
  %bcf.lhs = load i64, ptr %bcf.var
  %bcf.rhs = load i64, ptr %bcf.var0
  %bcf.cmp = icmp eq i64 %bcf.lhs, %bcf.rhs  ; always true
  br i1 %bcf.cmp, label %bcf.body.1, label %bcf.clone.1

bcf.body.1:                        ; real computation + modular state update
  %r1         = mul i32 %x, 2
  %bcf.v      = load i64, ptr %bcf.var0
  %bcf.av     = mul i64 <a>, %bcf.v
  %bcf.avmod  = urem i64 %bcf.av, <m>
  %bcf.sub    = sub i64 %bcf.avmod, <b>
  %bcf.result = urem i64 %bcf.sub, <m>
  store i64 %bcf.result, ptr %bcf.var0   ; always restores bcf.var0 to 1009
  %bcf.lhs2 = load i64, ptr %bcf.var
  %bcf.rhs2 = load i64, ptr %bcf.var0
  %bcf.cmp2 = icmp eq i64 %bcf.lhs2, %bcf.rhs2  ; always true
  br i1 %bcf.cmp2, label %bcf.tail.1, label %bcf.clone.1

bcf.tail.1:
  ret i32 %r1

bcf.clone.1:                       ; dead block — never reached
  %r1.c = mul i32 %x, 2
  ; (cloned modular update omitted for brevity)
  br label %bcf.body.1

; neg gets identical treatment: bcf.body.2 / bcf.tail.2 / bcf.clone.2
neg:
  %bcf.lhs3 = load i64, ptr %bcf.var
  %bcf.rhs3 = load i64, ptr %bcf.var0
  %bcf.cmp3 = icmp ne i64 %bcf.lhs3, %bcf.rhs3  ; always false — targets swapped
  br i1 %bcf.cmp3, label %bcf.clone.2, label %bcf.body.2
; ...
}
```

The constants `<a>`, `<b>`, `<m>` are random per-block values chosen so that
`a * x ≡ x + b (mod m)` — meaning the update always maps `bcf.var0` back to
its initial value, keeping the `bcf.var == bcf.var0` invariant intact. The
predicate is permanently true, but a static analyzer must reconstruct the
modular arithmetic to prove it.

---

### Control Flow Flattening

Before flattening:

```llvm
define i32 @loop_sum(i32 %n) {
entry:
  br label %loop

loop:
  %i   = phi i32 [ 0, %entry ], [ %i.next, %loop ]
  %acc = phi i32 [ 0, %entry ], [ %acc.next, %loop ]
  %i.next   = add i32 %i, 1
  %acc.next = add i32 %acc, %i
  %done = icmp eq i32 %i.next, %n
  br i1 %done, label %exit, label %loop

exit:
  ret i32 %acc.next
}
```

After flattening — PHI nodes are demoted to stack allocas, every block feeds
into a central dispatch switch, and the state value stored between iterations
is XOR-encrypted with a per-block key derived from the dominator tree:

```llvm
define i32 @loop_sum(i32 %n) {
entry:
  ; PHI nodes demoted to stack before flattening
  %i.demoted   = alloca i32
  %acc.demoted = alloca i32
  store i32 0, ptr %i.demoted
  store i32 0, ptr %acc.demoted
  ; flattening control state
  %cff.state   = alloca i32
  %cff.keys    = alloca [2 x i32]   ; one XOR key slot per original block
  %cff.visited = alloca [2 x i8]    ; first-visit flag per block
  ; (zero-init of cff.keys and cff.visited omitted for brevity)
  store i32 <loop_state>, ptr %cff.state
  br label %cff.dispatch

cff.dispatch:
  %cff.sw = load i32, ptr %cff.state
  switch i32 %cff.sw, label %cff.default [
    i32 <loop_state>, label %loop
    i32 <exit_state>, label %exit
  ]

cff.default:
  br label %cff.dispatch          ; unreachable in practice

loop:
  %i        = load i32, ptr %i.demoted
  %acc      = load i32, ptr %acc.demoted
  %i.next   = add i32 %i, 1
  %acc.next = add i32 %acc, %i
  store i32 %i.next,   ptr %i.demoted
  store i32 %acc.next, ptr %acc.demoted
  %done     = icmp eq i32 %i.next, %n
  ; dominance-based key update: loop dominates exit, so XOR exit's key slot on first visit
  %cff.vptr = gep i8, ptr %cff.visited, i8 0
  %cff.vis  = load i8, ptr %cff.vptr
  call void @__cff_update_key(i8 %cff.vis, i32 1,
                               ptr @.cff.dom.loop_sum.0,  ; [i32 1] — exit's index
                               ptr %cff.keys, i32 <key_list[loop]>)
  store i8 1, ptr %cff.vptr
  ; select next encrypted state, XOR with this block's key slot
  %cff.sel  = select i1 %done, i32 <exit_enc>, i32 <loop_enc>
  %cff.kptr = gep i32, ptr %cff.keys, i32 0
  %cff.kval = load i32, ptr %cff.kptr
  %cff.enc  = xor i32 %cff.kval, %cff.sel
  store i32 %cff.enc, ptr %cff.state
  br label %cff.dispatch

exit:
  %cff.vptr1 = gep i8, ptr %cff.visited, i8 1
  %cff.vis1  = load i8, ptr %cff.vptr1
  store i8 1, ptr %cff.vptr1
  %result = load i32, ptr %acc.demoted
  ret i32 %result
}

; helper injected into the module
define private void @__cff_update_key(i8 %flag, i32 %len, ptr %posArray,
                                       ptr %keyArray, i32 %num) { ... }

@.cff.dom.loop_sum.0 = private global [1 x i32] [i32 1]  ; exit's block index
```

The original loop is gone. State values `<loop_state>` and `<exit_state>` are
random 32-bit integers; what gets stored to `cff.state` is their XOR with a
key slot that is itself XOR-updated on first visit based on the dominator tree.
A decompiler has to reconstruct both the key schedule and the dominance
relationships before it can recover the original CFG.

---

## Putting It to the Test

Reading IR is one thing. Watching a decompiler suffer is another.

We took the serial checker demo that ships with the Shifting.Codes UI —
a license validation function that checks a `XXXX-NNNN-XXXX-XXXX` serial
number against a weighted checksum, [source here](/blog/llvm-obfuscation-passes-python-port/serial_checker.c) — compiled it a [native Windows binary](/blog/llvm-obfuscation-passes-python-port/SerialCheckPlutoAll.exe), and
uploaded both the original and obfuscated builds to
[Decompiler Explorer](https://dogbolt.org) — a tool that runs your binary
through Hex-Rays, Ghidra, Binary Ninja, and others simultaneously so you can
compare results side by side.

**Unobfuscated:** [Decompiler Explorer](https://dogbolt.org/?id=9c53cc4c-f37f-4b24-9b95-a167e6eaf330#BinaryNinja=11&Hex-Rays=639&Ghidra=310)

**Obfuscated:** [Decompiler Explorer](https://dogbolt.org/?id=9b65791c-5eb9-48c5-bf92-8c1fb889e664#BinaryNinja=1&Hex-Rays=925&Ghidra=2885)

The results speak for themselves — not bad for an afternoon's worth of Python passes.

---

## Using Shifting.Codes on Your Own Code

### Requirements

- Python 3.12+
- [UV](https://docs.astral.sh/uv/) package manager
- LLVM 21 development libraries
- `clang` in your PATH (to compile `.cpp` → LLVM bitcode)

### Setup

```bash
git clone https://github.com/expend20/shifting-codes-python-port
cd shifting-codes-python-port
uv sync
```

### Obfuscate a .cpp File

**Step 1:** Compile your source to LLVM bitcode:

```bash
clang -O1 -emit-llvm -c your_code.cpp -o your_code.bc
```

**Step 2:** Write a small Python driver script:

```python
import llvm_nanobind as llvm
from shifting_codes.passes import PassPipeline
from shifting_codes.passes.substitution import SubstitutionPass
from shifting_codes.passes.bogus_control_flow import BogusControlFlowPass
from shifting_codes.passes.flattening import FlatteningPass

with llvm.create_context() as ctx:
    mod = llvm.parse_bitcode_file("your_code.bc", ctx)

    pipeline = PassPipeline()
    pipeline.add(SubstitutionPass())
    pipeline.add(BogusControlFlowPass())
    pipeline.add(FlatteningPass())
    pipeline.run(mod, ctx)

    mod.write_bitcode_to_file("obfuscated.bc")
```

```bash
uv run python obfuscate.py
```

**Step 3:** Compile the obfuscated bitcode back to a native binary:

```bash
clang obfuscated.bc -o your_code_obfuscated
```

The result is functionally identical to the original. The disassembly is not.

### Selective Obfuscation

If you only want to obfuscate specific functions, pass a set of function
names to `pipeline.run()`:

```python
pipeline.run(mod, ctx, selected_functions={"check_license_key", "decrypt_payload"})
```

Everything else in the module is left untouched — useful when you want to
protect sensitive logic without inflating the entire binary.

### The UI Tool

If writing a Python driver feels like too much ceremony, Shifting.Codes ships
a **PyQt6 GUI** that handles the whole flow interactively:

- Load or paste C/C++ source directly
- Select passes via checkboxes, reorder them with drag-and-drop
- Choose which functions to target
- See the LLVM IR before and after, side by side in a diff view
- Export the obfuscated binary in one click

```bash
uv run python -m shifting_codes.ui.app
```

The diff view is particularly useful for understanding what each pass
actually does to your code — or for satisfying morbid curiosity about how
bad it can get when you stack all 17 passes at once.

![UI showcase](/blog/llvm-obfuscation-passes-python-port/UI-showcase.gif)

---

## Credits

**Pluto** — designed and authored by [bluesadi](https://github.com/bluesadi).
The original C++ implementation is the intellectual foundation for everything
Shifting.Codes does. It is clear, well-structured, and a genuinely excellent
reference for anyone interested in compiler-level program transformation.
Unmaintained, but worth reading.

**llvm-nanobind** — the binding library that makes this entire Python port
possible. Maintaining accurate Python bindings for the LLVM C++ API across
major versions is a thankless, technically brutal task. Special thanks to
[mrexodia](https://github.com/mrexodia) for his contributions to the project
— without his work this port would not have been feasible.

Without both of these projects, Shifting.Codes would be a much longer C++
program and a much shorter article.

---

*Shifting.Codes is provided for legitimate use cases including software
protection, security research, CTF challenge authoring, and compiler
education. The authors make no representations regarding fitness for any
particular purpose and accept no liability for any misuse or damages arising
from the use of this software. Use is entirely at your own risk and
responsibility.*


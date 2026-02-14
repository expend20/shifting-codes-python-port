# llvm-nanobind Issues & Gotchas

Issues encountered while porting Pluto obfuscation passes to Python using llvm-nanobind.

## API Surprises

### `ctx.types.ptr` is a property, not a method
```python
# WRONG — TypeError: 'llvm.Type' object is not callable
ptr_ty = ctx.types.ptr()

# CORRECT
ptr_ty = ctx.types.ptr
```
Other type accessors like `ctx.types.i32`, `ctx.types.void` are also properties.
Only `ctx.types.function(ret, args)` and `ctx.types.array(elem, count)` are methods.

### `ctx.create_module()` returns `ModuleManager`, not `Module`
Must use as a context manager to get the actual `Module`:
```python
# WRONG — returns ModuleManager, has no add_function/add_global/etc.
mod = ctx.create_module("test")

# CORRECT
with ctx.create_module("test") as mod:
    mod.add_function(...)
```

### `mod.target_triple`, not `mod.triple`
```python
# WRONG
mod.triple = "x86_64-pc-windows-msvc"

# CORRECT
mod.target_triple = "x86_64-pc-windows-msvc"
```

### `llvm.create_target_machine()` is a module-level function
```python
# WRONG — Target has no create_target_machine method
tm = target.create_target_machine(triple, cpu, features)

# CORRECT
tm = llvm.create_target_machine(target, triple, cpu, features)
```

### `inst.block` for parent block, not `inst.parent`
```python
bb = inst.block  # correct
```

### `gv.global_value_type` for content type
`gv.type` returns the pointer type. Use `gv.global_value_type` for the actual stored type.

## Call Instruction Limitations

### No setter for call callee
There is no `set_called_operand()` or `set_callee()` on call instructions.
`called_value` is read-only. To change a call's target, rebuild the call:

```python
# Build new indirect call and replace the old one
with bb.create_builder() as builder:
    builder.position_before(call_inst)
    loaded = builder.load(ptr_ty, gv, "fn.ptr")
    new_call = builder.call(func_ty, loaded, args, "result")
call_inst.replace_all_uses_with(new_call)
call_inst.erase_from_parent()
```

### Two overloads for `builder.call()`
```python
# Direct call (infers function type from Function object)
builder.call(func, args, name)

# Indirect call (explicit function type, callee can be any value/pointer)
builder.call(func_ty, loaded_ptr, args, name)
```
Passing a loaded pointer to the 2-arg form causes `LLVMAssertionError`.

## Segfaults & Crashes

### ConstantDataArray element access crashes
Accessing elements of array initializers via `init.get_operand(i)` on arrays created
with `const_array` causes a segfault. No workaround found — we removed array encryption
from the GlobalEncryption pass entirely.

### `func.dll_storage_class` required for Windows DLL exports
Functions emitted to object files for Windows DLLs must have:
```python
func.dll_storage_class = llvm.DLLExport
```
Otherwise the symbol won't be exported and `ctypes.CDLL` can't find it.

## Missing APIs

### No `splitBasicBlock()`
Cannot split a basic block at an arbitrary instruction. The Bogus Control Flow pass
had to be redesigned to work without block splitting — it inserts opaque predicates
before existing terminators instead of cloning block contents.

## Initialization

### Must initialize ASM printers for `emit_to_file()`
```python
llvm.initialize_all_targets()
llvm.initialize_all_target_mcs()
llvm.initialize_all_target_infos()
llvm.initialize_all_asm_printers()  # without this: "can't emit a file of this type"
```

## Integer Constant Overflow

### Constants must fit the type's bit width
`vtype.constant(value)` raises `TypeError` if `value` exceeds the type's range.
When generating random keys for XOR encryption, mask to the bit width:
```python
bit_width = vtype.int_width
mask = (1 << bit_width) - 1
key = rng.get_uint64() & mask
```

## PHI Node Maintenance

### New predecessors require PHI incoming entries
When adding a new block that branches to an existing block containing PHI nodes,
you must add incoming values for the new predecessor:
```python
for inst in target_bb.instructions:
    if inst.opcode == llvm.Opcode.PHI:
        inst.add_incoming(inst.type.undef(), new_bb)
    else:
        break
```
Without this, the module verifier fails with:
`PHINode should have one entry for each predecessor of its parent basic block!`

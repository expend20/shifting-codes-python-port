"""Global Encryption Pass — port of Polaris GlobalsEncryption.cpp.

Use-based discovery of constant globals, per-function local alloca+decrypt
via a shared byte-level XOR decrypt helper function.

Polaris upgrade: replaces Pluto's single-site inline decryption with
per-function stack-local copies decrypted via a shared helper.
"""

from __future__ import annotations

import llvm

from shifting_codes.passes import PassRegistry
from shifting_codes.passes.base import ModulePass, PassInfo
from shifting_codes.utils.crypto import CryptoRandom
from shifting_codes.utils.ir_helpers import (
    KEY_LEN, build_decrypt_function, encrypt_bytes,
)


def _type_byte_size(ty: llvm.Type) -> int | None:
    """Compute byte size for integer and integer-array types."""
    if ty.kind == llvm.TypeKind.Integer:
        return ty.int_width // 8
    if ty.kind == llvm.TypeKind.Array:
        elem = ty.element_type
        if elem.kind == llvm.TypeKind.Integer:
            return (elem.int_width // 8) * ty.array_length
    return None


def _is_encryptable_global(gv) -> bool:
    """Check if a value is a global variable suitable for encryption."""
    if not hasattr(gv, 'global_value_type'):
        return False
    if not hasattr(gv, 'name') or not gv.name:
        return False
    if gv.name.startswith("llvm.") or gv.name.startswith("__"):
        return False
    if not hasattr(gv, 'linkage'):
        return False
    if gv.linkage not in (llvm.Linkage.Internal, llvm.Linkage.Private,
                          llvm.Linkage.LinkOnceODR):
        return False
    if not hasattr(gv, 'initializer') or gv.initializer is None:
        return False
    if not hasattr(gv, 'is_global_constant') or not gv.is_global_constant:
        return False
    vtype = gv.global_value_type
    if vtype.kind == llvm.TypeKind.Integer:
        return True
    if vtype.kind == llvm.TypeKind.Array:
        return vtype.element_type.kind == llvm.TypeKind.Integer
    return False


@PassRegistry.register
class GlobalEncryptionPass(ModulePass):

    def __init__(self, rng: CryptoRandom | None = None):
        self.rng = rng or CryptoRandom()

    @classmethod
    def info(cls) -> PassInfo:
        return PassInfo(
            name="global_encryption",
            description="[Polaris] Per-function local-copy global encryption",
            is_module_pass=True,
        )

    def run_on_module(self, mod: llvm.Module, ctx: llvm.Context) -> bool:
        # Phase 1: Discover globals by scanning instruction operands
        # gv_name -> (gv, {func_names}, [(inst, operand_idx, func)])
        gv_info: dict[str, tuple] = {}

        for func in mod.functions:
            if func.is_declaration:
                continue
            for bb in func.basic_blocks:
                for inst in bb.instructions:
                    for i in range(inst.num_operands):
                        op = inst.get_operand(i)
                        try:
                            if _is_encryptable_global(op):
                                name = op.name
                                if name not in gv_info:
                                    gv_info[name] = (op, set(), [])
                                gv_info[name][1].add(func.name)
                                gv_info[name][2].append((inst, i, func))
                        except Exception:
                            continue

        if not gv_info:
            return False

        # Phase 2: Build shared decrypt helper
        decrypt_func = build_decrypt_function(mod, ctx)

        i32 = ctx.types.i32
        i64 = ctx.types.i64
        i8 = ctx.types.i8
        ptr = ctx.types.ptr
        changed = False

        # Phase 3: For each global, encrypt and insert per-function decryption
        for gv_name, (gv, func_names, uses) in gv_info.items():
            vtype = gv.global_value_type
            byte_size = _type_byte_size(vtype)
            if byte_size is None or byte_size == 0:
                continue

            # Verify all users are instructions (skip globals used by constants)
            skip = False
            for u in uses:
                if u[0] is None:
                    skip = True
                    break
            if skip:
                continue

            key = self.rng.get_uint32() & 0xFFFFFFFF

            # Encrypt the initializer
            init = gv.initializer
            if init is None:
                continue

            try:
                if vtype.kind == llvm.TypeKind.Integer:
                    orig_val = init.const_zext_value
                    enc_val = encrypt_bytes(orig_val, byte_size, key)
                    gv.initializer = vtype.constant(enc_val)
                elif vtype.kind == llvm.TypeKind.Array:
                    elem_type = vtype.element_type
                    elem_size = elem_type.int_width // 8
                    elem_count = vtype.array_length
                    enc_elements = []
                    byte_offset = 0
                    for j in range(elem_count):
                        elem = init.get_aggregate_element(j)
                        orig_val = elem.const_zext_value
                        enc_val = encrypt_bytes(orig_val, elem_size, key,
                                                 byte_offset)
                        enc_elements.append(enc_val)
                        byte_offset += elem_size
                    enc_consts = [elem_type.constant(v) for v in enc_elements]
                    gv.initializer = llvm.const_array(elem_type, enc_consts)
                else:
                    continue
            except Exception:
                continue

            gv.set_constant(False)
            if gv.linkage == llvm.Linkage.LinkOnceODR:
                gv.linkage = llvm.Linkage.Internal

            # Group uses by function
            func_uses: dict[str, list] = {}
            func_objs: dict[str, llvm.Function] = {}
            for inst, operand_idx, func in uses:
                fn = func.name
                if fn not in func_uses:
                    func_uses[fn] = []
                    func_objs[fn] = func
                func_uses[fn].append((inst, operand_idx))

            # Per-function: alloca + memcpy + decrypt
            func_copies: dict[str, llvm.Value] = {}
            for fn, func in func_objs.items():
                entry_bb = list(func.basic_blocks)[0]
                first_inst = list(entry_bb.instructions)[0]

                with entry_bb.create_builder() as b:
                    b.position_before(first_inst)
                    local_copy = b.alloca(vtype, name="ge.copy")
                    # Byte-by-byte copy: load from global, store to local
                    # Use i8 GEP-based copy loop (no memcpy intrinsic needed)
                    for off in range(byte_size):
                        src_ptr = b.gep(i8, gv, [i64.constant(off)], "ge.src")
                        val = b.load(i8, src_ptr, "ge.byte")
                        dst_ptr = b.gep(i8, local_copy, [i64.constant(off)],
                                        "ge.dst")
                        b.store(val, dst_ptr)

                    # Store key and call decrypt
                    key_alloca = b.alloca(i32, name="ge.key")
                    b.store(i32.constant(key), key_alloca)
                    b.call(decrypt_func,
                           [local_copy, key_alloca,
                            i64.constant(byte_size), i64.constant(KEY_LEN)],
                           "")

                func_copies[fn] = local_copy

            # Replace operands
            for fn, use_list in func_uses.items():
                local = func_copies[fn]
                for inst, operand_idx in use_list:
                    inst.set_operand(operand_idx, local)

            changed = True

        return changed

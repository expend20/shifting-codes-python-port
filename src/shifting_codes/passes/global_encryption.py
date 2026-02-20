"""Global Encryption Pass — port of Pluto GlobalEncryption.cpp.

XOR-encrypts internal/private global variables with integer or integer-array
type at compile time, and inserts decryption code at program startup.
"""

from __future__ import annotations

import llvm

from shifting_codes.passes import PassRegistry
from shifting_codes.passes.base import ModulePass, PassInfo
from shifting_codes.utils.crypto import CryptoRandom


@PassRegistry.register
class GlobalEncryptionPass(ModulePass):

    def __init__(self, rng: CryptoRandom | None = None):
        self.rng = rng or CryptoRandom()

    @classmethod
    def info(cls) -> PassInfo:
        return PassInfo(
            name="global_encryption",
            description="[Pluto] XOR-encrypt internal global variables",
            is_module_pass=True,
        )

    def run_on_module(self, mod: llvm.Module, ctx: llvm.Context) -> bool:
        int_mods = []   # (gv, enc_val, key)
        arr_mods = []   # (gv, encrypted_elements, key, elem_type, elem_count)

        for gv in mod.globals:
            if self._should_skip(gv):
                continue

            vtype = gv.global_value_type
            init = gv.initializer
            if init is None:
                continue

            if vtype.kind == llvm.TypeKind.Integer:
                bit_width = vtype.int_width
                mask = (1 << bit_width) - 1
                key = self.rng.get_uint64() & mask
                try:
                    orig_val = init.const_zext_value
                    enc_val = (key ^ orig_val) & mask
                    int_mods.append((gv, enc_val, key))
                except Exception:
                    continue

            elif vtype.kind == llvm.TypeKind.Array:
                elem_type = vtype.element_type
                if elem_type.kind != llvm.TypeKind.Integer:
                    continue
                elem_count = vtype.array_length
                bit_width = elem_type.int_width
                mask = (1 << bit_width) - 1
                key = self.rng.get_uint64() & mask

                # Read and encrypt each element
                encrypted_elements = []
                try:
                    for i in range(elem_count):
                        elem = init.get_aggregate_element(i)
                        orig_val = elem.const_zext_value
                        enc_val = (key ^ orig_val) & mask
                        encrypted_elements.append(enc_val)
                except Exception:
                    continue
                arr_mods.append((gv, encrypted_elements, key, elem_type, elem_count))

        if not int_mods and not arr_mods:
            return False

        # Find first non-declaration function for decryption insertion
        entry_func = None
        for func in mod.functions:
            if not func.is_declaration:
                entry_func = func
                break

        if entry_func is None:
            return False

        entry_bb = list(entry_func.basic_blocks)[0]
        first_inst = list(entry_bb.instructions)[0]

        # Encrypt array globals and insert decryption loop
        for gv, encrypted_elements, key, elem_type, elem_count in arr_mods:
            # Build encrypted initializer
            enc_consts = [elem_type.constant(v) for v in encrypted_elements]
            enc_init = llvm.const_array(elem_type, enc_consts)
            gv.initializer = enc_init
            gv.set_constant(False)
            # Encrypted content can't be linker-merged; pin to internal.
            if gv.linkage == llvm.Linkage.LinkOnceODR:
                gv.linkage = llvm.Linkage.Internal

            # Insert decryption: for each element, GEP + load + xor + store
            i64 = ctx.types.i64
            with entry_bb.create_builder() as builder:
                builder.position_before(first_inst)
                for i in range(elem_count):
                    elem_ptr = builder.gep(
                        gv.global_value_type, gv,
                        [i64.constant(0), i64.constant(i)],
                        "ge.arr.ptr",
                    )
                    val = builder.load(elem_type, elem_ptr, "ge.arr.load")
                    decrypted = builder.xor(
                        val, elem_type.constant(key), "ge.arr.dec"
                    )
                    builder.store(decrypted, elem_ptr)

        # Encrypt integer globals and insert decryption
        for gv, enc_val, key in int_mods:
            vtype = gv.global_value_type
            gv.initializer = vtype.constant(enc_val)
            gv.set_constant(False)
            if gv.linkage == llvm.Linkage.LinkOnceODR:
                gv.linkage = llvm.Linkage.Internal

            with entry_bb.create_builder() as builder:
                builder.position_before(first_inst)
                val = builder.load(vtype, gv, "ge.load")
                decrypted = builder.xor(val, vtype.constant(key), "ge.dec")
                builder.store(decrypted, gv)

        return True

    # Linkages safe to encrypt: truly module-local, plus linkonce_odr which
    # Windows/MSVC uses for string constants (string pooling).
    _ENCRYPTABLE_LINKAGES = frozenset({
        llvm.Linkage.Internal,
        llvm.Linkage.Private,
        llvm.Linkage.LinkOnceODR,
    })

    @staticmethod
    def _should_skip(gv) -> bool:
        if gv.name.startswith("llvm."):
            return True
        if gv.linkage not in GlobalEncryptionPass._ENCRYPTABLE_LINKAGES:
            return True
        vtype = gv.global_value_type
        if vtype.kind == llvm.TypeKind.Integer:
            return False
        if vtype.kind == llvm.TypeKind.Array:
            elem_type = vtype.element_type
            if elem_type.kind == llvm.TypeKind.Integer:
                return False
        return True

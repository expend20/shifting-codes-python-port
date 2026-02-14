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
            description="XOR-encrypt internal global variables",
            is_module_pass=True,
        )

    def run_on_module(self, mod: llvm.Module, ctx: llvm.Context) -> bool:
        # Collect eligible integer globals
        int_mods = []

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

        if not int_mods:
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

        # Encrypt integer globals and insert decryption
        for gv, enc_val, key in int_mods:
            vtype = gv.global_value_type
            gv.initializer = vtype.constant(enc_val)
            gv.set_constant(False)

            with entry_bb.create_builder() as builder:
                builder.position_before(first_inst)
                val = builder.load(vtype, gv, "ge.load")
                decrypted = builder.xor(val, vtype.constant(key), "ge.dec")
                builder.store(decrypted, gv)

        return True

    @staticmethod
    def _should_skip(gv: llvm.GlobalVariable) -> bool:
        if gv.name.startswith("llvm."):
            return True
        if gv.linkage not in (llvm.Linkage.Internal, llvm.Linkage.Private):
            return True
        vtype = gv.global_value_type
        if vtype.kind == llvm.TypeKind.Integer:
            return False
        return True

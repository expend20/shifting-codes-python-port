"""String Encryption Pass — XOR-encrypts string constant globals.

Finds ConstantDataArray globals with [N x i8] type, XOR-encrypts them
at compile time, and injects per-use stack-local decryption so strings
are not visible in the binary.

Differs from GlobalEncryptionPass which targets integer globals and
integer arrays — this pass specifically targets string literals.
"""

from __future__ import annotations

import llvm

from shifting_codes.passes import PassRegistry
from shifting_codes.passes.base import ModulePass, PassInfo
from shifting_codes.utils.crypto import CryptoRandom
from shifting_codes.utils.ir_helpers import KEY_LEN, build_decrypt_function


def _is_string_global(gv) -> bool:
    """Check if a global variable is a string constant ([N x i8])."""
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
    if vtype.kind != llvm.TypeKind.Array:
        return False
    elem = vtype.element_type
    if elem.kind != llvm.TypeKind.Integer or elem.int_width != 8:
        return False
    return True


@PassRegistry.register
class StringEncryptionPass(ModulePass):

    def __init__(self, rng: CryptoRandom | None = None):
        self.rng = rng or CryptoRandom()

    @classmethod
    def info(cls) -> PassInfo:
        return PassInfo(
            name="string_encryption",
            description="[VMwhere] XOR-encrypt string constant globals",
            is_module_pass=True,
        )

    def run_on_module(self, mod: llvm.Module, ctx: llvm.Context) -> bool:
        # Phase 1: Discover string globals via module globals scan
        # Collect all encryptable string globals first
        string_globals: dict[str, llvm.GlobalVariable] = {}
        for gv in mod.globals:
            try:
                if _is_string_global(gv):
                    string_globals[gv.name] = gv
            except Exception:
                continue

        if not string_globals:
            return False

        # Phase 1b: Find all uses of these globals in instructions
        # gv_name -> [(inst, operand_idx, func)]
        gv_uses: dict[str, list] = {name: [] for name in string_globals}

        for func in mod.functions:
            if func.is_declaration:
                continue
            for bb in func.basic_blocks:
                for inst in bb.instructions:
                    for i in range(inst.num_operands):
                        try:
                            op = inst.get_operand(i)
                            if hasattr(op, 'name') and op.name in string_globals:
                                gv_uses[op.name].append((inst, i, func))
                        except Exception:
                            continue

        # Filter to globals that actually have uses
        used_globals = {name: gv for name, gv in string_globals.items()
                        if gv_uses[name]}
        if not used_globals:
            return False

        # Phase 2: Build shared decrypt helper
        decrypt_func = build_decrypt_function(
            mod, ctx, name="__obfu_strenc_dec")

        i8 = ctx.types.i8
        i32 = ctx.types.i32
        i64 = ctx.types.i64
        changed = False

        # Phase 3: For each string global, encrypt and insert per-function
        # decryption (same pattern as GlobalEncryptionPass)
        for gv_name, gv in used_globals.items():
            vtype = gv.global_value_type
            byte_count = vtype.array_length
            if byte_count == 0:
                continue

            init = gv.initializer
            if init is None:
                continue

            # Read original bytes
            try:
                orig_bytes = []
                for j in range(byte_count):
                    elem = init.get_aggregate_element(j)
                    orig_bytes.append(elem.const_zext_value & 0xFF)
            except Exception:
                continue

            # Generate a 4-byte key
            key_val = self.rng.get_uint32() & 0xFFFFFFFF
            key_bytes = key_val.to_bytes(4, 'little')

            # Encrypt
            enc_bytes = []
            for j, b in enumerate(orig_bytes):
                enc_bytes.append(b ^ key_bytes[j % KEY_LEN])

            # Replace initializer with encrypted array
            enc_consts = [i8.constant(v) for v in enc_bytes]
            gv.initializer = llvm.const_array(i8, enc_consts)
            gv.set_constant(False)
            if gv.linkage == llvm.Linkage.LinkOnceODR:
                gv.linkage = llvm.Linkage.Internal

            # Group uses by function
            uses = gv_uses[gv_name]
            func_uses: dict[str, list] = {}
            func_objs: dict[str, llvm.Function] = {}
            for inst, operand_idx, func in uses:
                fn = func.name
                if fn not in func_uses:
                    func_uses[fn] = []
                    func_objs[fn] = func
                func_uses[fn].append((inst, operand_idx))

            # Per-function: alloca + byte-copy + decrypt at entry block
            func_copies: dict[str, llvm.Value] = {}
            for fn, func in func_objs.items():
                entry_bb = list(func.basic_blocks)[0]
                first_inst = list(entry_bb.instructions)[0]

                with entry_bb.create_builder() as b:
                    b.position_before(first_inst)
                    local_copy = b.alloca(vtype, name="se.copy")

                    # Byte-copy from global to stack
                    for off in range(byte_count):
                        src_ptr = b.gep(i8, gv, [i64.constant(off)],
                                        "se.src")
                        val = b.load(i8, src_ptr, "se.byte")
                        dst_ptr = b.gep(i8, local_copy,
                                        [i64.constant(off)], "se.dst")
                        b.store(val, dst_ptr)

                    # Store key and call decrypt
                    key_alloca = b.alloca(i32, name="se.key")
                    b.store(i32.constant(key_val), key_alloca)
                    b.call(decrypt_func,
                           [local_copy, key_alloca,
                            i64.constant(byte_count),
                            i64.constant(KEY_LEN)],
                           "")

                func_copies[fn] = local_copy

            # Replace operands
            for fn, use_list in func_uses.items():
                local = func_copies[fn]
                for inst, operand_idx in use_list:
                    inst.set_operand(operand_idx, local)

            changed = True

        return changed

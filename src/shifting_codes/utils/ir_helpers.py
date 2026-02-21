"""IR helper functions: PHI demotion, register demotion, collectors, encryption."""

import llvm

KEY_LEN = 4


def encrypt_bytes(orig_val: int, byte_size: int, key: int,
                  byte_offset: int = 0) -> int:
    """XOR integer value byte-by-byte with 4-byte key (cyclic)."""
    key_bytes = (key & 0xFFFFFFFF).to_bytes(4, 'little')
    val_bytes = bytearray(orig_val.to_bytes(byte_size, 'little', signed=False))
    for i in range(byte_size):
        val_bytes[i] ^= key_bytes[(byte_offset + i) % KEY_LEN]
    return int.from_bytes(val_bytes, 'little')


def build_decrypt_function(mod: llvm.Module, ctx: llvm.Context,
                           name: str = "__obfu_globalenc_dec") -> llvm.Function:
    """Build: void @<name>(ptr %data, ptr %key, i64 %len, i64 %keyLen)

    Loop body: data[i] ^= key[i % keyLen]
    """
    i8 = ctx.types.i8
    i64 = ctx.types.i64
    ptr = ctx.types.ptr
    fn_ty = ctx.types.function(ctx.types.void, [ptr, ptr, i64, i64])
    func = mod.add_function(name, fn_ty)
    func.linkage = llvm.Linkage.Private

    entry_bb = func.append_basic_block("entry")
    cmp_bb = func.append_basic_block("cmp")
    body_bb = func.append_basic_block("body")
    end_bb = func.append_basic_block("end")

    data = func.get_param(0)
    key = func.get_param(1)
    length = func.get_param(2)
    key_len = func.get_param(3)

    with entry_bb.create_builder() as b:
        i_ptr = b.alloca(i64, name="i")
        b.store(i64.constant(0), i_ptr)
        b.br(cmp_bb)

    with cmp_bb.create_builder() as b:
        iv = b.load(i64, i_ptr, "iv")
        cond = b.icmp(llvm.IntPredicate.SLT, iv, length, "cmp")
        b.cond_br(cond, body_bb, end_bb)

    with body_bb.create_builder() as b:
        iv = b.load(i64, i_ptr, "iv")
        key_idx = b.srem(iv, key_len, "kidx")
        key_ptr = b.gep(i8, key, [key_idx], "kptr")
        key_byte = b.load(i8, key_ptr, "kbyte")
        data_ptr = b.gep(i8, data, [iv], "dptr")
        data_byte = b.load(i8, data_ptr, "dbyte")
        dec = b.xor(key_byte, data_byte, "dec")
        b.store(dec, data_ptr)
        b.store(b.add(iv, i64.constant(1), "inc"), i_ptr)
        b.br(cmp_bb)

    with end_bb.create_builder() as b:
        b.ret_void()

    return func


def demote_phi_to_stack(func: llvm.Function) -> None:
    """Demote all PHI nodes in a function to stack variables.

    For each PHI node:
    1. Create an alloca in the entry block
    2. Store each incoming value at the end of its predecessor block
    3. Load from the alloca where the PHI was
    4. Replace uses of PHI with the load
    5. Delete the PHI
    """
    entry_bb = list(func.basic_blocks)[0]

    phi_nodes = []
    for bb in func.basic_blocks:
        for inst in bb.instructions:
            if inst.opcode == llvm.Opcode.PHI:
                phi_nodes.append(inst)

    if not phi_nodes:
        return

    entry_first = list(entry_bb.instructions)[0]

    for phi in phi_nodes:
        phi_bb = phi.block
        phi_type = phi.type

        with entry_bb.create_builder() as builder:
            builder.position_before(entry_first)
            alloca = builder.alloca(phi_type, name=f"{phi.name}.demoted")

        for i in range(phi.num_incoming):
            incoming_val = phi.get_incoming_value(i)
            incoming_bb = phi.get_incoming_block(i)
            incoming_term = incoming_bb.terminator

            with incoming_bb.create_builder() as builder:
                builder.position_before(incoming_term)
                builder.store(incoming_val, alloca)

        with phi_bb.create_builder() as builder:
            builder.position_before(phi)
            load = builder.load(phi_type, alloca, phi.name)

        phi.replace_all_uses_with(load)
        phi.erase_from_parent()


def demote_regs_to_stack(func: llvm.Function) -> None:
    """Demote instructions used outside their defining block to stack variables."""
    entry_bb = list(func.basic_blocks)[0]
    entry_term = entry_bb.terminator

    to_demote = []
    for bb in func.basic_blocks:
        for inst in bb.instructions:
            if inst.opcode == llvm.Opcode.Alloca and inst.block == entry_bb:
                continue
            used_outside = False
            for use in inst.uses:
                user = use.user
                if hasattr(user, 'block') and user.block != bb:
                    used_outside = True
                    break
            if used_outside:
                to_demote.append(inst)

    for inst in to_demote:
        inst_bb = inst.block

        with entry_bb.create_builder() as builder:
            builder.position_before(entry_term)
            alloca = builder.alloca(inst.type, name=f"{inst.name}.reg2mem")

        with inst_bb.create_builder() as builder:
            next_insts = list(inst_bb.instructions)
            idx = None
            for i, ii in enumerate(next_insts):
                if ii == inst:
                    idx = i
                    break
            if idx is not None and idx + 1 < len(next_insts):
                builder.position_before(next_insts[idx + 1])
            else:
                builder.position_before(inst_bb.terminator)
            builder.store(inst, alloca)

        users_to_fix = []
        for use in inst.uses:
            user = use.user
            if hasattr(user, 'block') and user.block != inst_bb:
                users_to_fix.append(user)

        for user in users_to_fix:
            user_bb = user.block
            with user_bb.create_builder() as builder:
                builder.position_before(user)
                load = builder.load(inst.type, alloca, inst.name)
            for i in range(user.num_operands):
                if user.get_operand(i) == inst:
                    user.set_operand(i, load)


def collect_binary_ops(bb: llvm.BasicBlock) -> list[llvm.Value]:
    """Collect all binary integer operations in a basic block."""
    ops = []
    binary_opcodes = {
        llvm.Opcode.Add, llvm.Opcode.Sub, llvm.Opcode.Mul,
        llvm.Opcode.And, llvm.Opcode.Or, llvm.Opcode.Xor,
        llvm.Opcode.Shl, llvm.Opcode.LShr, llvm.Opcode.AShr,
        llvm.Opcode.UDiv, llvm.Opcode.SDiv, llvm.Opcode.URem, llvm.Opcode.SRem,
    }
    for inst in bb.instructions:
        if inst.opcode in binary_opcodes and inst.type.kind == llvm.TypeKind.Integer:
            ops.append(inst)
    return ops

"""Build XTEA encrypt function as LLVM IR using the Builder API.

Produces: void @xtea_encrypt(ptr %v, ptr %key, i32 %num_rounds)
where %v points to two i32 values [v0, v1] and %key points to four i32 values.
"""

from __future__ import annotations

import llvm

DELTA = 0x9E3779B9


def build_xtea_encrypt(ctx: llvm.Context, mod: llvm.Module) -> llvm.Function:
    """Build the XTEA encrypt function into the given module.

    Returns the created function.
    """
    i32 = ctx.types.i32
    ptr = ctx.types.ptr
    void = ctx.types.void

    # void @xtea_encrypt(ptr %v, ptr %key, i32 %num_rounds)
    func_ty = ctx.types.function(void, [ptr, ptr, i32])
    func = mod.add_function("xtea_encrypt", func_ty)
    func.linkage = llvm.Linkage.External

    v_param = func.get_param(0)
    key_param = func.get_param(1)
    rounds_param = func.get_param(2)
    v_param.name = "v"
    key_param.name = "key"
    rounds_param.name = "num_rounds"

    # Basic blocks
    entry_bb = func.append_basic_block("entry")
    loop_cond_bb = func.append_basic_block("loop.cond")
    loop_body_bb = func.append_basic_block("loop.body")
    exit_bb = func.append_basic_block("exit")

    # === Entry block ===
    with entry_bb.create_builder() as b:
        # Alloca local variables
        v0_ptr = b.alloca(i32, name="v0.ptr")
        v1_ptr = b.alloca(i32, name="v1.ptr")
        sum_ptr = b.alloca(i32, name="sum.ptr")
        i_ptr = b.alloca(i32, name="i.ptr")

        # Load v[0] and v[1] via GEP
        v0_gep = b.gep(i32, v_param, [i32.constant(0)], "v0.addr")
        v0_init = b.load(i32, v0_gep, "v0.init")
        b.store(v0_init, v0_ptr)

        v1_gep = b.gep(i32, v_param, [i32.constant(1)], "v1.addr")
        v1_init = b.load(i32, v1_gep, "v1.init")
        b.store(v1_init, v1_ptr)

        # sum = 0, i = 0
        b.store(i32.constant(0), sum_ptr)
        b.store(i32.constant(0), i_ptr)
        b.br(loop_cond_bb)

    # === Loop condition ===
    with loop_cond_bb.create_builder() as b:
        i_val = b.load(i32, i_ptr, "i")
        cond = b.icmp(llvm.IntPredicate.SLT, i_val, rounds_param, "loop.cond")
        b.cond_br(cond, loop_body_bb, exit_bb)

    # === Loop body ===
    with loop_body_bb.create_builder() as b:
        v0 = b.load(i32, v0_ptr, "v0")
        v1 = b.load(i32, v1_ptr, "v1")
        sum_val = b.load(i32, sum_ptr, "sum")

        # --- First half-round: v0 update ---
        # ((v1 << 4) ^ (v1 >> 5)) + v1
        v1_shl4 = b.shl(v1, i32.constant(4), "v1.shl4")
        v1_shr5 = b.lshr(v1, i32.constant(5), "v1.shr5")
        v1_xor = b.xor(v1_shl4, v1_shr5, "v1.xor")
        v1_mix = b.add(v1_xor, v1, "v1.mix")

        # sum + key[sum & 3]
        sum_and3 = b.and_(sum_val, i32.constant(3), "sum.and3")
        key_idx0 = b.gep(i32, key_param, [sum_and3], "key.idx0")
        key_val0 = b.load(i32, key_idx0, "key.val0")
        sum_key0 = b.add(sum_val, key_val0, "sum.key0")

        # XOR the two parts
        round_xor0 = b.xor(v1_mix, sum_key0, "round.xor0")

        # v0 += ...
        v0_new = b.add(v0, round_xor0, "v0.new")
        b.store(v0_new, v0_ptr)

        # --- sum += DELTA ---
        sum_new = b.add(sum_val, i32.constant(DELTA), "sum.new")
        b.store(sum_new, sum_ptr)

        # --- Second half-round: v1 update ---
        # ((v0_new << 4) ^ (v0_new >> 5)) + v0_new
        v0n_shl4 = b.shl(v0_new, i32.constant(4), "v0n.shl4")
        v0n_shr5 = b.lshr(v0_new, i32.constant(5), "v0n.shr5")
        v0n_xor = b.xor(v0n_shl4, v0n_shr5, "v0n.xor")
        v0n_mix = b.add(v0n_xor, v0_new, "v0n.mix")

        # sum_new + key[(sum_new >> 11) & 3]
        sum_shr11 = b.lshr(sum_new, i32.constant(11), "sum.shr11")
        sum_shr_and3 = b.and_(sum_shr11, i32.constant(3), "sum.shr.and3")
        key_idx1 = b.gep(i32, key_param, [sum_shr_and3], "key.idx1")
        key_val1 = b.load(i32, key_idx1, "key.val1")
        sum_key1 = b.add(sum_new, key_val1, "sum.key1")

        # XOR the two parts
        round_xor1 = b.xor(v0n_mix, sum_key1, "round.xor1")

        # v1 += ...
        v1_new = b.add(v1, round_xor1, "v1.new")
        b.store(v1_new, v1_ptr)

        # i++
        i_val2 = b.load(i32, i_ptr, "i.cur")
        i_next = b.add(i_val2, i32.constant(1), "i.next")
        b.store(i_next, i_ptr)
        b.br(loop_cond_bb)

    # === Exit block ===
    with exit_bb.create_builder() as b:
        # Store results back: v[0] = v0, v[1] = v1
        v0_final = b.load(i32, v0_ptr, "v0.final")
        v1_final = b.load(i32, v1_ptr, "v1.final")
        v0_out = b.gep(i32, v_param, [i32.constant(0)], "v0.out")
        v1_out = b.gep(i32, v_param, [i32.constant(1)], "v1.out")
        b.store(v0_final, v0_out)
        b.store(v1_final, v1_out)
        b.ret_void()

    return func

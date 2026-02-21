"""Build the VM interpreter as LLVM IR using the nanobind Builder API.

Constructs: void @__vm_interpret(ptr %bytecode, i64 %bc_len, ptr %args,
                                  ptr %ret_slot, ptr %host_table)

The interpreter has a 32x64-bit register file, a program counter, and a stack.
It fetches, decodes, and dispatches instructions in a loop using a switch on
the 5-bit opcode field.
"""

from __future__ import annotations

import llvm

from shifting_codes.riscybusiness_vm.isa import (
    Opcode, SYSCALL_EXIT, SYSCALL_HOST_CALL,
)

# VM constants
NUM_REGS = 32
VM_STACK_SIZE = 4096


def build_vm_interpreter(mod: llvm.Module, ctx: llvm.Context) -> llvm.Function:
    """Build the VM interpreter function into the given module.

    Signature:
        void @__vm_interpret(ptr %bytecode, i64 %bc_len, ptr %args,
                             ptr %ret_slot, ptr %host_table)
    """
    i8 = ctx.types.i8
    i32 = ctx.types.i32
    i64 = ctx.types.i64
    ptr = ctx.types.ptr
    void = ctx.types.void

    fn_ty = ctx.types.function(void, [ptr, i64, ptr, ptr, ptr])
    func = mod.add_function("__vm_interpret", fn_ty)
    func.linkage = llvm.Linkage.Private

    # Name params
    bc_param = func.get_param(0)
    bc_len_param = func.get_param(1)
    args_param = func.get_param(2)
    ret_slot_param = func.get_param(3)
    host_table_param = func.get_param(4)
    bc_param.name = "bytecode"
    bc_len_param.name = "bc_len"
    args_param.name = "args"
    ret_slot_param.name = "ret_slot"
    host_table_param.name = "host_table"

    # --- Create all basic blocks upfront ---
    entry_bb = func.append_basic_block("entry")
    loop_header_bb = func.append_basic_block("loop.header")
    loop_body_bb = func.append_basic_block("loop.body")
    handler_op64_bb = func.append_basic_block("handler.op64")
    handler_imm64_bb = func.append_basic_block("handler.imm64")
    handler_lui_bb = func.append_basic_block("handler.lui")
    handler_auipc_bb = func.append_basic_block("handler.auipc")
    handler_branch_bb = func.append_basic_block("handler.branch")
    handler_jal_bb = func.append_basic_block("handler.jal")
    handler_load_bb = func.append_basic_block("handler.load")
    handler_store_bb = func.append_basic_block("handler.store")
    handler_system_bb = func.append_basic_block("handler.system")
    default_bb = func.append_basic_block("handler.default")
    exit_bb = func.append_basic_block("exit")

    # Sub-handlers for op64 dispatch
    op64_add_bb = func.append_basic_block("op64.add")
    op64_sub_bb = func.append_basic_block("op64.sub")
    op64_sll_bb = func.append_basic_block("op64.sll")
    op64_slt_bb = func.append_basic_block("op64.slt")
    op64_sltu_bb = func.append_basic_block("op64.sltu")
    op64_xor_bb = func.append_basic_block("op64.xor")
    op64_srl_bb = func.append_basic_block("op64.srl")
    op64_sra_bb = func.append_basic_block("op64.sra")
    op64_or_bb = func.append_basic_block("op64.or")
    op64_and_bb = func.append_basic_block("op64.and")
    op64_mul_bb = func.append_basic_block("op64.mul")
    op64_div_bb = func.append_basic_block("op64.div")
    op64_divu_bb = func.append_basic_block("op64.divu")
    op64_rem_bb = func.append_basic_block("op64.rem")
    op64_remu_bb = func.append_basic_block("op64.remu")
    op64_done_bb = func.append_basic_block("op64.done")

    # Sub-handlers for imm64 dispatch
    imm64_addi_bb = func.append_basic_block("imm64.addi")
    imm64_slli_bb = func.append_basic_block("imm64.slli")
    imm64_xori_bb = func.append_basic_block("imm64.xori")
    imm64_srxi_bb = func.append_basic_block("imm64.srxi")
    imm64_ori_bb = func.append_basic_block("imm64.ori")
    imm64_andi_bb = func.append_basic_block("imm64.andi")
    imm64_slti_bb = func.append_basic_block("imm64.slti")
    imm64_sltiu_bb = func.append_basic_block("imm64.sltiu")
    imm64_done_bb = func.append_basic_block("imm64.done")

    # Branch sub-handlers
    branch_beq_bb = func.append_basic_block("branch.beq")
    branch_bne_bb = func.append_basic_block("branch.bne")
    branch_blt_bb = func.append_basic_block("branch.blt")
    branch_bge_bb = func.append_basic_block("branch.bge")
    branch_bltu_bb = func.append_basic_block("branch.bltu")
    branch_bgeu_bb = func.append_basic_block("branch.bgeu")
    branch_taken_bb = func.append_basic_block("branch.taken")
    branch_not_taken_bb = func.append_basic_block("branch.not_taken")

    # Load sub-handlers
    load_lb_bb = func.append_basic_block("load.lb")
    load_lh_bb = func.append_basic_block("load.lh")
    load_lw_bb = func.append_basic_block("load.lw")
    load_ld_bb = func.append_basic_block("load.ld")
    load_done_bb = func.append_basic_block("load.done")

    # Store sub-handlers
    store_sb_bb = func.append_basic_block("store.sb")
    store_sh_bb = func.append_basic_block("store.sh")
    store_sw_bb = func.append_basic_block("store.sw")
    store_sd_bb = func.append_basic_block("store.sd")

    # op64 write-back helpers
    op64_write_bb = func.append_basic_block("op64.write")

    # ===== ENTRY BLOCK =====
    with entry_bb.create_builder() as b:
        # Allocate register file: [32 x i64]
        regs_ty = ctx.types.array(i64, NUM_REGS)
        regs = b.alloca(regs_ty, name="regs")

        # Allocate PC
        pc_ptr = b.alloca(i64, name="pc")

        # Allocate VM stack
        stack_ty = ctx.types.array(i8, VM_STACK_SIZE)
        stack = b.alloca(stack_ty, name="stack")

        # Initialize all regs to 0
        for i in range(NUM_REGS):
            reg_ptr = b.gep(i64, regs, [i64.constant(i)], f"reg.{i}.init")
            b.store(i64.constant(0), reg_ptr)

        # Set SP (x2) to top of stack - 8
        sp_ptr = b.gep(i64, regs, [i64.constant(2)], "sp.ptr")
        stack_top = b.gep(i8, stack, [i64.constant(VM_STACK_SIZE - 8)], "stack.top")
        stack_top_int = b.ptrtoint(stack_top, i64, "stack.top.int")
        b.store(stack_top_int, sp_ptr)

        # Copy args into a0-a7 (regs[10..17])
        # We load from the args array (ptr to i64 array)
        # args_param is ptr to array of i64
        # We'll do a fixed copy of up to 8 args — the caller knows how many
        # For simplicity, copy based on bc_len-independent iteration
        # Actually, we just unconditionally copy 8 slots (extras are 0)
        for i in range(8):
            arg_ptr = b.gep(i64, args_param, [i64.constant(i)], f"arg.{i}.ptr")
            arg_val = b.load(i64, arg_ptr, f"arg.{i}")
            reg_a_ptr = b.gep(i64, regs, [i64.constant(10 + i)], f"a{i}.ptr")
            b.store(arg_val, reg_a_ptr)

        # PC = 0
        b.store(i64.constant(0), pc_ptr)
        b.br(loop_header_bb)

    # ===== LOOP HEADER =====
    with loop_header_bb.create_builder() as b:
        pc_val = b.load(i64, pc_ptr, "pc.val")
        done = b.icmp(llvm.IntPredicate.UGE, pc_val, bc_len_param, "done")
        b.cond_br(done, exit_bb, loop_body_bb)

    # ===== LOOP BODY: fetch + decode opcode + dispatch =====
    with loop_body_bb.create_builder() as b:
        pc_val = b.load(i64, pc_ptr, "pc")
        # Fetch instruction: load i32 from bytecode[pc]
        inst_ptr = b.gep(i8, bc_param, [pc_val], "inst.ptr")
        inst = b.load(i32, inst_ptr, "inst")

        # Decode opcode: (inst >> 2) & 0x1F
        inst_shr2 = b.lshr(inst, i32.constant(2), "inst.shr2")
        opcode_val = b.and_(inst_shr2, i32.constant(0x1F), "opcode")

        # Switch on opcode
        sw = b.switch_(opcode_val, default_bb, 9)
        sw.add_case(i32.constant(Opcode.OP64), handler_op64_bb)
        sw.add_case(i32.constant(Opcode.IMM64), handler_imm64_bb)
        sw.add_case(i32.constant(Opcode.LUI), handler_lui_bb)
        sw.add_case(i32.constant(Opcode.AUIPC), handler_auipc_bb)
        sw.add_case(i32.constant(Opcode.BRANCH), handler_branch_bb)
        sw.add_case(i32.constant(Opcode.JAL), handler_jal_bb)
        sw.add_case(i32.constant(Opcode.LOAD), handler_load_bb)
        sw.add_case(i32.constant(Opcode.STORE), handler_store_bb)
        sw.add_case(i32.constant(Opcode.SYSTEM), handler_system_bb)

    # Helper: decode common fields from instruction.
    # Since we can't share SSA values across blocks, each handler re-loads pc
    # and re-fetches the instruction. This is simpler and correct.

    def _fetch_inst(b):
        """Fetch the current instruction within a handler block."""
        pc = b.load(i64, pc_ptr, "h.pc")
        iptr = b.gep(i8, bc_param, [pc], "h.inst.ptr")
        return b.load(i32, iptr, "h.inst")

    def _decode_rd(b, inst):
        return b.and_(b.lshr(inst, i32.constant(7), "rd.shr"), i32.constant(0x1F), "rd")

    def _decode_funct3(b, inst):
        return b.and_(b.lshr(inst, i32.constant(12), "f3.shr"), i32.constant(0x7), "funct3")

    def _decode_rs1(b, inst):
        return b.and_(b.lshr(inst, i32.constant(15), "rs1.shr"), i32.constant(0x1F), "rs1")

    def _decode_rs2(b, inst):
        return b.and_(b.lshr(inst, i32.constant(20), "rs2.shr"), i32.constant(0x1F), "rs2")

    def _decode_funct7(b, inst):
        return b.and_(b.lshr(inst, i32.constant(25), "f7.shr"), i32.constant(0x7F), "funct7")

    def _read_reg(b, idx_i32):
        """Read register value: regs[idx]."""
        idx64 = b.zext(idx_i32, i64, "reg.idx")
        rptr = b.gep(i64, regs, [idx64], "reg.ptr")
        return b.load(i64, rptr, "reg.val")

    def _write_reg(b, idx_i32, val_i64, skip_zero_bb):
        """Write register value, skipping if rd == 0. Returns (write_bb, after_bb)."""
        # Check rd != 0
        is_zero = b.icmp(llvm.IntPredicate.EQ, idx_i32, i32.constant(0), "rd.is.zero")
        write_bb = func.append_basic_block("write.reg")
        b.cond_br(is_zero, skip_zero_bb, write_bb)

        with write_bb.create_builder() as wb:
            idx64 = wb.zext(idx_i32, i64, "wr.idx")
            rptr = wb.gep(i64, regs, [idx64], "wr.ptr")
            wb.store(val_i64, rptr)
            wb.br(skip_zero_bb)

    def _advance_pc_and_loop(b):
        """Increment PC by 4 and branch back to loop header."""
        pc = b.load(i64, pc_ptr, "adv.pc")
        pc_next = b.add(pc, i64.constant(4), "pc.next")
        b.store(pc_next, pc_ptr)
        b.br(loop_header_bb)

    # ===== HANDLER: OP64 (R-type register-register) =====
    with handler_op64_bb.create_builder() as b:
        inst_val = _fetch_inst(b)
        rd = _decode_rd(b, inst_val)
        funct3 = _decode_funct3(b, inst_val)
        rs1_idx = _decode_rs1(b, inst_val)
        rs2_idx = _decode_rs2(b, inst_val)
        funct7 = _decode_funct7(b, inst_val)

        rs1_val = _read_reg(b, rs1_idx)
        rs2_val = _read_reg(b, rs2_idx)

        # Dispatch key: (funct7 << 3) | funct3
        f7_shl = b.shl(funct7, i32.constant(3), "f7.shl")
        dispatch_key = b.or_(f7_shl, funct3, "op64.key")

        # Switch on dispatch key
        sw = b.switch_(dispatch_key, op64_done_bb, 15)
        # funct7=0: ADD(0), SLL(1), SLT(2), SLTU(3), XOR(4), SRL(5), OR(6), AND(7)
        sw.add_case(i32.constant(0), op64_add_bb)      # 0b0000000_000 = 0
        sw.add_case(i32.constant(1), op64_sll_bb)      # 0b0000000_001 = 1
        sw.add_case(i32.constant(2), op64_slt_bb)      # 0b0000000_010 = 2
        sw.add_case(i32.constant(3), op64_sltu_bb)     # 0b0000000_011 = 3
        sw.add_case(i32.constant(4), op64_xor_bb)      # 0b0000000_100 = 4
        sw.add_case(i32.constant(5), op64_srl_bb)      # 0b0000000_101 = 5
        sw.add_case(i32.constant(6), op64_or_bb)       # 0b0000000_110 = 6
        sw.add_case(i32.constant(7), op64_and_bb)      # 0b0000000_111 = 7
        # funct7=0x20: SUB(0), SRA(5)
        sw.add_case(i32.constant(0x100), op64_sub_bb)   # 0b0100000_000 = 256
        sw.add_case(i32.constant(0x105), op64_sra_bb)   # 0b0100000_101 = 261
        # funct7=1 (MULDIV): MUL(0), DIV(4), DIVU(5), REM(6), REMU(7)
        sw.add_case(i32.constant(0x08), op64_mul_bb)    # 0b0000001_000 = 8
        sw.add_case(i32.constant(0x0C), op64_div_bb)    # 0b0000001_100 = 12
        sw.add_case(i32.constant(0x0D), op64_divu_bb)   # 0b0000001_101 = 13
        sw.add_case(i32.constant(0x0E), op64_rem_bb)    # 0b0000001_110 = 14
        sw.add_case(i32.constant(0x0F), op64_remu_bb)   # 0b0000001_111 = 15

    # We need a PHI in op64_done to collect the result. But building PHI
    # across many blocks is complex. Instead, use an alloca for the result.
    # Actually, let's use the op64_write block with a memory-based approach.

    # Create a result alloca in entry (re-enter entry builder? No, entry is terminated.)
    # We'll add a dedicated alloca block. But that breaks things.
    # Simpler approach: each sub-handler writes directly to regs[rd] and branches to op64_done.
    # We pass rd via an alloca too.

    # Actually the cleanest approach: each sub-handler stores result to a shared alloca,
    # then op64_done reads it and writes to regs[rd].
    # But we can't add allocas after entry is terminated.

    # Let me use a simpler approach: each sub-handler writes to regs[rd] directly
    # (with the rd==0 check), then advances PC and loops.

    # Problem: rd is defined in handler_op64_bb, not visible in sub-handlers.
    # Solution: each sub-handler re-decodes from the instruction.
    # Or: store rd in an alloca. Let's add allocas to entry.

    # Actually, the entry block already has its builder closed. We need to add
    # allocas before the terminator. Let me restructure: add allocas in entry
    # before the br.

    # ... This is getting complex. Let me use a different strategy:
    # Store shared decode results (rd, rs1_val, rs2_val) in allocas created in entry.

    # I'll rebuild entry with the extra allocas.
    # WAIT — entry's builder is closed. We can insert before the terminator.

    # Actually, let's just re-decode in each sub-handler. It's a few extra
    # instructions but keeps things simple and correct.

    def _op64_sub_handler(bb, compute_fn, name):
        """Build an op64 sub-handler that computes result and writes to rd."""
        with bb.create_builder() as b:
            inst_val = _fetch_inst(b)
            rd = _decode_rd(b, inst_val)
            rs1_idx = _decode_rs1(b, inst_val)
            rs2_idx = _decode_rs2(b, inst_val)
            rs1_val = _read_reg(b, rs1_idx)
            rs2_val = _read_reg(b, rs2_idx)
            result = compute_fn(b, rs1_val, rs2_val)
            # Write result to regs[rd] if rd != 0
            is_zero = b.icmp(llvm.IntPredicate.EQ, rd, i32.constant(0), "rd.z")
            wr_bb = func.append_basic_block(f"{name}.wr")
            b.cond_br(is_zero, op64_done_bb, wr_bb)
            with wr_bb.create_builder() as wb:
                idx64 = wb.zext(rd, i64, "wr.idx")
                rptr = wb.gep(i64, regs, [idx64], "wr.ptr")
                wb.store(result, rptr)
                wb.br(op64_done_bb)

    _op64_sub_handler(op64_add_bb, lambda b, a, c: b.add(a, c, "res"), "add")
    _op64_sub_handler(op64_sub_bb, lambda b, a, c: b.sub(a, c, "res"), "sub")
    _op64_sub_handler(op64_sll_bb, lambda b, a, c: b.shl(a, c, "res"), "sll")
    _op64_sub_handler(op64_xor_bb, lambda b, a, c: b.xor(a, c, "res"), "xor")
    _op64_sub_handler(op64_srl_bb, lambda b, a, c: b.lshr(a, c, "res"), "srl")
    _op64_sub_handler(op64_sra_bb, lambda b, a, c: b.ashr(a, c, "res"), "sra")
    _op64_sub_handler(op64_or_bb, lambda b, a, c: b.or_(a, c, "res"), "or")
    _op64_sub_handler(op64_and_bb, lambda b, a, c: b.and_(a, c, "res"), "and")
    _op64_sub_handler(op64_mul_bb, lambda b, a, c: b.mul(a, c, "res"), "mul")
    _op64_sub_handler(op64_div_bb, lambda b, a, c: b.sdiv(a, c, "res"), "div")
    _op64_sub_handler(op64_divu_bb, lambda b, a, c: b.udiv(a, c, "res"), "divu")
    _op64_sub_handler(op64_rem_bb, lambda b, a, c: b.srem(a, c, "res"), "rem")
    _op64_sub_handler(op64_remu_bb, lambda b, a, c: b.urem(a, c, "res"), "remu")

    # SLT: set less than (signed)
    def _slt_compute(b, a, c):
        cmp = b.icmp(llvm.IntPredicate.SLT, a, c, "slt.cmp")
        return b.zext(cmp, i64, "slt.res")
    _op64_sub_handler(op64_slt_bb, _slt_compute, "slt")

    # SLTU: set less than (unsigned)
    def _sltu_compute(b, a, c):
        cmp = b.icmp(llvm.IntPredicate.ULT, a, c, "sltu.cmp")
        return b.zext(cmp, i64, "sltu.res")
    _op64_sub_handler(op64_sltu_bb, _sltu_compute, "sltu")

    # op64.done: advance PC, loop
    with op64_done_bb.create_builder() as b:
        _advance_pc_and_loop(b)

    # ===== HANDLER: IMM64 (I-type immediate) =====
    with handler_imm64_bb.create_builder() as b:
        inst_val = _fetch_inst(b)
        funct3 = _decode_funct3(b, inst_val)

        sw = b.switch_(funct3, imm64_done_bb, 8)
        sw.add_case(i32.constant(0), imm64_addi_bb)
        sw.add_case(i32.constant(1), imm64_slli_bb)
        sw.add_case(i32.constant(2), imm64_slti_bb)
        sw.add_case(i32.constant(3), imm64_sltiu_bb)
        sw.add_case(i32.constant(4), imm64_xori_bb)
        sw.add_case(i32.constant(5), imm64_srxi_bb)
        sw.add_case(i32.constant(6), imm64_ori_bb)
        sw.add_case(i32.constant(7), imm64_andi_bb)

    def _decode_i_imm(b, inst_val):
        """Decode and sign-extend I-type immediate."""
        raw = b.ashr(inst_val, i32.constant(20), "imm.raw")
        return b.sext(raw, i64, "imm")

    def _imm64_sub_handler(bb, compute_fn, name):
        """Build an imm64 sub-handler."""
        with bb.create_builder() as b:
            inst_val = _fetch_inst(b)
            rd = _decode_rd(b, inst_val)
            rs1_idx = _decode_rs1(b, inst_val)
            rs1_val = _read_reg(b, rs1_idx)
            imm = _decode_i_imm(b, inst_val)
            result = compute_fn(b, rs1_val, imm)
            is_zero = b.icmp(llvm.IntPredicate.EQ, rd, i32.constant(0), "rd.z")
            wr_bb = func.append_basic_block(f"imm.{name}.wr")
            b.cond_br(is_zero, imm64_done_bb, wr_bb)
            with wr_bb.create_builder() as wb:
                idx64 = wb.zext(rd, i64, "wr.idx")
                rptr = wb.gep(i64, regs, [idx64], "wr.ptr")
                wb.store(result, rptr)
                wb.br(imm64_done_bb)

    _imm64_sub_handler(imm64_addi_bb, lambda b, v, i: b.add(v, i, "res"), "addi")
    _imm64_sub_handler(imm64_xori_bb, lambda b, v, i: b.xor(v, i, "res"), "xori")
    _imm64_sub_handler(imm64_ori_bb, lambda b, v, i: b.or_(v, i, "res"), "ori")
    _imm64_sub_handler(imm64_andi_bb, lambda b, v, i: b.and_(v, i, "res"), "andi")

    # SLLI: shift left by shamt (lower 6 bits of imm)
    def _slli_compute(b, v, imm):
        shamt = b.and_(imm, i64.constant(0x3F), "shamt")
        return b.shl(v, shamt, "res")
    _imm64_sub_handler(imm64_slli_bb, _slli_compute, "slli")

    # SRXI: SRLI or SRAI based on imm[10] (bit 30 of instruction)
    with imm64_srxi_bb.create_builder() as b:
        inst_val = _fetch_inst(b)
        rd = _decode_rd(b, inst_val)
        rs1_idx = _decode_rs1(b, inst_val)
        rs1_val = _read_reg(b, rs1_idx)
        # shamt = imm[5:0], arithmetic flag = imm[10] = bit 30
        imm_raw = b.lshr(inst_val, i32.constant(20), "srx.imm.raw")
        shamt_i32 = b.and_(imm_raw, i32.constant(0x3F), "srx.shamt32")
        shamt = b.zext(shamt_i32, i64, "srx.shamt")
        is_arith = b.and_(inst_val, i32.constant(1 << 30), "srx.arith.bit")
        is_sra = b.icmp(llvm.IntPredicate.NE, is_arith, i32.constant(0), "is.sra")

        srl_bb = func.append_basic_block("srxi.srl")
        sra_bb = func.append_basic_block("srxi.sra")
        srxi_merge_bb = func.append_basic_block("srxi.merge")
        b.cond_br(is_sra, sra_bb, srl_bb)

        with srl_bb.create_builder() as sb:
            srl_res = sb.lshr(rs1_val, shamt, "srl.res")
            sb.br(srxi_merge_bb)

        with sra_bb.create_builder() as sb:
            sra_res = sb.ashr(rs1_val, shamt, "sra.res")
            sb.br(srxi_merge_bb)

        with srxi_merge_bb.create_builder() as mb:
            phi = mb.phi(i64, "srx.result")
            phi.add_incoming(srl_res, srl_bb)
            phi.add_incoming(sra_res, sra_bb)
            is_zero = mb.icmp(llvm.IntPredicate.EQ, rd, i32.constant(0), "rd.z")
            srxi_wr_bb = func.append_basic_block("srxi.wr")
            mb.cond_br(is_zero, imm64_done_bb, srxi_wr_bb)

            with srxi_wr_bb.create_builder() as wb:
                idx64 = wb.zext(rd, i64, "wr.idx")
                rptr = wb.gep(i64, regs, [idx64], "wr.ptr")
                wb.store(phi, rptr)
                wb.br(imm64_done_bb)

    # SLTI: set less than immediate (signed)
    def _slti_compute(b, v, imm):
        cmp = b.icmp(llvm.IntPredicate.SLT, v, imm, "slti.cmp")
        return b.zext(cmp, i64, "slti.res")
    _imm64_sub_handler(imm64_slti_bb, _slti_compute, "slti")

    # SLTIU: set less than immediate (unsigned)
    def _sltiu_compute(b, v, imm):
        cmp = b.icmp(llvm.IntPredicate.ULT, v, imm, "sltiu.cmp")
        return b.zext(cmp, i64, "sltiu.res")
    _imm64_sub_handler(imm64_sltiu_bb, _sltiu_compute, "sltiu")

    # imm64.done: advance PC, loop
    with imm64_done_bb.create_builder() as b:
        _advance_pc_and_loop(b)

    # ===== HANDLER: LUI =====
    with handler_lui_bb.create_builder() as b:
        inst_val = _fetch_inst(b)
        rd = _decode_rd(b, inst_val)
        # LUI: rd = sign_extend(imm[31:12] << 12)
        # Upper 20 bits of instruction, shifted left 12
        imm_raw = b.and_(inst_val, i32.constant(0xFFFFF000), "lui.imm32")
        # Sign extend to 64 bit
        imm64 = b.sext(imm_raw, i64, "lui.imm64")
        is_zero = b.icmp(llvm.IntPredicate.EQ, rd, i32.constant(0), "rd.z")
        lui_wr_bb = func.append_basic_block("lui.wr")
        b.cond_br(is_zero, loop_header_bb, lui_wr_bb)

        with lui_wr_bb.create_builder() as wb:
            idx64 = wb.zext(rd, i64, "wr.idx")
            rptr = wb.gep(i64, regs, [idx64], "wr.ptr")
            wb.store(imm64, rptr)
            _advance_pc_and_loop(wb)

    # ===== HANDLER: AUIPC =====
    with handler_auipc_bb.create_builder() as b:
        inst_val = _fetch_inst(b)
        rd = _decode_rd(b, inst_val)
        imm_raw = b.and_(inst_val, i32.constant(0xFFFFF000), "auipc.imm32")
        imm64 = b.sext(imm_raw, i64, "auipc.imm64")
        pc_val = b.load(i64, pc_ptr, "auipc.pc")
        result = b.add(pc_val, imm64, "auipc.res")
        is_zero = b.icmp(llvm.IntPredicate.EQ, rd, i32.constant(0), "rd.z")
        auipc_wr_bb = func.append_basic_block("auipc.wr")
        b.cond_br(is_zero, loop_header_bb, auipc_wr_bb)

        with auipc_wr_bb.create_builder() as wb:
            idx64 = wb.zext(rd, i64, "wr.idx")
            rptr = wb.gep(i64, regs, [idx64], "wr.ptr")
            wb.store(result, rptr)
            _advance_pc_and_loop(wb)

    # ===== HANDLER: BRANCH =====
    with handler_branch_bb.create_builder() as b:
        inst_val = _fetch_inst(b)
        funct3 = _decode_funct3(b, inst_val)
        rs1_idx = _decode_rs1(b, inst_val)
        rs2_idx = _decode_rs2(b, inst_val)
        rs1_val = _read_reg(b, rs1_idx)
        rs2_val = _read_reg(b, rs2_idx)

        # Decode B-type immediate
        # bit_11 = (inst >> 7) & 1
        bit_11 = b.and_(b.lshr(inst_val, i32.constant(7), "b.s7"), i32.constant(1), "b.11")
        # bits_4_1 = (inst >> 8) & 0xF
        bits_4_1 = b.and_(b.lshr(inst_val, i32.constant(8), "b.s8"), i32.constant(0xF), "b.41")
        # bits_10_5 = (inst >> 25) & 0x3F
        bits_10_5 = b.and_(b.lshr(inst_val, i32.constant(25), "b.s25"), i32.constant(0x3F), "b.105")
        # bit_12 = (inst >> 31) & 1
        bit_12 = b.lshr(inst_val, i32.constant(31), "b.12")

        # Reconstruct: (bit_12 << 12) | (bit_11 << 11) | (bits_10_5 << 5) | (bits_4_1 << 1)
        imm = b.or_(
            b.or_(
                b.shl(bit_12, i32.constant(12), "b.12s"),
                b.shl(bit_11, i32.constant(11), "b.11s"),
                "b.hi"
            ),
            b.or_(
                b.shl(bits_10_5, i32.constant(5), "b.105s"),
                b.shl(bits_4_1, i32.constant(1), "b.41s"),
                "b.lo"
            ),
            "b.imm.raw"
        )
        # Sign extend from bit 12: if bit 12 set, OR with 0xFFFFE000
        sign_mask = b.shl(bit_12, i32.constant(12), "b.sign.check")
        needs_ext = b.icmp(llvm.IntPredicate.NE, sign_mask, i32.constant(0), "b.needs.ext")
        ext_val = b.select(needs_ext, i32.constant(0xFFFFE000 & 0xFFFFFFFF), i32.constant(0), "b.ext")
        imm_signed = b.or_(imm, ext_val, "b.imm.signed")
        imm64_val = b.sext(imm_signed, i64, "b.imm64")

        sw = b.switch_(funct3, branch_not_taken_bb, 6)
        sw.add_case(i32.constant(0), branch_beq_bb)   # BEQ
        sw.add_case(i32.constant(1), branch_bne_bb)   # BNE
        sw.add_case(i32.constant(4), branch_blt_bb)   # BLT
        sw.add_case(i32.constant(5), branch_bge_bb)   # BGE
        sw.add_case(i32.constant(6), branch_bltu_bb)  # BLTU
        sw.add_case(i32.constant(7), branch_bgeu_bb)  # BGEU

    # Branch condition sub-handlers — each computes the condition and branches
    # to taken/not_taken. We need to pass the immediate through.
    # Since imm64_val is defined in handler_branch_bb, it's not directly
    # available in sub-handler blocks. We'll use an alloca.

    # ... But allocas can only be added in entry which is already terminated.
    # Alternative: re-decode the immediate in taken/not_taken.
    # Or: store to pc_ptr directly in each sub-handler.

    # Simplest approach: each sub-handler re-decodes and sets PC directly.

    def _branch_sub_handler(bb, pred, name):
        with bb.create_builder() as b:
            inst_val = _fetch_inst(b)
            rs1_idx = _decode_rs1(b, inst_val)
            rs2_idx = _decode_rs2(b, inst_val)
            rs1_v = _read_reg(b, rs1_idx)
            rs2_v = _read_reg(b, rs2_idx)
            cond = b.icmp(pred, rs1_v, rs2_v, f"{name}.cond")

            # Re-decode B-type immediate
            b11 = b.and_(b.lshr(inst_val, i32.constant(7), ""), i32.constant(1), "")
            b41 = b.and_(b.lshr(inst_val, i32.constant(8), ""), i32.constant(0xF), "")
            b105 = b.and_(b.lshr(inst_val, i32.constant(25), ""), i32.constant(0x3F), "")
            b12 = b.lshr(inst_val, i32.constant(31), "")
            raw = b.or_(
                b.or_(b.shl(b12, i32.constant(12), ""), b.shl(b11, i32.constant(11), ""), ""),
                b.or_(b.shl(b105, i32.constant(5), ""), b.shl(b41, i32.constant(1), ""), ""),
                ""
            )
            sm = b.shl(b12, i32.constant(12), "")
            ne = b.icmp(llvm.IntPredicate.NE, sm, i32.constant(0), "")
            ev = b.select(ne, i32.constant(0xFFFFE000 & 0xFFFFFFFF), i32.constant(0), "")
            imm_s = b.or_(raw, ev, "")
            imm_64 = b.sext(imm_s, i64, "b.off")

            taken_bb_local = func.append_basic_block(f"{name}.taken")
            nottaken_bb_local = func.append_basic_block(f"{name}.nottaken")
            b.cond_br(cond, taken_bb_local, nottaken_bb_local)

            # Taken: pc += imm
            with taken_bb_local.create_builder() as tb:
                pc = tb.load(i64, pc_ptr, "t.pc")
                tb.store(tb.add(pc, imm_64, "t.npc"), pc_ptr)
                tb.br(loop_header_bb)

            # Not taken: pc += 4
            with nottaken_bb_local.create_builder() as nb:
                _advance_pc_and_loop(nb)

    _branch_sub_handler(branch_beq_bb, llvm.IntPredicate.EQ, "beq")
    _branch_sub_handler(branch_bne_bb, llvm.IntPredicate.NE, "bne")
    _branch_sub_handler(branch_blt_bb, llvm.IntPredicate.SLT, "blt")
    _branch_sub_handler(branch_bge_bb, llvm.IntPredicate.SGE, "bge")
    _branch_sub_handler(branch_bltu_bb, llvm.IntPredicate.ULT, "bltu")
    _branch_sub_handler(branch_bgeu_bb, llvm.IntPredicate.UGE, "bgeu")

    # branch.taken and branch.not_taken are unused now (each sub-handler has its own)
    with branch_taken_bb.create_builder() as b:
        b.br(loop_header_bb)  # unreachable fallback
    with branch_not_taken_bb.create_builder() as b:
        _advance_pc_and_loop(b)

    # ===== HANDLER: JAL (J-type) =====
    with handler_jal_bb.create_builder() as b:
        inst_val = _fetch_inst(b)
        rd = _decode_rd(b, inst_val)
        # Decode J-type immediate
        # imm_field = (inst >> 12) & 0xFFFFF
        imm_field = b.and_(b.lshr(inst_val, i32.constant(12), ""), i32.constant(0xFFFFF), "j.imm.field")
        # bits_19_12 = imm_field & 0xFF
        bits_19_12 = b.and_(imm_field, i32.constant(0xFF), "j.1912")
        # bit_11 = (imm_field >> 8) & 1
        bit_11 = b.and_(b.lshr(imm_field, i32.constant(8), ""), i32.constant(1), "j.11")
        # bits_10_1 = (imm_field >> 9) & 0x3FF
        bits_10_1 = b.and_(b.lshr(imm_field, i32.constant(9), ""), i32.constant(0x3FF), "j.101")
        # bit_20 = (imm_field >> 19) & 1
        bit_20 = b.and_(b.lshr(imm_field, i32.constant(19), ""), i32.constant(1), "j.20")

        # Reconstruct: (bit_20<<20) | (bits_19_12<<12) | (bit_11<<11) | (bits_10_1<<1)
        raw = b.or_(
            b.or_(
                b.shl(bit_20, i32.constant(20), ""),
                b.shl(bits_19_12, i32.constant(12), ""),
                ""
            ),
            b.or_(
                b.shl(bit_11, i32.constant(11), ""),
                b.shl(bits_10_1, i32.constant(1), ""),
                ""
            ),
            "j.raw"
        )
        # Sign extend from bit 20
        sm = b.shl(bit_20, i32.constant(20), "")
        ne = b.icmp(llvm.IntPredicate.NE, sm, i32.constant(0), "")
        ev = b.select(ne, i32.constant(0xFFE00000 & 0xFFFFFFFF), i32.constant(0), "")
        imm_s = b.or_(raw, ev, "j.imm.s")
        imm64_val = b.sext(imm_s, i64, "j.imm64")

        # rd = pc + 4 (link address) — only if rd != 0
        pc_val = b.load(i64, pc_ptr, "j.pc")
        link_addr = b.add(pc_val, i64.constant(4), "j.link")

        jal_wr_bb = func.append_basic_block("jal.wr")
        jal_set_pc_bb = func.append_basic_block("jal.setpc")
        is_zero = b.icmp(llvm.IntPredicate.EQ, rd, i32.constant(0), "rd.z")
        b.cond_br(is_zero, jal_set_pc_bb, jal_wr_bb)

        with jal_wr_bb.create_builder() as wb:
            idx64 = wb.zext(rd, i64, "wr.idx")
            rptr = wb.gep(i64, regs, [idx64], "wr.ptr")
            wb.store(link_addr, rptr)
            wb.br(jal_set_pc_bb)

        with jal_set_pc_bb.create_builder() as sb:
            pc2 = sb.load(i64, pc_ptr, "j.pc2")
            new_pc = sb.add(pc2, imm64_val, "j.npc")
            sb.store(new_pc, pc_ptr)
            sb.br(loop_header_bb)

    # ===== HANDLER: LOAD =====
    with handler_load_bb.create_builder() as b:
        inst_val = _fetch_inst(b)
        rd = _decode_rd(b, inst_val)
        funct3 = _decode_funct3(b, inst_val)
        rs1_idx = _decode_rs1(b, inst_val)
        rs1_val = _read_reg(b, rs1_idx)
        imm = _decode_i_imm(b, inst_val)
        addr = b.add(rs1_val, imm, "ld.addr")
        addr_ptr = b.inttoptr(addr, ptr, "ld.ptr")

        sw = b.switch_(funct3, load_done_bb, 4)
        sw.add_case(i32.constant(0), load_lb_bb)   # LB
        sw.add_case(i32.constant(1), load_lh_bb)   # LH
        sw.add_case(i32.constant(2), load_lw_bb)   # LW
        sw.add_case(i32.constant(3), load_ld_bb)   # LD

    def _load_sub_handler(bb, load_ty, sext, name):
        with bb.create_builder() as b:
            inst_val = _fetch_inst(b)
            rd = _decode_rd(b, inst_val)
            rs1_idx = _decode_rs1(b, inst_val)
            rs1_val = _read_reg(b, rs1_idx)
            imm = _decode_i_imm(b, inst_val)
            addr = b.add(rs1_val, imm, "addr")
            aptr = b.inttoptr(addr, ptr, "aptr")
            val = b.load(load_ty, aptr, "lval")
            if sext:
                val64 = b.sext(val, i64, "sext")
            else:
                val64 = b.zext(val, i64, "zext")
            is_zero = b.icmp(llvm.IntPredicate.EQ, rd, i32.constant(0), "rd.z")
            wr_bb_local = func.append_basic_block(f"ld.{name}.wr")
            b.cond_br(is_zero, load_done_bb, wr_bb_local)
            with wr_bb_local.create_builder() as wb:
                idx64 = wb.zext(rd, i64, "wr.idx")
                rptr = wb.gep(i64, regs, [idx64], "wr.ptr")
                wb.store(val64, rptr)
                wb.br(load_done_bb)

    _load_sub_handler(load_lb_bb, i8, True, "lb")
    _load_sub_handler(load_lh_bb, ctx.types.i16, True, "lh")
    _load_sub_handler(load_lw_bb, i32, True, "lw")

    # LD: load i64
    with load_ld_bb.create_builder() as b:
        inst_val = _fetch_inst(b)
        rd = _decode_rd(b, inst_val)
        rs1_idx = _decode_rs1(b, inst_val)
        rs1_val = _read_reg(b, rs1_idx)
        imm = _decode_i_imm(b, inst_val)
        addr = b.add(rs1_val, imm, "addr")
        aptr = b.inttoptr(addr, ptr, "aptr")
        val64 = b.load(i64, aptr, "lval64")
        is_zero = b.icmp(llvm.IntPredicate.EQ, rd, i32.constant(0), "rd.z")
        ld_wr_bb = func.append_basic_block("ld.ld.wr")
        b.cond_br(is_zero, load_done_bb, ld_wr_bb)
        with ld_wr_bb.create_builder() as wb:
            idx64 = wb.zext(rd, i64, "wr.idx")
            rptr = wb.gep(i64, regs, [idx64], "wr.ptr")
            wb.store(val64, rptr)
            wb.br(load_done_bb)

    with load_done_bb.create_builder() as b:
        _advance_pc_and_loop(b)

    # ===== HANDLER: STORE =====
    with handler_store_bb.create_builder() as b:
        inst_val = _fetch_inst(b)
        funct3 = _decode_funct3(b, inst_val)

        sw = b.switch_(funct3, loop_header_bb, 4)
        sw.add_case(i32.constant(0), store_sb_bb)
        sw.add_case(i32.constant(1), store_sh_bb)
        sw.add_case(i32.constant(2), store_sw_bb)
        sw.add_case(i32.constant(3), store_sd_bb)

    def _decode_s_imm_ir(b, inst_val):
        """Decode S-type immediate in IR."""
        imm_4_0 = b.and_(b.lshr(inst_val, i32.constant(7), ""), i32.constant(0x1F), "s.40")
        imm_11_5 = b.and_(b.lshr(inst_val, i32.constant(25), ""), i32.constant(0x7F), "s.115")
        raw = b.or_(b.shl(imm_11_5, i32.constant(5), ""), imm_4_0, "s.raw")
        # Sign extend from bit 11
        bit_11 = b.and_(b.lshr(raw, i32.constant(11), ""), i32.constant(1), "s.b11")
        ne = b.icmp(llvm.IntPredicate.NE, bit_11, i32.constant(0), "")
        ev = b.select(ne, i32.constant(0xFFFFF000 & 0xFFFFFFFF), i32.constant(0), "")
        imm_s = b.or_(raw, ev, "s.imm")
        return b.sext(imm_s, i64, "s.imm64")

    def _store_sub_handler(bb, store_ty, name):
        with bb.create_builder() as b:
            inst_val = _fetch_inst(b)
            rs1_idx = _decode_rs1(b, inst_val)
            rs2_idx = _decode_rs2(b, inst_val)
            rs1_val = _read_reg(b, rs1_idx)
            rs2_val = _read_reg(b, rs2_idx)
            imm = _decode_s_imm_ir(b, inst_val)
            addr = b.add(rs1_val, imm, "st.addr")
            aptr = b.inttoptr(addr, ptr, "st.ptr")
            if store_ty == i64:
                b.store(rs2_val, aptr)
            else:
                truncated = b.trunc(rs2_val, store_ty, f"st.{name}.trunc")
                b.store(truncated, aptr)
            _advance_pc_and_loop(b)

    _store_sub_handler(store_sb_bb, i8, "sb")
    _store_sub_handler(store_sh_bb, ctx.types.i16, "sh")
    _store_sub_handler(store_sw_bb, i32, "sw")
    _store_sub_handler(store_sd_bb, i64, "sd")

    # ===== HANDLER: SYSTEM (ECALL) =====
    with handler_system_bb.create_builder() as b:
        # Read a7 (reg 17) for syscall number
        a7_ptr = b.gep(i64, regs, [i64.constant(17)], "a7.ptr")
        a7_val = b.load(i64, a7_ptr, "a7.val")

        # Check for EXIT syscall
        is_exit = b.icmp(llvm.IntPredicate.EQ, a7_val, i64.constant(SYSCALL_EXIT), "is.exit")
        sys_check_host_bb = func.append_basic_block("sys.check.host")
        b.cond_br(is_exit, exit_bb, sys_check_host_bb)

        # Check for HOST_CALL syscall
        with sys_check_host_bb.create_builder() as hb:
            is_host = hb.icmp(llvm.IntPredicate.EQ, a7_val,
                              i64.constant(SYSCALL_HOST_CALL), "is.host")
            sys_host_call_bb = func.append_basic_block("sys.host.call")
            sys_other_bb = func.append_basic_block("sys.other")
            hb.cond_br(is_host, sys_host_call_bb, sys_other_bb)

        # HOST_CALL: read a0 (index), a1-a6 (args), call host_table[index]
        # Uses uniform signature: i64(i64, i64, i64, i64, i64, i64)
        with sys_host_call_bb.create_builder() as hb:
            # Read a0 = host function index
            a0_ptr_h = hb.gep(i64, regs, [i64.constant(10)], "hc.a0.ptr")
            host_idx = hb.load(i64, a0_ptr_h, "hc.idx")
            # Read a1-a6 as call arguments
            host_args = []
            for i in range(6):
                a_ptr = hb.gep(i64, regs, [i64.constant(11 + i)], f"hc.a{i+1}.ptr")
                a_val = hb.load(i64, a_ptr, f"hc.a{i+1}")
                host_args.append(a_val)
            # Load function pointer from host_table[index]
            fn_ptr = hb.gep(ptr, host_table_param, [host_idx], "hc.fn.ptr")
            fn_val = hb.load(ptr, fn_ptr, "hc.fn")
            # Call with uniform i64(i64,i64,i64,i64,i64,i64) signature
            host_fn_ty = ctx.types.function(i64, [i64, i64, i64, i64, i64, i64])
            result = hb.call(host_fn_ty, fn_val, host_args, "hc.result")
            # Store result into a0
            hb.store(result, a0_ptr_h)
            _advance_pc_and_loop(hb)

        # Other syscalls: just advance PC
        with sys_other_bb.create_builder() as sb:
            _advance_pc_and_loop(sb)

    # ===== DEFAULT HANDLER =====
    with default_bb.create_builder() as b:
        # Unknown opcode: advance PC and continue (graceful degradation)
        _advance_pc_and_loop(b)

    # ===== EXIT BLOCK =====
    with exit_bb.create_builder() as b:
        # Copy a0 (reg 10) to ret_slot
        a0_ptr = b.gep(i64, regs, [i64.constant(10)], "a0.ptr")
        a0_val = b.load(i64, a0_ptr, "a0.val")
        b.store(a0_val, ret_slot_param)
        b.ret_void()

    # ===== UNUSED WRITE BLOCK =====
    with op64_write_bb.create_builder() as b:
        b.br(op64_done_bb)  # unreachable fallback

    return func

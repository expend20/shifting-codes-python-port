"""Bytecode compiler: translates LLVM IR functions into VM bytecode.

Supports integer arithmetic, comparisons, branches, loads/stores, calls
(via HOST_CALL mechanism), and returns.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field

import llvm

from shifting_codes.riscybusiness_vm.isa import (
    Opcode, Funct3Op64, Funct7Op64, Funct3Imm64, Funct3Branch,
    Funct3Load, Funct3Store, RegIndex, ARG_REGS, TEMP_REGS,
    SYSCALL_EXIT, SYSCALL_HOST_CALL,
    encode_r_type, encode_i_type, encode_s_type, encode_b_type,
    encode_u_type, encode_j_type, pack_instruction,
)
from shifting_codes.utils.ir_helpers import demote_phi_to_stack, demote_regs_to_stack


# ---------------------------------------------------------------------------
# Register Allocator
# ---------------------------------------------------------------------------


class RegisterAllocator:
    """Maps LLVM Values to VM register indices.

    Function args → a0-a7, SSA temporaries → t0-t6, s0-s11.
    Supports per-block register recycling for block-local values.

    Alloca values are NOT assigned persistent registers. Instead, their SP
    offsets are tracked in ``_alloca_offsets`` and materialized on-demand
    via ``materialize_alloca`` when used as operands.
    """

    def __init__(self):
        self._map: dict[int, int] = {}  # hash(Value) -> register index
        self._persistent: set[int] = set()  # hashes of persistent values (args)
        self._available: list[int] = list(reversed(TEMP_REGS))  # free register stack
        self._used_local: dict[int, int] = {}  # reg -> hash (for non-persistent)
        self._alloca_offsets: dict[int, int] = {}  # hash(alloca) -> SP offset
        self._next_alloca_offset: int = 0  # running negative offset from initial SP
        self._gref_table_reg: int | None = None  # register holding global ref table base
        self._gref_indices: dict[str, int] = {}  # gv_name -> table index

    def register_alloca(self, value) -> int:
        """Register an alloca and return its SP offset (negative).

        Does NOT consume a register — the address is recomputed on use.
        """
        self._next_alloca_offset -= 8
        offset = self._next_alloca_offset
        self._alloca_offsets[hash(value)] = offset
        return offset

    def is_alloca(self, value) -> bool:
        return hash(value) in self._alloca_offsets

    def get_alloca_offset(self, value) -> int | None:
        return self._alloca_offsets.get(hash(value))

    @property
    def total_alloca_size(self) -> int:
        """Total bytes to subtract from SP for all allocas."""
        return -self._next_alloca_offset

    def setup_gref_table(self, table_arg_index: int) -> None:
        """Reserve an arg register to hold the global ref table base pointer."""
        self._gref_table_reg = ARG_REGS[table_arg_index]

    def register_gref(self, gv_name: str) -> int:
        """Register a global variable reference and return its table index."""
        if gv_name not in self._gref_indices:
            self._gref_indices[gv_name] = len(self._gref_indices)
        return self._gref_indices[gv_name]

    @property
    def gref_table_reg(self) -> int | None:
        return self._gref_table_reg

    @property
    def gref_names(self) -> list[str]:
        """Ordered list of global ref names matching table indices."""
        return [name for name, _ in sorted(self._gref_indices.items(),
                                            key=lambda x: x[1])]

    @property
    def gref_count(self) -> int:
        return len(self._gref_indices)

    def assign_arg(self, value, index: int) -> int:
        """Assign a function argument to a0-a7."""
        if index >= len(ARG_REGS):
            raise ValueError(f"Too many arguments: {index + 1} > {len(ARG_REGS)}")
        reg = ARG_REGS[index]
        h = hash(value)
        self._map[h] = reg
        self._persistent.add(h)
        return reg

    def mark_persistent(self, value) -> None:
        """Mark a value as needing its register across block boundaries."""
        h = hash(value)
        self._persistent.add(h)
        # Remove from _used_local if already assigned (so reset_block_locals
        # won't free this register)
        for reg, hh in list(self._used_local.items()):
            if hh == h:
                del self._used_local[reg]
                break

    def get_or_assign(self, value) -> int:
        """Get existing register or assign a new temporary.

        If all registers are in use, spills the oldest non-persistent local
        register to make room. The spilled value will need to be reloaded
        from the stack if used again.
        """
        h = hash(value)
        if h in self._map:
            return self._map[h]
        if not self._available:
            # Spill: evict the first non-persistent local to free a register
            if self._used_local:
                evict_reg = next(iter(self._used_local))
                evict_hash = self._used_local[evict_reg]
                del self._map[evict_hash]
                del self._used_local[evict_reg]
                self._available.append(evict_reg)
            else:
                raise ValueError(
                    f"Register allocation overflow: all {len(TEMP_REGS)} temporaries "
                    f"in use by persistent values."
                )
        reg = self._available.pop()
        self._map[h] = reg
        if h not in self._persistent:
            self._used_local[reg] = h
        return reg

    def get(self, value) -> int | None:
        """Get register for a value if already assigned."""
        return self._map.get(hash(value))

    def has(self, value) -> bool:
        return hash(value) in self._map

    def free_value(self, value) -> None:
        """Free the register held by a non-persistent value.

        Used for eager reclamation when a value's last use has passed.
        """
        self.free_by_hash(hash(value))

    def free_by_hash(self, h: int) -> None:
        """Free the register held by a non-persistent value (by hash).

        No-op if the hash is persistent or not in the register map.
        """
        if h in self._persistent:
            return
        if h not in self._map:
            return
        reg = self._map[h]
        del self._map[h]
        if reg in self._used_local:
            del self._used_local[reg]
        self._available.append(reg)

    def reset_block_locals(self) -> None:
        """Free registers used by block-local (non-persistent) values.

        Call this at block boundaries to reclaim registers for values
        that were only live within the previous block.
        """
        for reg, h in list(self._used_local.items()):
            del self._map[h]
            self._available.append(reg)
        self._used_local.clear()


# ---------------------------------------------------------------------------
# Block Layout
# ---------------------------------------------------------------------------


@dataclass
class BlockLayout:
    """Tracks byte offsets for each basic block in the linearized bytecode."""
    block_offsets: dict[int, int] = field(default_factory=dict)  # hash(bb) -> byte offset
    block_order: list = field(default_factory=list)  # ordered basic blocks

    def set_offset(self, bb, offset: int):
        self.block_offsets[hash(bb)] = offset

    def get_offset(self, bb) -> int:
        return self.block_offsets[hash(bb)]

    def has(self, bb) -> bool:
        return hash(bb) in self.block_offsets


# ---------------------------------------------------------------------------
# Instruction encoding helpers
# ---------------------------------------------------------------------------


def _fits_i12(val: int) -> bool:
    """Check if value fits in a signed 12-bit immediate."""
    return -2048 <= val <= 2047


def _encode_load_imm(rd: int, value: int) -> list[int]:
    """Encode loading an immediate value into a register.

    For small values: ADDI rd, x0, imm
    For larger values: LUI rd, upper20 + ADDI rd, rd, lower12
    """
    value = value & 0xFFFFFFFFFFFFFFFF  # 64-bit mask

    if _fits_i12(value) or _fits_i12(value - 0x10000000000000000 if value > 0x7FFFFFFFFFFFFFFF else value):
        # Small immediate: ADDI rd, x0, imm
        imm = value & 0xFFF
        # Sign extend check for proper ADDI encoding
        if value > 0x7FFFFFFFFFFFFFFF:
            signed_val = value - 0x10000000000000000
        else:
            signed_val = value
        return [encode_i_type(Opcode.IMM64, rd, Funct3Imm64.ADDI, RegIndex.ZERO, signed_val & 0xFFF)]

    # 32-bit range: LUI + ADDI
    val32 = value & 0xFFFFFFFF
    lower12 = val32 & 0xFFF
    # Sign-extend lower12 for addition correction
    if lower12 >= 0x800:
        upper20 = ((val32 + 0x1000) >> 12) & 0xFFFFF
    else:
        upper20 = (val32 >> 12) & 0xFFFFF

    insns = [encode_u_type(Opcode.LUI, rd, upper20)]
    if lower12 != 0:
        insns.append(encode_i_type(Opcode.IMM64, rd, Funct3Imm64.ADDI, rd, lower12))
    return insns


def _get_type_size(ty) -> int:
    """Get the size in bytes of an LLVM type (for GEP stride computation)."""
    kind = ty.kind
    if kind == llvm.TypeKind.Integer:
        return (ty.int_width + 7) // 8
    if kind == llvm.TypeKind.Pointer:
        return 8  # 64-bit pointers
    if kind == llvm.TypeKind.Array:
        return ty.array_length * _get_type_size(ty.element_type)
    raise ValueError(f"Cannot compute size for type kind: {kind}")


def _scale_index(rd: int, rs_idx: int, elem_size: int) -> list[int]:
    """Emit instructions to scale an index register by element size into rd.

    For power-of-2 sizes, uses SLLI. Result is in rd.
    """
    if elem_size == 1:
        if rd != rs_idx:
            return [encode_i_type(Opcode.IMM64, rd, Funct3Imm64.ADDI, rs_idx, 0)]
        return []
    shift = (elem_size).bit_length() - 1
    if (1 << shift) != elem_size:
        raise ValueError(f"Non-power-of-2 element size {elem_size} not supported")
    return [encode_i_type(Opcode.IMM64, rd, Funct3Imm64.SLLI, rs_idx, shift)]


def _prepare_operand(
    value,
    reg_alloc: RegisterAllocator,
    global_refs: list[str] | None = None,
    global_ref_map: dict[str, int] | None = None,
    num_params: int = 0,
) -> tuple[int, list[int]]:
    """Ensure a value is in a register, emitting load-immediate for constants.

    Returns (register_index, prefix_instructions).
    For SSA values: returns the assigned register with no extra instructions.
    For constants: assigns a register and emits load-immediate into it.
    For global variables: maps to arg register a(num_params + i), pre-loaded
    by the interpreter stub.
    """
    # Alloca values: materialize address as SP + effective_offset into a
    # temp register.  SP was adjusted by total_alloca_size in the prologue,
    # so effective_offset = total_alloca_size + alloca_offset (which is
    # always non-negative).
    alloca_off = reg_alloc.get_alloca_offset(value)
    if alloca_off is not None:
        effective = reg_alloc.total_alloca_size + alloca_off  # >= 0
        reg = reg_alloc.get_or_assign(value)
        if _fits_i12(effective):
            insns = [encode_i_type(Opcode.IMM64, reg, Funct3Imm64.ADDI,
                                    RegIndex.SP, effective & 0xFFF)]
        else:
            insns = _encode_load_imm(reg, effective)
            insns.append(encode_r_type(Opcode.OP64, reg, Funct3Op64.ADD,
                                        RegIndex.SP, reg, Funct7Op64.NORMAL))
        return reg, insns
    const_val = _get_const_int_value(value)
    if const_val is not None:
        reg = reg_alloc.get_or_assign(value)
        insns = _encode_load_imm(reg, const_val)
        return reg, insns
    # Handle global variable references via the global ref table.
    # The table base address is in a dedicated arg register; each global's
    # address is at table[i] (i.e., base + i*8).  We load it on demand
    # into a temp register.
    if (global_refs is not None and global_ref_map is not None
            and _is_global_var(value)):
        gv_name = value.name
        if gv_name not in global_ref_map:
            slot_idx = len(global_ref_map)
            global_ref_map[gv_name] = slot_idx
            global_refs.append(gv_name)
            reg_alloc.register_gref(gv_name)
        idx = global_ref_map[gv_name]
        table_reg = reg_alloc.gref_table_reg
        reg = reg_alloc.get_or_assign(value)
        byte_off = idx * 8
        insns: list[int] = []
        if _fits_i12(byte_off):
            # LD reg, byte_off(table_reg)
            insns.append(encode_i_type(Opcode.LOAD, reg, Funct3Load.LD,
                                        table_reg, byte_off & 0xFFF))
        else:
            # Large offset: ADDI reg, table_reg, offset; LD reg, 0(reg)
            insns.extend(_encode_load_imm(reg, byte_off))
            insns.append(encode_r_type(Opcode.OP64, reg, Funct3Op64.ADD,
                                        table_reg, reg, Funct7Op64.NORMAL))
            insns.append(encode_i_type(Opcode.LOAD, reg, Funct3Load.LD, reg, 0))
        return reg, insns
    return reg_alloc.get_or_assign(value), []


def _is_global_var(value) -> bool:
    """Check if a value is a global variable (not a function)."""
    try:
        return value.is_global_value and not hasattr(value, 'basic_blocks')
    except (AttributeError, RuntimeError):
        return False


# ---------------------------------------------------------------------------
# Instruction selection
# ---------------------------------------------------------------------------

# Map LLVM binary opcode to (funct3, funct7) for OP64
_BINOP_MAP = {
    llvm.Opcode.Add: (Funct3Op64.ADD, Funct7Op64.NORMAL),
    llvm.Opcode.Sub: (Funct3Op64.ADD, Funct7Op64.SUB_SRA),
    llvm.Opcode.Mul: (Funct3Op64.ADD, Funct7Op64.MULDIV),  # MUL: funct3=0, funct7=1
    llvm.Opcode.SDiv: (Funct3Op64.XOR, Funct7Op64.MULDIV),  # DIV: funct3=4, funct7=1
    llvm.Opcode.UDiv: (Funct3Op64.SRL, Funct7Op64.MULDIV),  # DIVU: funct3=5, funct7=1
    llvm.Opcode.SRem: (Funct3Op64.OR, Funct7Op64.MULDIV),   # REM: funct3=6, funct7=1
    llvm.Opcode.URem: (Funct3Op64.AND, Funct7Op64.MULDIV),  # REMU: funct3=7, funct7=1
    llvm.Opcode.And: (Funct3Op64.AND, Funct7Op64.NORMAL),
    llvm.Opcode.Or: (Funct3Op64.OR, Funct7Op64.NORMAL),
    llvm.Opcode.Xor: (Funct3Op64.XOR, Funct7Op64.NORMAL),
    llvm.Opcode.Shl: (Funct3Op64.SLL, Funct7Op64.NORMAL),
    llvm.Opcode.LShr: (Funct3Op64.SRL, Funct7Op64.NORMAL),
    llvm.Opcode.AShr: (Funct3Op64.SRL, Funct7Op64.SUB_SRA),
}

# Map LLVM icmp predicate to branch funct3
_ICMP_TO_BRANCH = {
    llvm.IntPredicate.EQ: Funct3Branch.BEQ,
    llvm.IntPredicate.NE: Funct3Branch.BNE,
    llvm.IntPredicate.SLT: Funct3Branch.BLT,
    llvm.IntPredicate.SGE: Funct3Branch.BGE,
    llvm.IntPredicate.ULT: Funct3Branch.BLTU,
    llvm.IntPredicate.UGE: Funct3Branch.BGEU,
    # SGT/UGT: swap operands and use BLT/BLTU
    # SLE/ULE: swap operands and use BGE/BGEU
}


def _get_const_int_value(value) -> int | None:
    """Try to extract a constant integer value."""
    try:
        if not value.is_constant_int:
            return None
        return value.const_zext_value
    except (AttributeError, RuntimeError):
        return None


def _compile_instruction(
    inst,
    reg_alloc: RegisterAllocator,
    layout: BlockLayout,
    current_offset: int,
    host_functions: list[str] | None = None,
    host_index_map: dict[str, int] | None = None,
    global_refs: list[str] | None = None,
    global_ref_map: dict[str, int] | None = None,
    num_params: int = 0,
) -> list[int]:
    """Compile a single LLVM instruction to VM instruction(s).

    Returns list of 32-bit instruction words.
    """
    opcode = inst.opcode

    def _prep(value):
        return _prepare_operand(value, reg_alloc, global_refs, global_ref_map,
                                num_params)

    # --- Binary arithmetic ---
    if opcode in _BINOP_MAP:
        funct3, funct7 = _BINOP_MAP[opcode]
        rd = reg_alloc.get_or_assign(inst)
        rs1, prefix1 = _prep(inst.get_operand(0))
        rs2, prefix2 = _prep(inst.get_operand(1))
        return prefix1 + prefix2 + [encode_r_type(Opcode.OP64, rd, funct3, rs1, rs2, funct7)]

    # --- Return ---
    if opcode == llvm.Opcode.Ret:
        insns = []
        if inst.num_operands > 0:
            # Move return value to a0
            ret_val = inst.get_operand(0)
            src, prefix = _prep(ret_val)
            insns.extend(prefix)
            if src != RegIndex.A0:
                insns.append(encode_i_type(
                    Opcode.IMM64, RegIndex.A0, Funct3Imm64.ADDI, src, 0
                ))
        # Load syscall number into a7, then ECALL
        insns.extend(_encode_load_imm(RegIndex.A7, SYSCALL_EXIT))
        insns.append(encode_i_type(Opcode.SYSTEM, 0, 0, 0, 0))  # ECALL
        return insns

    # --- Unconditional branch ---
    if opcode == llvm.Opcode.Br:
        if inst.num_operands == 1:
            # Unconditional: br label %target
            # Offset will be fixed up in second pass
            return [encode_j_type(Opcode.JAL, RegIndex.ZERO, 0)]  # placeholder
        else:
            # Conditional: br i1 %cond, label %true, label %false
            # operands: [cond, false_bb, true_bb] (LLVM IR convention)
            cond_val = inst.get_operand(0)

            # Check if cond is an icmp we can fuse
            icmp_info = _try_get_icmp(cond_val, reg_alloc)
            if icmp_info is not None:
                funct3, rs1, rs2, prefix = icmp_info
                return prefix + [
                    encode_b_type(Opcode.BRANCH, funct3, rs1, rs2, 0),  # placeholder → true
                    encode_j_type(Opcode.JAL, RegIndex.ZERO, 0),         # placeholder → false
                ]

            # Fallback: compare cond != 0
            cond_reg = reg_alloc.get_or_assign(cond_val)
            return [
                encode_b_type(Opcode.BRANCH, Funct3Branch.BNE, cond_reg, RegIndex.ZERO, 0),
                encode_j_type(Opcode.JAL, RegIndex.ZERO, 0),
            ]

    # --- ICmp (standalone, not fused with branch) ---
    if opcode == llvm.Opcode.ICmp:
        rd = reg_alloc.get_or_assign(inst)
        pred = inst.icmp_predicate
        op0 = inst.get_operand(0)
        op1 = inst.get_operand(1)
        rs1, prefix1 = _prep(op0)
        rs2, prefix2 = _prep(op1)
        prefix = prefix1 + prefix2

        # SLT/SLTU are directly available as R-type
        if pred == llvm.IntPredicate.SLT:
            return prefix + [encode_r_type(Opcode.OP64, rd, Funct3Op64.SLT, rs1, rs2, Funct7Op64.NORMAL)]
        if pred == llvm.IntPredicate.ULT:
            return prefix + [encode_r_type(Opcode.OP64, rd, Funct3Op64.SLTU, rs1, rs2, Funct7Op64.NORMAL)]

        # For others, we use branch to set 0/1 — but that's complex.
        # Simpler: use BEQ/BNE to skip an ADDI
        # rd = 0; if (cond) rd = 1
        insns = list(prefix)
        insns.append(encode_i_type(Opcode.IMM64, rd, Funct3Imm64.ADDI, RegIndex.ZERO, 0))  # rd = 0
        if pred == llvm.IntPredicate.EQ:
            # BNE rs1, rs2, +8 (skip next); ADDI rd, zero, 1
            insns.append(encode_b_type(Opcode.BRANCH, Funct3Branch.BNE, rs1, rs2, 8))
        elif pred == llvm.IntPredicate.NE:
            insns.append(encode_b_type(Opcode.BRANCH, Funct3Branch.BEQ, rs1, rs2, 8))
        elif pred == llvm.IntPredicate.SGE:
            insns.append(encode_b_type(Opcode.BRANCH, Funct3Branch.BLT, rs1, rs2, 8))
        elif pred == llvm.IntPredicate.UGE:
            insns.append(encode_b_type(Opcode.BRANCH, Funct3Branch.BLTU, rs1, rs2, 8))
        elif pred == llvm.IntPredicate.SGT:
            # SGT: swap operands, use BGE (rs2 >= rs1 means skip)
            insns.append(encode_b_type(Opcode.BRANCH, Funct3Branch.BGE, rs2, rs1, 8))
        elif pred == llvm.IntPredicate.UGT:
            insns.append(encode_b_type(Opcode.BRANCH, Funct3Branch.BGEU, rs2, rs1, 8))
        elif pred == llvm.IntPredicate.SLE:
            insns.append(encode_b_type(Opcode.BRANCH, Funct3Branch.BLT, rs2, rs1, 8))
        elif pred == llvm.IntPredicate.ULE:
            insns.append(encode_b_type(Opcode.BRANCH, Funct3Branch.BLTU, rs2, rs1, 8))
        else:
            raise ValueError(f"Unsupported icmp predicate: {pred}")
        insns.append(encode_i_type(Opcode.IMM64, rd, Funct3Imm64.ADDI, RegIndex.ZERO, 1))  # rd = 1
        return insns

    # --- Alloca ---
    if opcode == llvm.Opcode.Alloca:
        # Alloca offsets are pre-computed; SP was adjusted in the prologue.
        # The address is materialized on-demand when the value is used
        # (see _prepare_operand).  Nothing to emit here.
        if not reg_alloc.is_alloca(inst):
            # Safety: register the alloca if somehow missed in pre-scan
            reg_alloc.register_alloca(inst)
        return []

    # --- Load ---
    if opcode == llvm.Opcode.Load:
        rd = reg_alloc.get_or_assign(inst)
        ptr_val = inst.get_operand(0)
        rs1, prefix = _prep(ptr_val)
        # Determine load width from result type
        ty = inst.type
        if ty.kind == llvm.TypeKind.Integer:
            width = ty.int_width
            if width <= 8:
                funct3 = Funct3Load.LB
            elif width <= 16:
                funct3 = Funct3Load.LH
            elif width <= 32:
                funct3 = Funct3Load.LW
            else:
                funct3 = Funct3Load.LD
        elif ty.kind == llvm.TypeKind.Pointer:
            funct3 = Funct3Load.LD  # pointers are 64-bit
        else:
            raise ValueError(f"Unsupported load type: {ty.kind}")
        return prefix + [encode_i_type(Opcode.LOAD, rd, funct3, rs1, 0)]

    # --- Store ---
    if opcode == llvm.Opcode.Store:
        val = inst.get_operand(0)
        ptr = inst.get_operand(1)
        rs2, prefix1 = _prep(val)
        rs1, prefix2 = _prep(ptr)
        prefix = prefix1 + prefix2
        # Determine store width from value type
        ty = val.type
        if ty.kind == llvm.TypeKind.Integer:
            width = ty.int_width
            if width <= 8:
                funct3 = Funct3Store.SB
            elif width <= 16:
                funct3 = Funct3Store.SH
            elif width <= 32:
                funct3 = Funct3Store.SW
            else:
                funct3 = Funct3Store.SD
        elif ty.kind == llvm.TypeKind.Pointer:
            funct3 = Funct3Store.SD
        else:
            raise ValueError(f"Unsupported store value type: {ty.kind}")
        return prefix + [encode_s_type(Opcode.STORE, funct3, rs1, rs2, 0)]

    # --- ZExt / SExt / Trunc ---
    if opcode in (llvm.Opcode.ZExt, llvm.Opcode.SExt, llvm.Opcode.Trunc):
        # In the VM, all registers are 64-bit. ZExt/Trunc are often no-ops.
        # We just move the value (or mask for trunc).
        rd = reg_alloc.get_or_assign(inst)
        src, prefix = _prep(inst.get_operand(0))
        insns = list(prefix)
        if rd != src:
            insns.append(encode_i_type(Opcode.IMM64, rd, Funct3Imm64.ADDI, src, 0))
        return insns

    # --- Select ---
    if opcode == llvm.Opcode.Select:
        # select i1 %cond, type %true_val, type %false_val
        # Compiled as: rd = false; BEQ cond, zero, +8; rd = true
        rd = reg_alloc.get_or_assign(inst)
        cond_reg, cond_prefix = _prep(inst.get_operand(0))
        true_reg, true_prefix = _prep(inst.get_operand(1))
        false_reg, false_prefix = _prep(inst.get_operand(2))
        insns = list(cond_prefix) + list(true_prefix) + list(false_prefix)
        # rd = false_val (default)
        if rd != false_reg:
            insns.append(encode_i_type(Opcode.IMM64, rd, Funct3Imm64.ADDI, false_reg, 0))
        # if cond != 0, overwrite with true_val
        # BEQ cond, zero, +8 → skip the next ADDI
        insns.append(encode_b_type(Opcode.BRANCH, Funct3Branch.BEQ, cond_reg, RegIndex.ZERO, 8))
        insns.append(encode_i_type(Opcode.IMM64, rd, Funct3Imm64.ADDI, true_reg, 0))
        return insns

    # --- GEP (GetElementPtr) ---
    if opcode == llvm.Opcode.GetElementPtr:
        rd = reg_alloc.get_or_assign(inst)
        base = inst.get_operand(0)
        rs1, base_prefix = _prep(base)
        source_ty = inst.gep_source_element_type
        elem_size = _get_type_size(source_ty)

        # Single-index GEP: gep <type>, ptr %base, idx
        # Result = base + idx * sizeof(source_type)
        if inst.num_operands == 2:
            idx = inst.get_operand(1)
            const_idx = _get_const_int_value(idx)
            if const_idx is not None:
                byte_offset = const_idx * elem_size
                if byte_offset == 0:
                    if rd != rs1:
                        return base_prefix + [encode_i_type(Opcode.IMM64, rd, Funct3Imm64.ADDI, rs1, 0)]
                    return base_prefix
                if _fits_i12(byte_offset):
                    return base_prefix + [encode_i_type(Opcode.IMM64, rd, Funct3Imm64.ADDI, rs1, byte_offset & 0xFFF)]
                insns = list(base_prefix) + _encode_load_imm(rd, byte_offset)
                insns.append(encode_r_type(Opcode.OP64, rd, Funct3Op64.ADD, rs1, rd, Funct7Op64.NORMAL))
                return insns
            # Variable index: rd = base + idx * elem_size
            rs2, prefix = _prep(idx)
            insns = list(base_prefix) + list(prefix)
            if elem_size == 1:
                insns.append(encode_r_type(Opcode.OP64, rd, Funct3Op64.ADD, rs1, rs2, Funct7Op64.NORMAL))
            else:
                insns.extend(_scale_index(rd, rs2, elem_size))
                insns.append(encode_r_type(Opcode.OP64, rd, Funct3Op64.ADD, rs1, rd, Funct7Op64.NORMAL))
            return insns

        # Multi-index GEP: gep <agg_type>, ptr %base, idx0, idx1
        # For array source [N x T]: result = base + idx0 * sizeof([N x T]) + idx1 * sizeof(T)
        if inst.num_operands == 3:
            if source_ty.kind != llvm.TypeKind.Array:
                raise ValueError(f"Multi-index GEP with non-array source type: {source_ty.kind}")
            inner_size = _get_type_size(source_ty.element_type)
            idx0 = inst.get_operand(1)
            idx1 = inst.get_operand(2)
            const0 = _get_const_int_value(idx0)
            const1 = _get_const_int_value(idx1)
            # Common case: idx0 == 0
            if const0 is not None and const0 == 0:
                if const1 is not None:
                    byte_offset = const1 * inner_size
                    if byte_offset == 0:
                        if rd != rs1:
                            return base_prefix + [encode_i_type(Opcode.IMM64, rd, Funct3Imm64.ADDI, rs1, 0)]
                        return base_prefix
                    if _fits_i12(byte_offset):
                        return base_prefix + [encode_i_type(Opcode.IMM64, rd, Funct3Imm64.ADDI, rs1, byte_offset & 0xFFF)]
                    insns = list(base_prefix) + _encode_load_imm(rd, byte_offset)
                    insns.append(encode_r_type(Opcode.OP64, rd, Funct3Op64.ADD, rs1, rd, Funct7Op64.NORMAL))
                    return insns
                # Variable idx1
                rs2, prefix = _prep(idx1)
                insns = list(base_prefix) + list(prefix)
                if inner_size == 1:
                    insns.append(encode_r_type(Opcode.OP64, rd, Funct3Op64.ADD, rs1, rs2, Funct7Op64.NORMAL))
                else:
                    insns.extend(_scale_index(rd, rs2, inner_size))
                    insns.append(encode_r_type(Opcode.OP64, rd, Funct3Op64.ADD, rs1, rd, Funct7Op64.NORMAL))
                return insns
            raise ValueError(f"Multi-index GEP with non-zero first index not supported")
        raise ValueError(f"Unsupported GEP with {inst.num_operands} operands")

    # --- Call (via HOST_CALL) ---
    if opcode == llvm.Opcode.Call:
        if host_functions is None or host_index_map is None:
            raise ValueError("Call instructions require host_functions tracking")
        called = inst.called_value
        fn_name = called.name
        # Get or assign host function index
        if fn_name not in host_index_map:
            host_index_map[fn_name] = len(host_functions)
            host_functions.append(fn_name)
        idx = host_index_map[fn_name]

        insns = []
        # Move call arguments to a1-a6 (a0 is reserved for host index)
        num_args = inst.num_arg_operands
        if num_args > 6:
            raise ValueError(f"HOST_CALL supports max 6 args, got {num_args}")
        for i in range(num_args):
            arg = inst.get_arg_operand(i)
            src, prefix = _prep(arg)
            insns.extend(prefix)
            target_reg = RegIndex.A1 + i  # a1=11, a2=12, ..., a6=16
            if src != target_reg:
                insns.append(encode_i_type(
                    Opcode.IMM64, target_reg, Funct3Imm64.ADDI, src, 0
                ))
        # Load host index into a0
        insns.extend(_encode_load_imm(RegIndex.A0, idx))
        # Load HOST_CALL syscall number into a7
        insns.extend(_encode_load_imm(RegIndex.A7, SYSCALL_HOST_CALL))
        # ECALL
        insns.append(encode_i_type(Opcode.SYSTEM, 0, 0, 0, 0))
        # Result is in a0; assign to destination register
        if inst.type.kind != llvm.TypeKind.Void:
            rd = reg_alloc.get_or_assign(inst)
            if rd != RegIndex.A0:
                insns.append(encode_i_type(
                    Opcode.IMM64, rd, Funct3Imm64.ADDI, RegIndex.A0, 0
                ))
        return insns

    raise ValueError(f"Unsupported instruction opcode: {opcode}")


def _try_get_icmp(
    cond_val, reg_alloc: RegisterAllocator,
    global_refs: list[str] | None = None,
    global_ref_map: dict[str, int] | None = None,
    num_params: int = 0,
) -> tuple[int, int, int, list[int]] | None:
    """Try to extract icmp info (funct3, rs1, rs2, prefix) for branch fusion.

    Returns (funct3, rs1, rs2, prefix_instructions) or None.
    """
    try:
        if cond_val.opcode != llvm.Opcode.ICmp:
            return None
    except (AttributeError, RuntimeError):
        return None

    pred = cond_val.icmp_predicate
    op0 = cond_val.get_operand(0)
    op1 = cond_val.get_operand(1)
    rs1, prefix1 = _prepare_operand(op0, reg_alloc, global_refs, global_ref_map, num_params)
    rs2, prefix2 = _prepare_operand(op1, reg_alloc, global_refs, global_ref_map, num_params)
    prefix = prefix1 + prefix2

    if pred in _ICMP_TO_BRANCH:
        return (_ICMP_TO_BRANCH[pred], rs1, rs2, prefix)
    if pred == llvm.IntPredicate.SGT:
        return (Funct3Branch.BLT, rs2, rs1, prefix)
    if pred == llvm.IntPredicate.UGT:
        return (Funct3Branch.BLTU, rs2, rs1, prefix)
    if pred == llvm.IntPredicate.SLE:
        return (Funct3Branch.BGE, rs2, rs1, prefix)
    if pred == llvm.IntPredicate.ULE:
        return (Funct3Branch.BGEU, rs2, rs1, prefix)
    return None


# ---------------------------------------------------------------------------
# Branch target info for fixup
# ---------------------------------------------------------------------------


@dataclass
class _BranchFixup:
    """Records a branch instruction that needs offset fixup."""
    byte_offset: int       # offset of the instruction in bytecode
    target_bb_hash: int    # hash of the target basic block
    is_j_type: bool        # True for JAL (J-type), False for Bxx (B-type)


# ---------------------------------------------------------------------------
# Main compiler entry point
# ---------------------------------------------------------------------------


def compile_function(func) -> tuple[bytes, list[str], list[str]]:
    """Compile an LLVM function to VM bytecode.

    Pre-processes with demote_phi_to_stack() to eliminate PHI nodes.

    Returns (bytecode, host_function_names, global_ref_names) where:
    - host_function_names is the list of external functions called via HOST_CALL
    - global_ref_names is the list of global variables whose addresses must be
      pre-loaded into VM registers by the interpreter stub

    Raises ValueError if the function contains unsupported features.
    """
    # Validate function is eligible
    _validate_function(func)

    # Demote PHI nodes and cross-block registers to stack operations
    demote_phi_to_stack(func)
    demote_regs_to_stack(func)

    reg_alloc = RegisterAllocator()
    layout = BlockLayout()
    host_functions: list[str] = []  # ordered list of host function names
    host_index_map: dict[str, int] = {}  # name -> index in host_functions
    global_refs: list[str] = []  # ordered list of global variable names
    global_ref_map: dict[str, int] = {}  # name -> index in global_refs

    # Assign argument registers
    num_params = func.function_type.param_count
    for i in range(num_params):
        param = func.get_param(i)
        reg_alloc.assign_arg(param, i)

    # Reserve one arg register for the global ref table base pointer.
    # Slot num_params in the args array holds the table base address;
    # global ref addresses are at table[0], table[1], etc.
    gref_table_arg_idx = num_params
    if gref_table_arg_idx < len(ARG_REGS):
        reg_alloc.setup_gref_table(gref_table_arg_idx)
    else:
        raise ValueError(f"Too many params ({num_params}) to reserve a global ref table register")

    blocks = list(func.basic_blocks)
    layout.block_order = blocks

    # Pre-scan: register all alloca instructions so their SP offsets are
    # known before any code is emitted.  This lets _prepare_operand
    # materialize alloca addresses on-demand without persistent registers.
    for bb in blocks:
        for inst in bb.instructions:
            if inst.opcode == llvm.Opcode.Alloca:
                reg_alloc.register_alloca(inst)

    # --- Pass 1: compile all instructions, record fixups ---
    all_insns: list[int] = []          # flat list of instruction words
    fixups: list[_BranchFixup] = []    # branch fixups needed
    block_starts: dict[int, int] = {}  # hash(bb) -> index into all_insns

    # Emit stack frame prologue: SP -= total_alloca_size
    frame_size = reg_alloc.total_alloca_size
    if frame_size > 0:
        neg = (-frame_size) & 0xFFFFFFFFFFFFFFFF
        if _fits_i12(-frame_size):
            all_insns.append(encode_i_type(
                Opcode.IMM64, RegIndex.SP, Funct3Imm64.ADDI,
                RegIndex.SP, neg & 0xFFF))
        else:
            # Load -frame_size into T0, then ADD SP, SP, T0
            all_insns.extend(_encode_load_imm(RegIndex.T0, neg))
            all_insns.append(encode_r_type(
                Opcode.OP64, RegIndex.SP, Funct3Op64.ADD,
                RegIndex.SP, RegIndex.T0, Funct7Op64.NORMAL))

    for bb in blocks:
        # Reclaim block-local registers at each block boundary
        reg_alloc.reset_block_locals()

        block_starts[hash(bb)] = len(all_insns)
        layout.set_offset(bb, len(all_insns) * 4)

        instructions = list(bb.instructions)

        # Pre-scan: find the last instruction index where each operand value
        # is used. After compiling that instruction, we can free the register.
        last_use_idx: dict[int, int] = {}  # hash(operand) -> last inst index
        for idx, scan_inst in enumerate(instructions):
            for oi in range(scan_inst.num_operands):
                op = scan_inst.get_operand(oi)
                last_use_idx[hash(op)] = idx
            # For conditional branches with fused icmp, the icmp's operands
            # are also used by the branch instruction
            if (scan_inst.opcode == llvm.Opcode.Br
                    and scan_inst.num_operands > 1):
                cond = scan_inst.get_operand(0)
                try:
                    if cond.opcode == llvm.Opcode.ICmp:
                        for oi in range(cond.num_operands):
                            op = cond.get_operand(oi)
                            last_use_idx[hash(op)] = idx
                except (AttributeError, RuntimeError):
                    pass
        # Build reverse map: inst_idx -> set of hashes to free after it
        free_after: dict[int, list[int]] = {}
        for vh, li in last_use_idx.items():
            free_after.setdefault(li, []).append(vh)

        for inst_idx, inst in enumerate(instructions):
            opcode = inst.opcode

            # Skip PHI nodes (should be demoted, but just in case)
            if opcode == llvm.Opcode.PHI:
                continue

            # Handle switch specially for fixup tracking
            if opcode == llvm.Opcode.Switch:
                # switch i32 %val, label %default [i32 C0, label %bb0, ...]
                # Operands: [cond, default_bb, case0_val, case0_bb, ...]
                cond_val = inst.get_operand(0)
                default_bb_target = inst.get_operand(1)
                cond_reg, cond_prefix = _prepare_operand(
                    cond_val, reg_alloc, global_refs, global_ref_map,
                    num_params)
                all_insns.extend(cond_prefix)

                num_cases = (inst.num_operands - 2) // 2
                for ci in range(num_cases):
                    case_val = inst.get_operand(2 + ci * 2)
                    case_bb = inst.get_operand(3 + ci * 2)
                    const_v = _get_const_int_value(case_val)
                    if const_v is None:
                        raise ValueError("Switch case value must be a constant")
                    # Load case constant into a temp register
                    tmp_reg = reg_alloc.get_or_assign(case_val)
                    load_insns = _encode_load_imm(tmp_reg, const_v)
                    all_insns.extend(load_insns)
                    # BEQ cond_reg, tmp_reg, target (placeholder offset)
                    b_offset = len(all_insns) * 4
                    fixups.append(_BranchFixup(b_offset, hash(case_bb), is_j_type=False))
                    all_insns.append(encode_b_type(
                        Opcode.BRANCH, Funct3Branch.BEQ, cond_reg, tmp_reg, 0
                    ))

                # Fall through to default
                j_offset = len(all_insns) * 4
                fixups.append(_BranchFixup(j_offset, hash(default_bb_target), is_j_type=True))
                all_insns.append(encode_j_type(Opcode.JAL, RegIndex.ZERO, 0))
                # Free dead operand registers
                for vh in free_after.get(inst_idx, []):
                    reg_alloc.free_by_hash(vh)
                continue

            # Handle branches specially for fixup tracking
            if opcode == llvm.Opcode.Br:
                if inst.num_operands == 1:
                    # Unconditional branch
                    target_bb = inst.get_operand(0)
                    insn_offset = len(all_insns) * 4
                    fixups.append(_BranchFixup(insn_offset, hash(target_bb), is_j_type=True))
                    all_insns.append(encode_j_type(Opcode.JAL, RegIndex.ZERO, 0))
                else:
                    # Conditional branch: operands are [cond, false_bb, true_bb]
                    cond_val = inst.get_operand(0)
                    false_bb = inst.get_operand(1)
                    true_bb = inst.get_operand(2)

                    icmp_info = _try_get_icmp(
                        cond_val, reg_alloc, global_refs, global_ref_map,
                        num_params)
                    if icmp_info is not None:
                        funct3, rs1, rs2, prefix = icmp_info
                    else:
                        funct3 = Funct3Branch.BNE
                        rs1 = reg_alloc.get_or_assign(cond_val)
                        rs2 = RegIndex.ZERO
                        prefix = []

                    # Emit prefix instructions (constant materialization)
                    all_insns.extend(prefix)

                    # B-type branch to true target
                    b_offset = len(all_insns) * 4
                    fixups.append(_BranchFixup(b_offset, hash(true_bb), is_j_type=False))
                    all_insns.append(encode_b_type(Opcode.BRANCH, funct3, rs1, rs2, 0))

                    # JAL to false target
                    j_offset = len(all_insns) * 4
                    fixups.append(_BranchFixup(j_offset, hash(false_bb), is_j_type=True))
                    all_insns.append(encode_j_type(Opcode.JAL, RegIndex.ZERO, 0))
                # Free dead operand registers
                for vh in free_after.get(inst_idx, []):
                    reg_alloc.free_by_hash(vh)
                continue

            # All other instructions
            compiled = _compile_instruction(
                inst, reg_alloc, layout, len(all_insns) * 4,
                host_functions, host_index_map,
                global_refs, global_ref_map,
                num_params,
            )
            all_insns.extend(compiled)

            # Free dead operand registers
            for vh in free_after.get(inst_idx, []):
                reg_alloc.free_by_hash(vh)

    # --- Pass 2: fixup branch offsets ---
    for fixup in fixups:
        target_offset = layout.block_offsets.get(fixup.target_bb_hash)
        if target_offset is None:
            raise ValueError("Branch target block not found in layout")

        rel_offset = target_offset - fixup.byte_offset
        insn_idx = fixup.byte_offset // 4

        if fixup.is_j_type:
            all_insns[insn_idx] = encode_j_type(Opcode.JAL, RegIndex.ZERO, rel_offset)
        else:
            # Re-encode B-type preserving funct3, rs1, rs2
            old_insn = all_insns[insn_idx]
            funct3 = (old_insn >> 12) & 0x7
            rs1 = (old_insn >> 15) & 0x1F
            rs2 = (old_insn >> 20) & 0x1F
            all_insns[insn_idx] = encode_b_type(Opcode.BRANCH, funct3, rs1, rs2, rel_offset)

    # --- Emit bytecode ---
    return (
        b"".join(pack_instruction(insn) for insn in all_insns),
        host_functions,
        global_refs,
    )


def _validate_function(func) -> None:
    """Validate that a function is eligible for compilation."""
    if func.is_declaration:
        raise ValueError("Cannot compile a declaration")

    # Check return type
    fn_ty = func.function_type
    ret_ty = fn_ty.return_type
    if ret_ty.kind not in (llvm.TypeKind.Integer, llvm.TypeKind.Void):
        raise ValueError(f"Unsupported return type: {ret_ty.kind}")

    # Check param types
    for i in range(fn_ty.param_count):
        pty = fn_ty.param_types[i]
        if pty.kind not in (llvm.TypeKind.Integer, llvm.TypeKind.Pointer):
            raise ValueError(f"Unsupported parameter type: {pty.kind}")

    # Check for unsupported instructions
    for bb in func.basic_blocks:
        for inst in bb.instructions:
            op = inst.opcode
            if op in (llvm.Opcode.FAdd, llvm.Opcode.FSub, llvm.Opcode.FMul,
                      llvm.Opcode.FDiv, llvm.Opcode.FRem, llvm.Opcode.FCmp):
                raise ValueError("Floating point not supported")
            if op == llvm.Opcode.Invoke:
                raise ValueError("Invoke (exceptions) not supported")

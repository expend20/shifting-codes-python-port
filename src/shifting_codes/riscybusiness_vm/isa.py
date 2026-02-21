"""ISA definition for the RISC-V inspired VM.

Loads opcode values from vendor/riscy-business/riscvm/opcodes.json and provides
encoding/decoding functions for all instruction formats.

32 registers x 64-bit, 32-bit fixed-width little-endian instructions matching
RISC-V field layout.
"""

from __future__ import annotations

import json
import struct
from enum import IntEnum
from pathlib import Path

# ---------------------------------------------------------------------------
# Load opcodes.json from the vendor submodule
# ---------------------------------------------------------------------------

_OPCODES_JSON = Path(__file__).resolve().parents[3] / "vendor" / "riscy-business" / "riscvm" / "opcodes.json"


def _load_opcodes() -> dict:
    with open(_OPCODES_JSON) as f:
        return json.load(f)


_OPCODES = _load_opcodes()

# ---------------------------------------------------------------------------
# Opcode enum — 5-bit opcode field (bits [6:2] of instruction)
# ---------------------------------------------------------------------------


class Opcode(IntEnum):
    """RV64 major opcodes (5-bit, from bits [6:2])."""
    LOAD = 0b00000     # 0
    FENCE = 0b00011    # 3
    IMM64 = 0b00100    # 4
    AUIPC = 0b00101    # 5
    IMM32 = 0b00110    # 6
    STORE = 0b01000    # 8
    OP64 = 0b01100     # 12
    LUI = 0b01101      # 13
    OP32 = 0b01110     # 14
    BRANCH = 0b11000   # 24
    JALR = 0b11001     # 25
    JAL = 0b11011      # 27
    SYSTEM = 0b11100   # 28


# ---------------------------------------------------------------------------
# Funct3/Funct7 enums for instruction sub-types
# ---------------------------------------------------------------------------


class Funct3Op64(IntEnum):
    """funct3 values for OP64 (R-type register-register)."""
    ADD = 0   # also SUB with funct7=0x20
    SLL = 1
    SLT = 2
    SLTU = 3
    XOR = 4
    SRL = 5   # also SRA with funct7=0x20
    OR = 6
    AND = 7


class Funct7Op64(IntEnum):
    """funct7 values for OP64 to distinguish sub-operations."""
    NORMAL = 0b0000000   # ADD, SLL, SLT, SLTU, XOR, SRL, OR, AND
    SUB_SRA = 0b0100000  # SUB (funct3=0), SRA (funct3=5)
    MULDIV = 0b0000001   # MUL, MULH, etc.


class Funct3Imm64(IntEnum):
    """funct3 values for IMM64 (I-type immediate)."""
    ADDI = 0
    SLLI = 1
    SLTI = 2
    SLTIU = 3
    XORI = 4
    SRXI = 5  # SRLI/SRAI distinguished by imm[11:5]
    ORI = 6
    ANDI = 7


class Funct3Branch(IntEnum):
    """funct3 values for BRANCH (B-type)."""
    BEQ = 0
    BNE = 1
    BLT = 4
    BGE = 5
    BLTU = 6
    BGEU = 7


class Funct3Load(IntEnum):
    """funct3 values for LOAD."""
    LB = 0
    LH = 1
    LW = 2
    LD = 3
    LBU = 4
    LHU = 5
    LWU = 6


class Funct3Store(IntEnum):
    """funct3 values for STORE."""
    SB = 0
    SH = 1
    SW = 2
    SD = 3


# ---------------------------------------------------------------------------
# Register indices (RISC-V ABI names)
# ---------------------------------------------------------------------------


class RegIndex(IntEnum):
    """32 RISC-V integer registers with ABI names."""
    ZERO = 0   # hardwired zero
    RA = 1     # return address
    SP = 2     # stack pointer
    GP = 3     # global pointer
    TP = 4     # thread pointer
    T0 = 5
    T1 = 6
    T2 = 7
    S0 = 8     # frame pointer / saved
    S1 = 9
    A0 = 10    # argument / return value
    A1 = 11
    A2 = 12
    A3 = 13
    A4 = 14
    A5 = 15
    A6 = 16
    A7 = 17
    S2 = 18
    S3 = 19
    S4 = 20
    S5 = 21
    S6 = 22
    S7 = 23
    S8 = 24
    S9 = 25
    S10 = 26
    S11 = 27
    T3 = 28
    T4 = 29
    T5 = 30
    T6 = 31


# Registers available for SSA temporaries (not zero, ra, sp, a0-a7)
# GP and TP are included since the VM doesn't use them for their ABI purpose
TEMP_REGS = [
    RegIndex.T0, RegIndex.T1, RegIndex.T2,
    RegIndex.GP, RegIndex.TP,
    RegIndex.S0, RegIndex.S1,
    RegIndex.S2, RegIndex.S3, RegIndex.S4, RegIndex.S5,
    RegIndex.S6, RegIndex.S7, RegIndex.S8, RegIndex.S9,
    RegIndex.S10, RegIndex.S11,
    RegIndex.T3, RegIndex.T4, RegIndex.T5, RegIndex.T6,
]

ARG_REGS = [
    RegIndex.A0, RegIndex.A1, RegIndex.A2, RegIndex.A3,
    RegIndex.A4, RegIndex.A5, RegIndex.A6, RegIndex.A7,
]

# Syscall numbers (matching riscy-business)
SYSCALL_EXIT = 10000
SYSCALL_HOST_CALL = 20000

# ---------------------------------------------------------------------------
# Instruction encoding helpers
# ---------------------------------------------------------------------------

_MASK5 = 0x1F
_MASK7 = 0x7F
_MASK3 = 0x7
_MASK12 = 0xFFF
_MASK20 = 0xFFFFF


def _sign_extend(value: int, bits: int) -> int:
    """Sign-extend a value from `bits` width to Python int."""
    sign_bit = 1 << (bits - 1)
    return (value & ((1 << bits) - 1)) - ((value & sign_bit) << 1)


def encode_r_type(opcode: int, rd: int, funct3: int, rs1: int, rs2: int, funct7: int) -> int:
    """Encode R-type: [funct7:7][rs2:5][rs1:5][funct3:3][rd:5][opcode:7]"""
    # opcode field is 7 bits: bits[1:0] = 0b11 (non-compressed), bits[6:2] = opcode
    op7 = ((opcode & _MASK5) << 2) | 0b11
    return (
        (op7 & _MASK7)
        | ((rd & _MASK5) << 7)
        | ((funct3 & _MASK3) << 12)
        | ((rs1 & _MASK5) << 15)
        | ((rs2 & _MASK5) << 20)
        | ((funct7 & _MASK7) << 25)
    ) & 0xFFFFFFFF


def encode_i_type(opcode: int, rd: int, funct3: int, rs1: int, imm12: int) -> int:
    """Encode I-type: [imm[11:0]:12][rs1:5][funct3:3][rd:5][opcode:7]"""
    op7 = ((opcode & _MASK5) << 2) | 0b11
    return (
        (op7 & _MASK7)
        | ((rd & _MASK5) << 7)
        | ((funct3 & _MASK3) << 12)
        | ((rs1 & _MASK5) << 15)
        | ((imm12 & _MASK12) << 20)
    ) & 0xFFFFFFFF


def encode_s_type(opcode: int, funct3: int, rs1: int, rs2: int, imm12: int) -> int:
    """Encode S-type: [imm[11:5]:7][rs2:5][rs1:5][funct3:3][imm[4:0]:5][opcode:7]"""
    op7 = ((opcode & _MASK5) << 2) | 0b11
    imm = imm12 & _MASK12
    imm_4_0 = imm & _MASK5
    imm_11_5 = (imm >> 5) & _MASK7
    return (
        (op7 & _MASK7)
        | (imm_4_0 << 7)
        | ((funct3 & _MASK3) << 12)
        | ((rs1 & _MASK5) << 15)
        | ((rs2 & _MASK5) << 20)
        | (imm_11_5 << 25)
    ) & 0xFFFFFFFF


def encode_b_type(opcode: int, funct3: int, rs1: int, rs2: int, imm13: int) -> int:
    """Encode B-type: [imm[12|10:5]:7][rs2:5][rs1:5][funct3:3][imm[4:1|11]:5][opcode:7]

    imm13 is a signed 13-bit offset (bit 0 is always 0 in RISC-V, but we
    encode the full value and extract bits accordingly).
    """
    op7 = ((opcode & _MASK5) << 2) | 0b11
    imm = imm13 & 0x1FFF  # 13 bits
    # B-type immediate bits: [12|10:5] in bits[31:25], [4:1|11] in bits[11:7]
    bit_12 = (imm >> 12) & 1
    bit_11 = (imm >> 11) & 1
    bits_10_5 = (imm >> 5) & 0x3F
    bits_4_1 = (imm >> 1) & 0xF
    return (
        (op7 & _MASK7)
        | (bit_11 << 7)
        | (bits_4_1 << 8)
        | ((funct3 & _MASK3) << 12)
        | ((rs1 & _MASK5) << 15)
        | ((rs2 & _MASK5) << 20)
        | (bits_10_5 << 25)
        | (bit_12 << 31)
    ) & 0xFFFFFFFF


def encode_u_type(opcode: int, rd: int, imm20: int) -> int:
    """Encode U-type: [imm[31:12]:20][rd:5][opcode:7]"""
    op7 = ((opcode & _MASK5) << 2) | 0b11
    return (
        (op7 & _MASK7)
        | ((rd & _MASK5) << 7)
        | ((imm20 & _MASK20) << 12)
    ) & 0xFFFFFFFF


def encode_j_type(opcode: int, rd: int, imm21: int) -> int:
    """Encode J-type: [imm[20|10:1|11|19:12]:20][rd:5][opcode:7]

    imm21 is a signed 21-bit offset (bit 0 always 0).
    """
    op7 = ((opcode & _MASK5) << 2) | 0b11
    imm = imm21 & 0x1FFFFF  # 21 bits
    bit_20 = (imm >> 20) & 1
    bits_10_1 = (imm >> 1) & 0x3FF
    bit_11 = (imm >> 11) & 1
    bits_19_12 = (imm >> 12) & 0xFF
    imm_field = (
        (bits_19_12)
        | (bit_11 << 8)
        | (bits_10_1 << 9)
        | (bit_20 << 19)
    )
    return (
        (op7 & _MASK7)
        | ((rd & _MASK5) << 7)
        | (imm_field << 12)
    ) & 0xFFFFFFFF


# ---------------------------------------------------------------------------
# Instruction decoding helpers
# ---------------------------------------------------------------------------


def decode_opcode(inst: int) -> int:
    """Extract 5-bit opcode from bits [6:2]."""
    return (inst >> 2) & _MASK5


def decode_rd(inst: int) -> int:
    """Extract rd from bits [11:7]."""
    return (inst >> 7) & _MASK5


def decode_funct3(inst: int) -> int:
    """Extract funct3 from bits [14:12]."""
    return (inst >> 12) & _MASK3


def decode_rs1(inst: int) -> int:
    """Extract rs1 from bits [19:15]."""
    return (inst >> 15) & _MASK5


def decode_rs2(inst: int) -> int:
    """Extract rs2 from bits [24:20]."""
    return (inst >> 20) & _MASK5


def decode_funct7(inst: int) -> int:
    """Extract funct7 from bits [31:25]."""
    return (inst >> 25) & _MASK7


def decode_i_imm(inst: int) -> int:
    """Extract and sign-extend I-type immediate (bits [31:20])."""
    raw = (inst >> 20) & _MASK12
    return _sign_extend(raw, 12)


def decode_s_imm(inst: int) -> int:
    """Extract and sign-extend S-type immediate."""
    imm_4_0 = (inst >> 7) & _MASK5
    imm_11_5 = (inst >> 25) & _MASK7
    raw = (imm_11_5 << 5) | imm_4_0
    return _sign_extend(raw, 12)


def decode_b_imm(inst: int) -> int:
    """Extract and sign-extend B-type immediate (13-bit, bit 0 = 0)."""
    bit_11 = (inst >> 7) & 1
    bits_4_1 = (inst >> 8) & 0xF
    bits_10_5 = (inst >> 25) & 0x3F
    bit_12 = (inst >> 31) & 1
    raw = (bit_12 << 12) | (bit_11 << 11) | (bits_10_5 << 5) | (bits_4_1 << 1)
    return _sign_extend(raw, 13)


def decode_u_imm(inst: int) -> int:
    """Extract U-type immediate (bits [31:12], already shifted left 12)."""
    return inst & 0xFFFFF000


def decode_j_imm(inst: int) -> int:
    """Extract and sign-extend J-type immediate (21-bit, bit 0 = 0)."""
    imm_field = (inst >> 12) & _MASK20
    bits_19_12 = imm_field & 0xFF
    bit_11 = (imm_field >> 8) & 1
    bits_10_1 = (imm_field >> 9) & 0x3FF
    bit_20 = (imm_field >> 19) & 1
    raw = (bit_20 << 20) | (bits_19_12 << 12) | (bit_11 << 11) | (bits_10_1 << 1)
    return _sign_extend(raw, 21)


def pack_instruction(inst: int) -> bytes:
    """Pack a 32-bit instruction as little-endian bytes."""
    return struct.pack("<I", inst & 0xFFFFFFFF)


def unpack_instruction(data: bytes, offset: int = 0) -> int:
    """Unpack a 32-bit instruction from little-endian bytes."""
    return struct.unpack_from("<I", data, offset)[0]

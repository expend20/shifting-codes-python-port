"""RISC-V inspired VM for code virtualization obfuscation.

Based on riscy-business (https://github.com/thesecretclub/riscy-business).
Translates LLVM IR to custom bytecode and builds an LLVM IR interpreter.
"""

from shifting_codes.riscybusiness_vm.isa import (
    Opcode, Funct3Op64, Funct7Op64, Funct3Branch, Funct3Load, Funct3Store,
    Funct3Imm64, RegIndex,
    encode_r_type, encode_i_type, encode_s_type, encode_b_type,
    encode_u_type, encode_j_type, decode_opcode,
)
from shifting_codes.riscybusiness_vm.compiler import compile_function
from shifting_codes.riscybusiness_vm.interpreter import build_vm_interpreter

__all__ = [
    "Opcode", "Funct3Op64", "Funct7Op64", "Funct3Branch",
    "Funct3Load", "Funct3Store", "Funct3Imm64", "RegIndex",
    "encode_r_type", "encode_i_type", "encode_s_type", "encode_b_type",
    "encode_u_type", "encode_j_type", "decode_opcode",
    "compile_function", "build_vm_interpreter",
]

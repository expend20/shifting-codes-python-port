"""Indirect Branch Pass — port of Polaris IndirectBranch.

Replaces direct branch instructions with indirect branches through
a stack-allocated jump table, preventing static CFG analysis.
"""

from __future__ import annotations

import llvm

from shifting_codes.passes import PassRegistry
from shifting_codes.passes.base import FunctionPass, PassInfo
from shifting_codes.utils.crypto import CryptoRandom


def _compute_obfuscated_index(
    builder: llvm.Builder, index: llvm.Value, rng: CryptoRandom,
) -> llvm.Value:
    """Obfuscate index via MBA: index ^ rand ^ rand = index."""
    int_ty = index.type
    bit_width = int_ty.int_width
    mask = (1 << bit_width) - 1

    rand_val = rng.get_uint64() & mask
    rand_const = int_ty.constant(rand_val)

    # First XOR: index ^ rand
    xor1 = builder.xor(index, rand_const, "sibr.xor1")

    # Obfuscated second XOR: a ^ b = (~a & b) | (a & ~b)
    not_xor1 = builder.not_(xor1, "sibr.not1")
    not_rand = builder.not_(rand_const, "sibr.not_rand")
    left = builder.and_(not_xor1, rand_const, "sibr.left")
    right = builder.and_(xor1, not_rand, "sibr.right")
    return builder.or_(left, right, "sibr.idx")


@PassRegistry.register
class IndirectBranchPass(FunctionPass):

    def __init__(self, rng: CryptoRandom | None = None):
        self.rng = rng or CryptoRandom()

    @classmethod
    def info(cls) -> PassInfo:
        return PassInfo(
            name="indirect_branch",
            description="[Polaris] Replace direct branches with indirect branches via jump table",
        )

    def run_on_function(self, func: llvm.Function, ctx: llvm.Context) -> bool:
        blocks = list(func.basic_blocks)
        if len(blocks) < 2:
            return False

        ptr_ty = ctx.types.ptr
        i32_ty = ctx.types.i32
        array_ty = ctx.types.array(ptr_ty, 2)

        # Collect branch instructions
        branches = []
        for bb in blocks:
            term = bb.terminator
            if term is not None and term.opcode == llvm.Opcode.Br:
                branches.append(term)

        if not branches:
            return False

        for branch in branches:
            bb = branch.block
            successors = list(branch.successors)
            is_conditional = len(successors) == 2

            # Allocate jump table in entry block
            entry_bb = list(func.basic_blocks)[0]
            first_inst = list(entry_bb.instructions)[0]

            with bb.create_builder() as builder:
                builder.position_before(first_inst)
                jump_table = builder.alloca(array_ty, name="sibr.table")

                builder.position_before(branch)

                if is_conditional:
                    # successors[0] = true target, successors[1] = false target
                    true_bb = successors[0]
                    false_bb = successors[1]

                    # Store: table[0] = false target, table[1] = true target
                    idx0 = builder.gep(
                        array_ty, jump_table,
                        [i32_ty.constant(0), i32_ty.constant(0)], "sibr.slot0",
                    )
                    builder.store(func.block_address(false_bb), idx0)

                    idx1 = builder.gep(
                        array_ty, jump_table,
                        [i32_ty.constant(0), i32_ty.constant(1)], "sibr.slot1",
                    )
                    builder.store(func.block_address(true_bb), idx1)

                    # index = zext(not(condition), i32)
                    condition = branch.condition
                    inverted = builder.not_(condition, "sibr.not_cond")
                    index = builder.zext(inverted, i32_ty, "sibr.zext")

                    # Obfuscate index
                    obf_index = _compute_obfuscated_index(builder, index, self.rng)

                    # Load target and create indirect branch
                    target_gep = builder.gep(
                        array_ty, jump_table,
                        [i32_ty.constant(0), obf_index], "sibr.target_ptr",
                    )
                    target_addr = builder.load(ptr_ty, target_gep, "sibr.target")

                    indir_br = builder.indirect_br(target_addr, 2)
                    indir_br.add_destination(true_bb)
                    indir_br.add_destination(false_bb)

                else:
                    # Unconditional branch
                    target_bb = successors[0]

                    idx0 = builder.gep(
                        array_ty, jump_table,
                        [i32_ty.constant(0), i32_ty.constant(0)], "sibr.slot0",
                    )
                    builder.store(func.block_address(target_bb), idx0)
                    loaded_addr = builder.load(ptr_ty, idx0, "sibr.target")

                    indir_br = builder.indirect_br(loaded_addr, 1)
                    indir_br.add_destination(target_bb)

            branch.erase_from_parent()

        return True

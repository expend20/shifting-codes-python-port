"""[Pluto] Control Flow Flattening Pass — port of Pluto Flattening.cpp.

Flattens function control flow into a switch-based dispatcher,
making static analysis significantly harder. Unlike the Polaris
variant, uses plaintext switch case constants with no key array
or encrypted state transitions.
"""

from __future__ import annotations

import llvm

from shifting_codes.passes import PassRegistry
from shifting_codes.passes.base import FunctionPass, PassInfo
from shifting_codes.utils.crypto import CryptoRandom
from shifting_codes.utils.ir_helpers import demote_phi_to_stack, demote_regs_to_stack


@PassRegistry.register
class PlutoFlatteningPass(FunctionPass):

    def __init__(self, rng: CryptoRandom | None = None):
        self.rng = rng or CryptoRandom()

    @classmethod
    def info(cls) -> PassInfo:
        return PassInfo(
            name="flattening_pluto",
            description="[Pluto] Control flow flattening via switch dispatcher",
        )

    def run_on_function(self, func: llvm.Function, ctx: llvm.Context) -> bool:
        blocks = list(func.basic_blocks)
        if len(blocks) <= 1:
            return False

        # Demote PHI nodes to stack first
        demote_phi_to_stack(func)

        i32 = ctx.types.i32

        entry_bb = blocks[0]
        original_blocks = blocks[1:]

        if not original_blocks:
            return False

        # Generate unique state values for each block
        used_states: set[int] = set()
        block_state_map: dict = {}
        for bb in original_blocks:
            state = self._unique_state(used_states)
            block_state_map[hash(bb)] = (bb, state)

        # Create state variable at entry (before existing instructions)
        first_state = list(block_state_map.values())[0][1]
        first_inst = list(entry_bb.instructions)[0]
        with entry_bb.create_builder() as builder:
            builder.position_before(first_inst)
            state_var = builder.alloca(i32, name="cff.state")
            builder.store(i32.constant(first_state), state_var)

        # Create dispatch block with switch
        dispatch_bb = func.append_basic_block("cff.dispatch")
        default_bb = func.append_basic_block("cff.default")

        with default_bb.create_builder() as builder:
            builder.br(dispatch_bb)

        with dispatch_bb.create_builder() as builder:
            sw_val = builder.load(i32, state_var, "cff.sw")
            switch = builder.switch_(sw_val, default_bb, len(original_blocks))
            for bb, state in block_state_map.values():
                switch.add_case(i32.constant(state), bb)

        # Rewrite terminators: entry block and all original blocks
        all_blocks_to_modify = [entry_bb] + original_blocks

        for bb in all_blocks_to_modify:
            instructions = list(bb.instructions)
            if not instructions:
                continue

            terminator = instructions[-1]
            if terminator.opcode != llvm.Opcode.Br:
                continue

            succs = list(terminator.successors)

            if len(succs) == 1:
                target = succs[0]
                target_entry = block_state_map.get(hash(target))
                if target_entry is not None:
                    _, target_state = target_entry
                    with bb.create_builder() as builder:
                        builder.position_before(terminator)
                        builder.store(i32.constant(target_state), state_var)
                        builder.br(dispatch_bb)
                    terminator.erase_from_parent()

            elif len(succs) == 2:
                true_bb = succs[0]
                false_bb = succs[1]
                true_entry = block_state_map.get(hash(true_bb))
                false_entry = block_state_map.get(hash(false_bb))

                if true_entry is not None and false_entry is not None:
                    condition = terminator.condition
                    _, true_state = true_entry
                    _, false_state = false_entry

                    with bb.create_builder() as builder:
                        builder.position_before(terminator)
                        selected = builder.select(
                            condition,
                            i32.constant(true_state),
                            i32.constant(false_state),
                            "cff.sel",
                        )
                        builder.store(selected, state_var)
                        builder.br(dispatch_bb)
                    terminator.erase_from_parent()

        demote_regs_to_stack(func)
        return True

    def _unique_state(self, used: set[int]) -> int:
        while True:
            state = self.rng.get_uint32() & 0x7FFFFFFF
            if state not in used and state >= 0x000F0000:
                used.add(state)
                return state

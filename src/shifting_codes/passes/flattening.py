"""Control Flow Flattening Pass — port of Polaris Flattening.cpp.

Flattens function control flow into a switch-based dispatcher with
dominance-based state encryption, making static analysis significantly harder.

Polaris upgrade: state values are XOR-encrypted with per-block keys
derived from dominator tree analysis, instead of plaintext case constants.
"""

from __future__ import annotations

import llvm

from shifting_codes.passes import PassRegistry
from shifting_codes.passes.base import FunctionPass, PassInfo
from shifting_codes.utils.crypto import CryptoRandom
from shifting_codes.utils.ir_helpers import demote_phi_to_stack, demote_regs_to_stack


def _compute_dominates(blocks: list, entry) -> dict:
    """Compute dominance: returns {block: set of blocks it dominates}.

    Uses iterative dataflow to compute dominator sets, then inverts.
    """
    # Build predecessor map by scanning successors
    preds: dict[int, list] = {hash(b): [] for b in blocks}
    for b in blocks:
        term = b.terminator
        if term is None:
            continue
        for succ in term.successors:
            h = hash(succ)
            if h in preds:
                preds[h].append(b)

    block_set = set(hash(b) for b in blocks)
    hash_to_block = {hash(b): b for b in blocks}

    # dom[h] = set of hashes that dominate block h
    dom = {h: set(block_set) for h in block_set}
    entry_h = hash(entry)
    dom[entry_h] = {entry_h}

    changed = True
    while changed:
        changed = False
        for b in blocks:
            h = hash(b)
            if h == entry_h:
                continue
            pred_list = preds.get(h, [])
            if not pred_list:
                new_dom = {h}
            else:
                new_dom = set.intersection(*(dom[hash(p)] for p in pred_list))
                new_dom = new_dom | {h}
            if new_dom != dom[h]:
                dom[h] = new_dom
                changed = True

    # Invert: for each block B, find all blocks B' where B is in dom[B']
    # Returns hash(block) → set of hash(dominated blocks)
    dominates: dict[int, set[int]] = {hash(b): set() for b in blocks}
    for h, dom_set in dom.items():
        for d in dom_set:
            if d != h:
                dominates[d].add(h)

    return dominates


def _build_update_key_func(mod: llvm.Module, ctx: llvm.Context) -> llvm.Function:
    """Build: void @__cff_update_key(i8 %flag, i32 %len, ptr %posArray,
                                      ptr %keyArray, i32 %num)

    If flag==0: for i in 0..len: keyArray[posArray[i]] ^= num
    """
    i8 = ctx.types.i8
    i32 = ctx.types.i32
    i64 = ctx.types.i64
    ptr = ctx.types.ptr

    fn_ty = ctx.types.function(ctx.types.void, [i8, i32, ptr, ptr, i32])
    func = mod.add_function("__cff_update_key", fn_ty)
    func.linkage = llvm.Linkage.Private

    entry_bb = func.append_basic_block("entry")
    cond_bb = func.append_basic_block("cond")
    update_bb = func.append_basic_block("update")
    end_bb = func.append_basic_block("end")

    flag = func.get_param(0)
    length = func.get_param(1)
    pos_array = func.get_param(2)
    key_array = func.get_param(3)
    num = func.get_param(4)

    with entry_bb.create_builder() as b:
        i_ptr = b.alloca(i32, name="i")
        b.store(i32.constant(0), i_ptr)
        # Only execute if flag == 0 (first visit)
        is_first = b.icmp(llvm.IntPredicate.EQ, flag, i8.constant(0), "first")
        b.cond_br(is_first, cond_bb, end_bb)

    with cond_bb.create_builder() as b:
        iv = b.load(i32, i_ptr, "iv")
        cmp = b.icmp(llvm.IntPredicate.SLT, iv, length, "cmp")
        b.cond_br(cmp, update_bb, end_bb)

    with update_bb.create_builder() as b:
        iv = b.load(i32, i_ptr, "iv")
        # pos = posArray[i]
        pos_ptr = b.gep(i32, pos_array, [iv], "posptr")
        pos = b.load(i32, pos_ptr, "pos")
        # key = &keyArray[pos]
        key_ptr = b.gep(i32, key_array, [pos], "keyptr")
        key_val = b.load(i32, key_ptr, "keyval")
        # keyArray[pos] ^= num
        xored = b.xor(key_val, num, "xored")
        b.store(xored, key_ptr)
        # i++
        b.store(b.add(iv, i32.constant(1), "inc"), i_ptr)
        b.br(cond_bb)

    with end_bb.create_builder() as b:
        b.ret_void()

    return func


@PassRegistry.register
class FlatteningPass(FunctionPass):

    def __init__(self, rng: CryptoRandom | None = None):
        self.rng = rng or CryptoRandom()

    @classmethod
    def info(cls) -> PassInfo:
        return PassInfo(
            name="flattening",
            description="[Polaris] Control flow flattening with encrypted state",
        )

    def run_on_function(self, func: llvm.Function, ctx: llvm.Context) -> bool:
        blocks = list(func.basic_blocks)
        if len(blocks) <= 1:
            return False

        demote_phi_to_stack(func)

        i8 = ctx.types.i8
        i32 = ctx.types.i32
        i64 = ctx.types.i64

        entry_bb = blocks[0]
        original_blocks = blocks[1:]

        if not original_blocks:
            return False

        # Build the update_key helper function
        update_func = _build_update_key_func(func.module, ctx)

        # Generate unique state values and block indices
        used_states: set[int] = set()
        block_state: dict[int, int] = {}  # hash(bb) → case value
        block_index: dict[int, int] = {}  # hash(bb) → index (0..N-1)
        for idx, bb in enumerate(original_blocks):
            state = self._unique_state(used_states)
            block_state[hash(bb)] = state
            block_index[hash(bb)] = idx

        n_blocks = len(original_blocks)

        # Compute dominance for key schedule
        all_flattened = original_blocks
        dominates = _compute_dominates(all_flattened, all_flattened[0])

        # Generate per-block random keys and compute key_map
        key_list: list[int] = []
        for _ in range(n_blocks):
            key_list.append(self.rng.get_uint32() & 0x7FFFFFFF)

        # key_map[idx] = XOR of key_list[j] for all j where block[j] dominates block[idx]
        key_map: list[int] = [0] * n_blocks
        for idx, bb in enumerate(original_blocks):
            for j, bb2 in enumerate(original_blocks):
                if j != idx and hash(bb) in dominates.get(hash(bb2), set()):
                    key_map[idx] ^= key_list[j]

        # Create state variable, key array, visited array at entry
        first_state = block_state[hash(original_blocks[0])]
        first_inst = list(entry_bb.instructions)[0]

        with entry_bb.create_builder() as builder:
            builder.position_before(first_inst)
            state_var = builder.alloca(i32, name="cff.state")
            builder.store(i32.constant(first_state), state_var)

            # Key array: [N x i32], initialized to 0
            key_array_ty = i32.array(n_blocks)
            key_array = builder.alloca(key_array_ty, name="cff.keys")
            # Zero-init key array element by element
            for i in range(n_blocks):
                ptr = builder.gep(i32, key_array, [i32.constant(i)], "cff.kinit")
                builder.store(i32.constant(0), ptr)

            # Visited array: [N x i8], initialized to 0
            visited_array_ty = i8.array(n_blocks)
            visited_array = builder.alloca(visited_array_ty, name="cff.visited")
            for i in range(n_blocks):
                ptr = builder.gep(i8, visited_array, [i8.constant(i)],
                                  "cff.vinit")
                builder.store(i8.constant(0), ptr)

        # Create dispatch block with switch
        dispatch_bb = func.append_basic_block("cff.dispatch")
        default_bb = func.append_basic_block("cff.default")

        with default_bb.create_builder() as builder:
            builder.br(dispatch_bb)

        with dispatch_bb.create_builder() as builder:
            sw_val = builder.load(i32, state_var, "cff.sw")
            switch = builder.switch_(sw_val, default_bb, n_blocks)
            for bb in original_blocks:
                switch.add_case(i32.constant(block_state[hash(bb)]), bb)

        # Create per-block dominator index globals and insert key update calls
        for idx, bb in enumerate(original_blocks):
            # Find blocks dominated by this block
            dominated = dominates.get(hash(bb), set())
            dom_indices = []
            for j, bb2 in enumerate(original_blocks):
                if hash(bb2) in dominated:
                    dom_indices.append(j)

            if dom_indices:
                # Create global array of dominated block indices
                arr_ty = i32.array(len(dom_indices))
                dom_gv = func.module.add_global(
                    arr_ty, f".cff.dom.{func.name}.{idx}")
                dom_consts = [i32.constant(di) for di in dom_indices]
                dom_gv.initializer = llvm.const_array(i32, dom_consts)
                dom_gv.linkage = llvm.Linkage.Private

            term = bb.terminator
            if term is None:
                continue

            with bb.create_builder() as builder:
                builder.position_before(term)
                # Load visited flag
                vis_ptr = builder.gep(i8, visited_array,
                                      [i8.constant(idx)], "cff.vptr")
                vis_flag = builder.load(i8, vis_ptr, "cff.vis")

                if dom_indices:
                    # Call update_key(visited, dom_count, dom_array, key_array, key)
                    builder.call(update_func,
                                 [vis_flag, i32.constant(len(dom_indices)),
                                  dom_gv, key_array,
                                  i32.constant(key_list[idx])], "")

                # Mark as visited
                builder.store(i8.constant(1), vis_ptr)

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
            bb_idx = block_index.get(hash(bb))
            bb_key = key_map[bb_idx] if bb_idx is not None else 0

            if len(succs) == 1:
                target = succs[0]
                target_state = block_state.get(hash(target))
                if target_state is not None:
                    fix_num = (target_state ^ bb_key) & 0xFFFFFFFF
                    with bb.create_builder() as builder:
                        builder.position_before(terminator)
                        if bb_idx is not None:
                            key_ptr = builder.gep(
                                i32, key_array,
                                [i32.constant(bb_idx)], "cff.kptr")
                            key_val = builder.load(i32, key_ptr, "cff.kval")
                            encrypted = builder.xor(
                                key_val, i32.constant(fix_num), "cff.enc")
                            builder.store(encrypted, state_var)
                        else:
                            # Entry block: no key, store plaintext
                            builder.store(i32.constant(target_state), state_var)
                        builder.br(dispatch_bb)
                    terminator.erase_from_parent()

            elif len(succs) == 2:
                true_bb = succs[0]
                false_bb = succs[1]
                true_state = block_state.get(hash(true_bb))
                false_state = block_state.get(hash(false_bb))

                if true_state is not None and false_state is not None:
                    condition = terminator.condition
                    fix_true = (true_state ^ bb_key) & 0xFFFFFFFF
                    fix_false = (false_state ^ bb_key) & 0xFFFFFFFF

                    with bb.create_builder() as builder:
                        builder.position_before(terminator)
                        selected = builder.select(
                            condition,
                            i32.constant(fix_true),
                            i32.constant(fix_false),
                            "cff.sel",
                        )
                        if bb_idx is not None:
                            key_ptr = builder.gep(
                                i32, key_array,
                                [i32.constant(bb_idx)], "cff.kptr")
                            key_val = builder.load(i32, key_ptr, "cff.kval")
                            encrypted = builder.xor(
                                key_val, selected, "cff.enc")
                            builder.store(encrypted, state_var)
                        else:
                            builder.store(selected, state_var)
                        builder.br(dispatch_bb)
                    terminator.erase_from_parent()

        # Demote registers used across blocks to stack (required after flattening)
        demote_regs_to_stack(func)

        return True

    def _unique_state(self, used: set[int]) -> int:
        while True:
            state = self.rng.get_uint32() & 0x7FFFFFFF
            if state not in used and state >= 0x000F0000:
                used.add(state)
                return state

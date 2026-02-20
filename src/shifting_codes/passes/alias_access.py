"""Alias Access Pass — port of Polaris AliasAccess.cpp.

Obscures local variable access through pointer aliasing and multi-level
struct indirection.  Original allocas are hidden inside randomly-built
struct types and accessed via a graph of transition nodes with getter
functions, making static analysis of stack variable access much harder.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import llvm

from shifting_codes.passes import PassRegistry
from shifting_codes.passes.base import FunctionPass, PassInfo
from shifting_codes.utils.crypto import CryptoRandom

BRANCH_NUM = 6  # Slots per transition node struct


@dataclass
class ElementPos:
    struct_type: llvm.Type
    index: int


@dataclass
class ReferenceNode:
    alloca: llvm.Value              # struct alloca for this node
    is_raw: bool
    node_id: int
    raw_insts: dict = field(default_factory=dict)   # {orig_alloca: ElementPos}
    edges: dict = field(default_factory=dict)        # {slot_idx: ReferenceNode}
    path: dict = field(default_factory=dict)          # {orig_alloca: [slot_indices]}


@PassRegistry.register
class AliasAccessPass(FunctionPass):

    def __init__(self, rng: CryptoRandom | None = None):
        self.rng = rng or CryptoRandom()
        self._counter = 0

    @classmethod
    def info(cls) -> PassInfo:
        return PassInfo(
            name="alias_access",
            description="[Polaris] Obscure stack variable access via struct indirection",
        )

    def run_on_function(self, func: llvm.Function, ctx: llvm.Context) -> bool:
        # Step 1: Collect allocas
        allocas = []
        for bb in func.basic_blocks:
            for inst in bb.instructions:
                if inst.opcode == llvm.Opcode.Alloca:
                    allocas.append(inst)

        if not allocas:
            return False

        self._counter += 1
        tag = self._counter
        mod = func.module
        ptr_ty = ctx.types.ptr
        i32 = ctx.types.i32
        i64 = ctx.types.i64

        # Step 2: Randomly distribute allocas into buckets
        n = len(allocas)
        buckets: list[list] = [[] for _ in range(n)]
        for a in allocas:
            idx = self.rng.get_range(n)
            buckets[idx].append(a)

        # Step 3: Build raw nodes from non-empty buckets
        raw_nodes: list[ReferenceNode] = []
        entry_bb = list(func.basic_blocks)[0]
        first_inst = list(entry_bb.instructions)[0]

        for bucket in buckets:
            if not bucket:
                continue

            # Create struct with alloca types at random positions + pointer padding
            field_count = 2 * len(bucket) + 1
            field_types = [ptr_ty] * field_count
            positions: dict = {}

            for alloca in bucket:
                # Pick random unused position
                while True:
                    pos = self.rng.get_range(field_count)
                    if pos not in positions:
                        break
                field_types[pos] = alloca.allocated_type
                positions[alloca] = pos

            struct_ty = ctx.types.struct(field_types)

            # Allocate the struct in entry block
            with entry_bb.create_builder() as builder:
                builder.position_before(first_inst)
                struct_alloca = builder.alloca(struct_ty, name=f"aa.raw.{tag}.{len(raw_nodes)}")

            node = ReferenceNode(
                alloca=struct_alloca,
                is_raw=True,
                node_id=len(raw_nodes),
            )
            for alloca, pos in positions.items():
                node.raw_insts[alloca] = ElementPos(struct_type=struct_ty, index=pos)
            raw_nodes.append(node)

        # Step 4: Build transition nodes
        trans_struct_ty = ctx.types.struct([ptr_ty] * BRANCH_NUM)
        trans_count = len(raw_nodes) * 3
        all_nodes: list[ReferenceNode] = list(raw_nodes)
        trans_nodes: list[ReferenceNode] = []

        for t in range(trans_count):
            with entry_bb.create_builder() as builder:
                builder.position_before(first_inst)
                trans_alloca = builder.alloca(
                    trans_struct_ty, name=f"aa.trans.{tag}.{t}",
                )

            node = ReferenceNode(
                alloca=trans_alloca,
                is_raw=False,
                node_id=len(all_nodes),
            )

            # Randomly connect to existing nodes
            num_edges = self.rng.get_range(BRANCH_NUM) + 1
            available_slots = list(range(BRANCH_NUM))
            for _ in range(min(num_edges, len(all_nodes))):
                if not available_slots:
                    break
                slot = available_slots.pop(self.rng.get_range(len(available_slots)))
                target = all_nodes[self.rng.get_range(len(all_nodes))]
                node.edges[slot] = target

                # Store pointer to target node in the transition struct
                with entry_bb.create_builder() as builder:
                    builder.position_before(first_inst)
                    slot_ptr = builder.gep(
                        trans_struct_ty, trans_alloca,
                        [i32.constant(0), i32.constant(slot)],
                        f"aa.edge.{tag}.{t}.{slot}",
                    )
                    builder.store(target.alloca, slot_ptr)

                # Propagate paths
                if target.is_raw:
                    for orig_alloca in target.raw_insts:
                        if orig_alloca not in node.path:
                            node.path[orig_alloca] = [slot]
                        # else: already have a path, keep first
                else:
                    for orig_alloca, path in target.path.items():
                        if orig_alloca not in node.path:
                            node.path[orig_alloca] = [slot] + path

            trans_nodes.append(node)
            all_nodes.append(node)

        # Step 5: Create getter functions (one per slot index)
        getter_funcs: dict[int, llvm.Function] = {}
        for slot_idx in range(BRANCH_NUM):
            getter_name = f"__obfu_aa_getter_{tag}_{slot_idx}"
            getter_fn_ty = ctx.types.function(ptr_ty, [ptr_ty])
            getter = mod.add_function(getter_name, getter_fn_ty)
            getter.linkage = llvm.Linkage.Private

            getter_entry = getter.append_basic_block("entry")
            with getter_entry.create_builder() as builder:
                gep = builder.gep(
                    trans_struct_ty, getter.get_param(0),
                    [i32.constant(0), i32.constant(slot_idx)],
                    "aa.getter.gep",
                )
                loaded = builder.load(ptr_ty, gep, "aa.getter.load")
                builder.ret(loaded)

            getter_funcs[slot_idx] = getter

        # Step 6: Replace operand references
        for orig_alloca in allocas:
            uses = list(orig_alloca.uses)
            for use in uses:
                user_inst = use.user
                operand_idx = use.operand_index

                # Find a node that has a path to this alloca
                # Prefer transition nodes (more indirection)
                source_node = None
                for node in trans_nodes:
                    if orig_alloca in node.path:
                        source_node = node
                        break
                if source_node is None:
                    # Fall back to raw node
                    for node in raw_nodes:
                        if orig_alloca in node.raw_insts:
                            source_node = node
                            break
                if source_node is None:
                    continue

                with user_inst.block.create_builder() as builder:
                    builder.position_before(user_inst)

                    if source_node.is_raw:
                        # Direct GEP to the element
                        elem = source_node.raw_insts[orig_alloca]
                        replacement = builder.gep(
                            elem.struct_type, source_node.alloca,
                            [i32.constant(0), i32.constant(elem.index)],
                            "aa.direct",
                        )
                    else:
                        # Walk the path: call getters to traverse graph
                        path = source_node.path[orig_alloca]
                        current_ptr = source_node.alloca
                        for slot_idx in path:
                            getter = getter_funcs[slot_idx]
                            current_ptr = builder.call(
                                getter, [current_ptr], "aa.walk",
                            )

                        # current_ptr is now the raw node; GEP to element
                        # Find which raw node we ended up at
                        target_node = source_node
                        for slot_idx in path:
                            target_node = target_node.edges[slot_idx]

                        if target_node.is_raw and orig_alloca in target_node.raw_insts:
                            elem = target_node.raw_insts[orig_alloca]
                            replacement = builder.gep(
                                elem.struct_type, current_ptr,
                                [i32.constant(0), i32.constant(elem.index)],
                                "aa.elem",
                            )
                        else:
                            # Fallback: shouldn't happen with correct paths
                            continue

                user_inst.set_operand(operand_idx, replacement)

        # Step 7: Erase original allocas (all uses should be replaced)
        for alloca in allocas:
            if not alloca.has_uses:
                alloca.erase_from_parent()

        return True

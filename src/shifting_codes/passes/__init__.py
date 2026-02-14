"""Pass registry and pipeline for obfuscation passes."""

from __future__ import annotations

import llvm

from shifting_codes.passes.base import FunctionPass, ModulePass, PassInfo

# Enum attribute kind IDs (stable within an LLVM version).
# optnone: tells the optimizer to skip this function entirely.
# noinline: required alongside optnone (LLVM verifier rejects optnone without it).
_ATTR_NOINLINE = 32
_ATTR_OPTNONE = 49


class PassRegistry:
    """Registry of available obfuscation passes."""

    _passes: dict[str, type[FunctionPass | ModulePass]] = {}

    @classmethod
    def register(cls, pass_cls: type[FunctionPass | ModulePass]) -> type:
        info = pass_cls.info()
        cls._passes[info.name] = pass_cls
        return pass_cls

    @classmethod
    def get(cls, name: str) -> type[FunctionPass | ModulePass] | None:
        return cls._passes.get(name)

    @classmethod
    def all_passes(cls) -> dict[str, type[FunctionPass | ModulePass]]:
        return dict(cls._passes)


class PassPipeline:
    """Ordered pipeline of obfuscation passes."""

    def __init__(self, passes: list[FunctionPass | ModulePass] | None = None):
        self.passes: list[FunctionPass | ModulePass] = passes or []

    def add(self, p: FunctionPass | ModulePass) -> None:
        self.passes.append(p)

    def run(
        self,
        mod: llvm.Module,
        ctx: llvm.Context,
        selected_functions: set[str] | None = None,
    ) -> bool:
        """Run all passes on the module.

        Args:
            mod: The LLVM module to transform.
            ctx: The LLVM context.
            selected_functions: If None, all functions receive FunctionPasses.
                If a set, only named functions receive FunctionPasses.
                ModulePasses always apply to the entire module regardless.
        """
        changed = False
        obfuscated_functions: set[str] = set()
        for p in self.passes:
            if isinstance(p, ModulePass):
                changed |= p.run_on_module(mod, ctx)
            elif isinstance(p, FunctionPass):
                for func in mod.functions:
                    if func.is_declaration:
                        continue
                    if selected_functions is not None and func.name not in selected_functions:
                        continue
                    if p.run_on_function(func, ctx):
                        changed = True
                        obfuscated_functions.add(func.name)

        # Stamp obfuscated functions with optnone + noinline so that
        # downstream compilers (e.g. clang -O2) cannot strip obfuscation.
        if obfuscated_functions:
            optnone = ctx.create_enum_attribute(_ATTR_OPTNONE, 0)
            noinline = ctx.create_enum_attribute(_ATTR_NOINLINE, 0)
            fn_idx = llvm.AttributeFunctionIndex
            for func in mod.functions:
                if func.name in obfuscated_functions:
                    func.add_attribute(fn_idx, optnone)
                    func.add_attribute(fn_idx, noinline)

        return changed

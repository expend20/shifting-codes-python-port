"""Pass registry and pipeline for obfuscation passes."""

from __future__ import annotations

import llvm

from shifting_codes.passes.base import FunctionPass, ModulePass, PassInfo


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

    def run(self, mod: llvm.Module, ctx: llvm.Context) -> bool:
        changed = False
        for p in self.passes:
            if isinstance(p, ModulePass):
                changed |= p.run_on_module(mod, ctx)
            elif isinstance(p, FunctionPass):
                for func in mod.functions:
                    if not func.is_declaration:
                        changed |= p.run_on_function(func, ctx)
        return changed

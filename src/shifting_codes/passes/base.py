"""Base classes for obfuscation passes."""

from abc import ABC, abstractmethod
from dataclasses import dataclass

import llvm


@dataclass(frozen=True)
class PassInfo:
    name: str
    description: str
    is_module_pass: bool = False


class FunctionPass(ABC):
    """Abstract base class for function-level obfuscation passes."""

    @abstractmethod
    def run_on_function(self, func: llvm.Function, ctx: llvm.Context) -> bool:
        """Run the pass on a single function. Returns True if the function was modified."""
        ...

    @classmethod
    @abstractmethod
    def info(cls) -> PassInfo:
        """Return metadata about this pass."""
        ...


class ModulePass(ABC):
    """Abstract base class for module-level obfuscation passes."""

    @abstractmethod
    def run_on_module(self, mod: llvm.Module, ctx: llvm.Context) -> bool:
        """Run the pass on the entire module. Returns True if the module was modified."""
        ...

    @classmethod
    @abstractmethod
    def info(cls) -> PassInfo:
        """Return metadata about this pass."""
        ...

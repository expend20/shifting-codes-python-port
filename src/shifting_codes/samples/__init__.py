"""Sample source files for the UI demo."""

from __future__ import annotations

from importlib import resources


def get_serial_checker_source() -> str:
    """Return the serial checker C source text."""
    return resources.files(__package__).joinpath("serial_checker.c").read_text()

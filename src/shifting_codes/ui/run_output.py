"""Run output panel for displaying compile & run results."""

from __future__ import annotations

from PyQt6.QtGui import QColor, QFont, QTextCharFormat, QTextCursor
from PyQt6.QtWidgets import QPlainTextEdit, QVBoxLayout, QWidget

from shifting_codes.ui.compiler import RunResult


class RunOutputPanel(QWidget):
    """Read-only panel showing compile/run logs and results."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self._text = QPlainTextEdit()
        self._text.setReadOnly(True)
        font = QFont("Consolas", 10)
        font.setStyleHint(QFont.StyleHint.Monospace)
        self._text.setFont(font)
        self._text.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        layout.addWidget(self._text)

        # Default colors (dark theme)
        self._colors = {
            "success": QColor("#4ec9b0"),
            "error": QColor("#f44747"),
            "info": QColor("#569cd6"),
            "text": QColor("#d4d4d4"),
        }

    def set_run_colors(self, colors: dict[str, QColor]):
        self._colors = colors

    def clear(self):
        self._text.clear()

    def append_log(self, msg: str):
        self._append(msg, self._colors["info"])

    def show_error(self, msg: str):
        self._append(f"ERROR: {msg}", self._colors["error"])

    def show_results(
        self, obfuscated: RunResult, original: RunResult | None = None
    ):
        self._append("=" * 60, self._colors["text"])

        if original is not None:
            self._append("--- Original ---", self._colors["info"])
            self._format_result(original)
            self._append("", self._colors["text"])
            self._append("--- Obfuscated ---", self._colors["info"])
            self._format_result(obfuscated)
            self._append("", self._colors["text"])

            # Compare
            match = (
                original.return_value == obfuscated.return_value
                and original.output_buffers == obfuscated.output_buffers
            )
            if match:
                self._append(
                    "MATCH - Obfuscated output matches original!",
                    self._colors["success"],
                )
            else:
                self._append(
                    "MISMATCH - Outputs differ!",
                    self._colors["error"],
                )
                if original.return_value != obfuscated.return_value:
                    self._append(
                        f"  Return: original={original.return_value} vs obfuscated={obfuscated.return_value}",
                        self._colors["error"],
                    )
                for name in set(original.output_buffers) | set(obfuscated.output_buffers):
                    ob = original.output_buffers.get(name, b"")
                    nb = obfuscated.output_buffers.get(name, b"")
                    if ob != nb:
                        self._append(
                            f"  Buffer '{name}': original={ob.hex()} vs obfuscated={nb.hex()}",
                            self._colors["error"],
                        )
        else:
            self._append("--- Obfuscated ---", self._colors["info"])
            self._format_result(obfuscated)

    def _format_result(self, result: RunResult):
        if result.return_value is not None:
            self._append(f"  Return value: {result.return_value}", self._colors["text"])
        for name, buf in result.output_buffers.items():
            self._append(f"  Buffer '{name}': {buf.hex()}", self._colors["text"])
        self._append(f"  Elapsed: {result.elapsed_ms:.2f}ms", self._colors["text"])

    def _append(self, text: str, color: QColor):
        cursor = self._text.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        fmt = QTextCharFormat()
        fmt.setForeground(color)
        cursor.insertText(text + "\n", fmt)
        self._text.setTextCursor(cursor)
        self._text.ensureCursorVisible()

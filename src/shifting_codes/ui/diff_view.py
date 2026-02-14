"""Before/After side-by-side diff view with highlighted changes."""

from __future__ import annotations

import difflib

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QFont, QTextFormat
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QPlainTextEdit,
    QSplitter,
    QTabWidget,
    QTextEdit,
    QWidget,
)

from shifting_codes.ui.ir_editor import IREditor, LLVMIRHighlighter
from shifting_codes.ui.theme import DARK_DIFF, DARK_SYNTAX

from PyQt6.QtWidgets import QLabel, QVBoxLayout as _QVBoxLayout


class DiffEditor(QPlainTextEdit):
    """Read-only text editor that can highlight specific lines with background colors."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        font = QFont("Consolas", 10)
        font.setStyleHint(QFont.StyleHint.Monospace)
        self.setFont(font)
        self.setTabStopDistance(40)
        self.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.setReadOnly(True)
        self._highlighter = LLVMIRHighlighter(self.document())
        self._line_colors: dict[int, QColor] = {}

    def set_line_colors(self, colors: dict[int, QColor]):
        self._line_colors = colors
        self._apply_highlights()

    def set_syntax_colors(self, colors: dict[str, str]):
        self._highlighter.set_colors(colors)

    def _apply_highlights(self):
        selections = []
        block = self.document().begin()
        line = 0
        while block.isValid():
            if line in self._line_colors:
                sel = QTextEdit.ExtraSelection()
                sel.format.setBackground(self._line_colors[line])
                sel.format.setProperty(QTextFormat.Property.FullWidthSelection, True)
                sel.cursor = self.textCursor()
                sel.cursor.setPosition(block.position())
                selections.append(sel)
            block = block.next()
            line += 1
        self.setExtraSelections(selections)


class SideBySideDiff(QWidget):
    """Side-by-side diff view with synchronized scrolling."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        splitter = QSplitter(Qt.Orientation.Horizontal)

        left_panel = QWidget()
        left_layout = _QVBoxLayout(left_panel)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(2)
        left_layout.addWidget(QLabel("Original"))
        self._left = DiffEditor()
        left_layout.addWidget(self._left)

        right_panel = QWidget()
        right_layout = _QVBoxLayout(right_panel)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(2)
        right_layout.addWidget(QLabel("Obfuscated"))
        self._right = DiffEditor()
        right_layout.addWidget(self._right)

        splitter.addWidget(left_panel)
        splitter.addWidget(right_panel)
        splitter.setSizes([500, 500])
        layout.addWidget(splitter)

        self._left.verticalScrollBar().valueChanged.connect(
            self._right.verticalScrollBar().setValue
        )
        self._right.verticalScrollBar().valueChanged.connect(
            self._left.verticalScrollBar().setValue
        )

        self._diff_colors = DARK_DIFF
        self._before = ""
        self._after = ""

    def set_diff_colors(self, diff_colors: dict[str, QColor]):
        self._diff_colors = diff_colors
        if self._before or self._after:
            self.set_diff(self._before, self._after)

    def set_syntax_colors(self, colors: dict[str, str]):
        self._left.set_syntax_colors(colors)
        self._right.set_syntax_colors(colors)

    def set_diff(self, before: str, after: str):
        self._before = before
        self._after = after
        left_lines, right_lines, left_colors, right_colors = _compute_side_by_side(
            before, after, self._diff_colors
        )
        self._left.setPlainText("\n".join(left_lines))
        self._right.setPlainText("\n".join(right_lines))
        self._left.set_line_colors(left_colors)
        self._right.set_line_colors(right_colors)


def _compute_side_by_side(
    before: str, after: str, diff_colors: dict[str, QColor]
) -> tuple[list[str], list[str], dict[int, QColor], dict[int, QColor]]:
    """Compute aligned side-by-side lines with color maps."""
    a_lines = before.splitlines()
    b_lines = after.splitlines()

    removed_bg = diff_colors["removed"]
    added_bg = diff_colors["added"]

    matcher = difflib.SequenceMatcher(None, a_lines, b_lines)

    left: list[str] = []
    right: list[str] = []
    left_colors: dict[int, QColor] = {}
    right_colors: dict[int, QColor] = {}

    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            for i in range(i2 - i1):
                left.append(a_lines[i1 + i])
                right.append(b_lines[j1 + i])
        elif tag == "replace":
            a_chunk = i2 - i1
            b_chunk = j2 - j1
            pairs = max(a_chunk, b_chunk)
            for k in range(pairs):
                row = len(left)
                has_left = k < a_chunk
                has_right = k < b_chunk
                left.append(a_lines[i1 + k] if has_left else "")
                right.append(b_lines[j1 + k] if has_right else "")
                if has_left:
                    left_colors[row] = removed_bg
                if has_right:
                    right_colors[row] = added_bg
        elif tag == "delete":
            for k in range(i2 - i1):
                row = len(left)
                left.append(a_lines[i1 + k])
                right.append("")
                left_colors[row] = removed_bg
        elif tag == "insert":
            for k in range(j2 - j1):
                row = len(left)
                left.append("")
                right.append(b_lines[j1 + k])
                right_colors[row] = added_bg

    return left, right, left_colors, right_colors


class DiffView(QTabWidget):
    """Tabbed view showing Output and side-by-side Diff of IR."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)

        self._after = IREditor(readonly=True)
        self._side_by_side = SideBySideDiff()

        self.addTab(self._after, "Output")
        self.addTab(self._side_by_side, "Diff")

    def set_both(self, before: str, after: str):
        self._after.setPlainText(after)
        self._side_by_side.set_diff(before, after)
        self.setCurrentIndex(1)

    def set_theme(self, syntax_colors: dict[str, str], diff_colors: dict[str, QColor]):
        self._after.set_syntax_colors(syntax_colors)
        self._side_by_side.set_syntax_colors(syntax_colors)
        self._side_by_side.set_diff_colors(diff_colors)

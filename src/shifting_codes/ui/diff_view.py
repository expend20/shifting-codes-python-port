"""Output tab view with obfuscated IR output, unified diff, and build log."""

from __future__ import annotations

import difflib

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QFont, QTextFormat
from PyQt6.QtWidgets import QPlainTextEdit, QTabWidget, QTextEdit, QWidget

from shifting_codes.ui.ir_editor import IREditor
from shifting_codes.ui.run_output import RunOutputPanel
from shifting_codes.ui.theme import DARK_DIFF


class UnifiedDiffEditor(QPlainTextEdit):
    """Read-only editor that displays unified diff with colored lines."""

    # Maximum lines to show in the diff to keep the UI responsive.
    MAX_DIFF_LINES = 5000

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        font = QFont("Consolas", 10)
        font.setStyleHint(QFont.StyleHint.Monospace)
        self.setFont(font)
        self.setTabStopDistance(40)
        self.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.setReadOnly(True)
        self._diff_colors = DARK_DIFF

    def set_diff_colors(self, colors: dict[str, QColor]):
        self._diff_colors = colors
        # Reapply highlights if there's content
        if self.document().blockCount() > 1:
            self._apply_highlights()

    def show_diff(self, before: str, after: str):
        """Compute and display a unified diff."""
        a_lines = before.splitlines(keepends=True)
        b_lines = after.splitlines(keepends=True)

        diff_lines = list(difflib.unified_diff(
            a_lines, b_lines,
            fromfile="original", tofile="obfuscated",
            n=3,
        ))

        truncated = False
        if len(diff_lines) > self.MAX_DIFF_LINES:
            diff_lines = diff_lines[:self.MAX_DIFF_LINES]
            truncated = True

        text = "".join(diff_lines)
        if truncated:
            text += f"\n... (diff truncated at {self.MAX_DIFF_LINES} lines)\n"

        if not text.strip():
            text = "(no differences)"

        self.setPlainText(text)
        self._apply_highlights()

    def _apply_highlights(self):
        removed_bg = self._diff_colors["removed"]
        added_bg = self._diff_colors["added"]

        selections = []
        block = self.document().begin()
        while block.isValid():
            line = block.text()
            color = None
            if line.startswith("-") and not line.startswith("---"):
                color = removed_bg
            elif line.startswith("+") and not line.startswith("+++"):
                color = added_bg

            if color is not None:
                sel = QTextEdit.ExtraSelection()
                sel.format.setBackground(color)
                sel.format.setProperty(QTextFormat.Property.FullWidthSelection, True)
                sel.cursor = self.textCursor()
                sel.cursor.setPosition(block.position())
                selections.append(sel)

            block = block.next()
        self.setExtraSelections(selections)


class DiffView(QTabWidget):
    """Tabbed view showing Output IR, Diff, and Build Log."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)

        self._after = IREditor(readonly=True)
        self._diff_editor = UnifiedDiffEditor()
        self._run_output = RunOutputPanel()

        self.addTab(self._after, "Output")
        self.addTab(self._diff_editor, "Diff")
        self.addTab(self._run_output, "Build Log")

        # Lazy diff: only compute when the tab is shown
        self._before = ""
        self._after_text = ""
        self._diff_dirty = False
        self.currentChanged.connect(self._on_tab_changed)

    @property
    def run_output(self) -> RunOutputPanel:
        return self._run_output

    def show_run_tab(self):
        self.setCurrentWidget(self._run_output)

    def set_both(self, before: str, after: str):
        self._after.setPlainText(after)
        self._before = before
        self._after_text = after
        self._diff_dirty = True
        self.setCurrentIndex(0)

    def set_theme(self, syntax_colors: dict[str, str],
                  diff_colors: dict[str, QColor] | None = None,
                  run_colors: dict[str, QColor] | None = None):
        self._after.set_syntax_colors(syntax_colors)
        if diff_colors:
            self._diff_editor.set_diff_colors(diff_colors)
        if run_colors:
            self._run_output.set_run_colors(run_colors)

    def _on_tab_changed(self, index: int):
        if self.widget(index) is self._diff_editor and self._diff_dirty:
            self._diff_editor.show_diff(self._before, self._after_text)
            self._diff_dirty = False

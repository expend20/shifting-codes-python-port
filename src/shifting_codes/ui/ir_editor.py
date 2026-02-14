"""LLVM IR text editor with syntax highlighting."""

from __future__ import annotations

from PyQt6.QtCore import QRegularExpression
from PyQt6.QtGui import QColor, QFont, QSyntaxHighlighter, QTextCharFormat
from PyQt6.QtWidgets import QPlainTextEdit, QWidget

from shifting_codes.ui.theme import DARK_SYNTAX


class LLVMIRHighlighter(QSyntaxHighlighter):
    """Syntax highlighter for LLVM IR text."""

    def __init__(self, parent=None, colors: dict[str, str] | None = None):
        super().__init__(parent)
        self._colors = colors or DARK_SYNTAX
        self._rules: list[tuple[QRegularExpression, QTextCharFormat]] = []
        self._build_rules()

    def set_colors(self, colors: dict[str, str]):
        self._colors = colors
        self._rules.clear()
        self._build_rules()
        self.rehighlight()

    def _build_rules(self):
        c = self._colors

        # Keywords
        kw_fmt = QTextCharFormat()
        kw_fmt.setForeground(QColor(c["keyword"]))
        kw_fmt.setFontWeight(QFont.Weight.Bold)
        keywords = [
            "define", "declare", "global", "constant", "internal", "private",
            "external", "linkonce_odr", "weak", "appending", "common",
            "dso_local", "unnamed_addr", "align", "nounwind", "readonly",
            "writeonly", "nocapture", "noundef", "signext", "zeroext",
        ]
        for kw in keywords:
            self._rules.append((QRegularExpression(rf"\b{kw}\b"), kw_fmt))

        # Instructions
        inst_fmt = QTextCharFormat()
        inst_fmt.setForeground(QColor(c["instruction"]))
        instructions = [
            "ret", "br", "switch", "indirectbr", "invoke", "unreachable",
            "add", "sub", "mul", "udiv", "sdiv", "urem", "srem",
            "and", "or", "xor", "shl", "lshr", "ashr",
            "alloca", "load", "store", "getelementptr", "fence",
            "icmp", "fcmp", "phi", "select", "call",
            "trunc", "zext", "sext", "fptrunc", "fpext",
            "ptrtoint", "inttoptr", "bitcast",
            "extractvalue", "insertvalue",
        ]
        for inst in instructions:
            self._rules.append((QRegularExpression(rf"\b{inst}\b"), inst_fmt))

        # Types
        type_fmt = QTextCharFormat()
        type_fmt.setForeground(QColor(c["type"]))
        self._rules.append((
            QRegularExpression(r"\b(i\d+|ptr|void|float|double|half|label|metadata)\b"),
            type_fmt,
        ))

        # Numbers
        num_fmt = QTextCharFormat()
        num_fmt.setForeground(QColor(c["number"]))
        self._rules.append((QRegularExpression(r"\b-?\d+\b"), num_fmt))

        # Strings
        str_fmt = QTextCharFormat()
        str_fmt.setForeground(QColor(c["string"]))
        self._rules.append((QRegularExpression(r'"[^"]*"'), str_fmt))

        # Labels (%name, @name)
        label_fmt = QTextCharFormat()
        label_fmt.setForeground(QColor(c["label"]))
        self._rules.append((QRegularExpression(r"[%@][\w.$]+"), label_fmt))

        # Comments
        comment_fmt = QTextCharFormat()
        comment_fmt.setForeground(QColor(c["comment"]))
        self._rules.append((QRegularExpression(r";.*$"), comment_fmt))

    def highlightBlock(self, text: str):
        for pattern, fmt in self._rules:
            it = pattern.globalMatch(text)
            while it.hasNext():
                match = it.next()
                self.setFormat(match.capturedStart(), match.capturedLength(), fmt)


class IREditor(QPlainTextEdit):
    """QPlainTextEdit with LLVM IR syntax highlighting."""

    def __init__(self, parent: QWidget | None = None, readonly: bool = False):
        super().__init__(parent)
        font = QFont("Consolas", 10)
        font.setStyleHint(QFont.StyleHint.Monospace)
        self.setFont(font)
        self.setTabStopDistance(40)
        self.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self._highlighter = LLVMIRHighlighter(self.document())
        if readonly:
            self.setReadOnly(True)

    def set_syntax_colors(self, colors: dict[str, str]):
        self._highlighter.set_colors(colors)

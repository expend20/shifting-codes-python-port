"""C/C++ source editor with syntax highlighting."""

from __future__ import annotations

from PyQt6.QtCore import QRegularExpression
from PyQt6.QtGui import QColor, QFont, QSyntaxHighlighter, QTextCharFormat
from PyQt6.QtWidgets import QPlainTextEdit, QWidget

from shifting_codes.ui.theme import DARK_C_SYNTAX


class CHighlighter(QSyntaxHighlighter):
    """Syntax highlighter for C/C++ source code."""

    def __init__(self, parent=None, colors: dict[str, str] | None = None):
        super().__init__(parent)
        self._colors = colors or DARK_C_SYNTAX
        self._rules: list[tuple[QRegularExpression, QTextCharFormat]] = []
        self._comment_start = QRegularExpression(r"/\*")
        self._comment_end = QRegularExpression(r"\*/")
        self._comment_fmt = QTextCharFormat()
        self._build_rules()

    def set_colors(self, colors: dict[str, str]):
        self._colors = colors
        self._rules.clear()
        self._build_rules()
        self.rehighlight()

    def _build_rules(self):
        c = self._colors

        # @obfuscate annotations
        ann_fmt = QTextCharFormat()
        ann_fmt.setForeground(QColor(c["annotation"]))
        ann_fmt.setFontWeight(QFont.Weight.Bold)
        self._rules.append((QRegularExpression(r"@obfuscate"), ann_fmt))

        # Preprocessor directives
        pp_fmt = QTextCharFormat()
        pp_fmt.setForeground(QColor(c["preprocessor"]))
        self._rules.append((QRegularExpression(r"^\s*#\w+.*$"), pp_fmt))

        # Keywords (storage, qualifier, other)
        kw_fmt = QTextCharFormat()
        kw_fmt.setForeground(QColor(c["keyword"]))
        kw_fmt.setFontWeight(QFont.Weight.Bold)
        keywords = [
            "auto", "break", "case", "const", "continue", "default",
            "do", "enum", "extern", "goto", "inline",
            "register", "restrict", "return", "signed", "sizeof",
            "static", "struct", "switch", "typedef", "typeof",
            "union", "unsigned", "void", "volatile",
            # C++ additions
            "class", "constexpr", "decltype", "delete", "explicit",
            "false", "friend", "mutable", "namespace", "new",
            "noexcept", "nullptr", "operator", "override",
            "private", "protected", "public", "template", "this",
            "throw", "true", "try", "catch", "typeid", "typename",
            "using", "virtual",
        ]
        for kw in keywords:
            self._rules.append((QRegularExpression(rf"\b{kw}\b"), kw_fmt))

        # Control flow keywords (different color)
        ctrl_fmt = QTextCharFormat()
        ctrl_fmt.setForeground(QColor(c["control"]))
        ctrl_fmt.setFontWeight(QFont.Weight.Bold)
        control = ["if", "else", "for", "while", "do", "switch", "case",
                    "break", "continue", "return", "goto", "default"]
        for kw in control:
            self._rules.append((QRegularExpression(rf"\b{kw}\b"), ctrl_fmt))

        # Types
        type_fmt = QTextCharFormat()
        type_fmt.setForeground(QColor(c["type"]))
        types = [
            "int", "char", "short", "long", "float", "double", "void",
            "int8_t", "int16_t", "int32_t", "int64_t",
            "uint8_t", "uint16_t", "uint32_t", "uint64_t",
            "size_t", "ssize_t", "ptrdiff_t", "bool",
            "FILE", "NULL",
        ]
        for t in types:
            self._rules.append((QRegularExpression(rf"\b{t}\b"), type_fmt))

        # Numbers (hex, decimal, float, suffixes)
        num_fmt = QTextCharFormat()
        num_fmt.setForeground(QColor(c["number"]))
        self._rules.append((QRegularExpression(r"\b0[xX][0-9a-fA-F]+[uUlL]*\b"), num_fmt))
        self._rules.append((QRegularExpression(r"\b\d+\.?\d*[eE]?[+-]?\d*[fFlLuU]*\b"), num_fmt))

        # Strings and chars
        str_fmt = QTextCharFormat()
        str_fmt.setForeground(QColor(c["string"]))
        self._rules.append((QRegularExpression(r'"(?:[^"\\]|\\.)*"'), str_fmt))
        self._rules.append((QRegularExpression(r"'(?:[^'\\]|\\.)*'"), str_fmt))

        # Function calls: identifier followed by (
        func_fmt = QTextCharFormat()
        func_fmt.setForeground(QColor(c["function"]))
        self._rules.append((QRegularExpression(r"\b([a-zA-Z_]\w*)\s*(?=\()"), func_fmt))

        # Single-line comments (// ...)
        comment_fmt = QTextCharFormat()
        comment_fmt.setForeground(QColor(c["comment"]))
        comment_fmt.setFontItalic(True)
        self._rules.append((QRegularExpression(r"//[^\n]*"), comment_fmt))

        # Multi-line comment format (used in highlightBlock)
        self._comment_fmt = QTextCharFormat()
        self._comment_fmt.setForeground(QColor(c["comment"]))
        self._comment_fmt.setFontItalic(True)

    def highlightBlock(self, text: str | None):
        if text is None:
            return
        # Apply single-line rules
        for pattern, fmt in self._rules:
            it = pattern.globalMatch(text)
            while it.hasNext():
                match = it.next()
                self.setFormat(match.capturedStart(), match.capturedLength(), fmt)

        # Handle multi-line /* ... */ comments
        self.setCurrentBlockState(0)

        start_index = 0
        if self.previousBlockState() != 1:
            match = self._comment_start.match(text)
            start_index = match.capturedStart() if match.hasMatch() else -1
        else:
            start_index = 0

        while start_index >= 0:
            end_match = self._comment_end.match(text, start_index + 2)
            if end_match.hasMatch():
                end_index = end_match.capturedEnd()
                length = end_index - start_index
            else:
                self.setCurrentBlockState(1)
                length = len(text) - start_index

            self.setFormat(start_index, length, self._comment_fmt)

            next_match = self._comment_start.match(text, start_index + length)
            start_index = next_match.capturedStart() if next_match.hasMatch() else -1


class SourceEditor(QPlainTextEdit):
    """QPlainTextEdit with C/C++ syntax highlighting."""

    def __init__(self, parent: QWidget | None = None, readonly: bool = True):
        super().__init__(parent)
        font = QFont("Consolas", 10)
        font.setStyleHint(QFont.StyleHint.Monospace)
        self.setFont(font)
        self.setTabStopDistance(40)
        self.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self._highlighter = CHighlighter(self.document())
        if readonly:
            self.setReadOnly(True)

    def set_syntax_colors(self, colors: dict[str, str]):
        self._highlighter.set_colors(colors)

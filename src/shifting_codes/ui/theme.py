"""Dark/Light theme definitions."""

from __future__ import annotations

from PyQt6.QtGui import QColor

DARK_STYLESHEET = """
QMainWindow, QWidget {
    background-color: #1e1e1e;
    color: #d4d4d4;
}
QPlainTextEdit, QListWidget {
    background-color: #252526;
    color: #d4d4d4;
    border: 1px solid #3c3c3c;
    selection-background-color: #264f78;
    font-family: Consolas, 'Courier New', monospace;
}
QTabWidget::pane {
    border: 1px solid #3c3c3c;
}
QTabBar::tab {
    background-color: #2d2d2d;
    color: #d4d4d4;
    padding: 6px 16px;
    border: 1px solid #3c3c3c;
    border-bottom: none;
}
QTabBar::tab:selected {
    background-color: #1e1e1e;
    border-bottom: 2px solid #569cd6;
}
QPushButton {
    background-color: #0e639c;
    color: #ffffff;
    border: none;
    padding: 5px 14px;
    border-radius: 2px;
}
QPushButton:hover {
    background-color: #1177bb;
}
QPushButton:pressed {
    background-color: #0d5689;
}
QPushButton:disabled {
    background-color: #3c3c3c;
    color: #6c6c6c;
}
QLabel {
    color: #cccccc;
}
QStatusBar {
    background-color: #007acc;
    color: #ffffff;
}
QSplitter::handle {
    background-color: #3c3c3c;
}
QListWidget::item {
    padding: 3px;
}
QListWidget::item:selected {
    background-color: #264f78;
}
QScrollBar:vertical {
    background-color: #1e1e1e;
    width: 12px;
}
QScrollBar::handle:vertical {
    background-color: #424242;
    min-height: 20px;
    border-radius: 3px;
}
QScrollBar::handle:vertical:hover {
    background-color: #4f4f4f;
}
QScrollBar:horizontal {
    background-color: #1e1e1e;
    height: 12px;
}
QScrollBar::handle:horizontal {
    background-color: #424242;
    min-width: 20px;
    border-radius: 3px;
}
QScrollBar::handle:horizontal:hover {
    background-color: #4f4f4f;
}
QScrollBar::add-line, QScrollBar::sub-line {
    width: 0;
    height: 0;
}
QScrollBar::add-page, QScrollBar::sub-page {
    background: none;
}
QSizeGrip {
    background-color: transparent;
    width: 12px;
    height: 12px;
}
QStatusBar QSizeGrip {
    background-color: transparent;
}
QToolTip {
    background-color: #252526;
    color: #d4d4d4;
    border: 1px solid #3c3c3c;
}
"""

LIGHT_STYLESHEET = """
QMainWindow, QWidget {
    background-color: #f3f3f3;
    color: #1e1e1e;
}
QPlainTextEdit, QListWidget {
    background-color: #ffffff;
    color: #1e1e1e;
    border: 1px solid #c8c8c8;
    selection-background-color: #add6ff;
    font-family: Consolas, 'Courier New', monospace;
}
QTabWidget::pane {
    border: 1px solid #c8c8c8;
}
QTabBar::tab {
    background-color: #ececec;
    color: #1e1e1e;
    padding: 6px 16px;
    border: 1px solid #c8c8c8;
    border-bottom: none;
}
QTabBar::tab:selected {
    background-color: #ffffff;
    border-bottom: 2px solid #0078d4;
}
QPushButton {
    background-color: #0078d4;
    color: #ffffff;
    border: none;
    padding: 5px 14px;
    border-radius: 2px;
}
QPushButton:hover {
    background-color: #106ebe;
}
QPushButton:pressed {
    background-color: #005a9e;
}
QPushButton:disabled {
    background-color: #c8c8c8;
    color: #888888;
}
QLabel {
    color: #333333;
}
QStatusBar {
    background-color: #0078d4;
    color: #ffffff;
}
QSplitter::handle {
    background-color: #c8c8c8;
}
QListWidget::item {
    padding: 3px;
}
QListWidget::item:selected {
    background-color: #add6ff;
}
QScrollBar:vertical {
    background-color: #f3f3f3;
    width: 12px;
}
QScrollBar::handle:vertical {
    background-color: #c1c1c1;
    min-height: 20px;
    border-radius: 3px;
}
QScrollBar::handle:vertical:hover {
    background-color: #a0a0a0;
}
QScrollBar:horizontal {
    background-color: #f3f3f3;
    height: 12px;
}
QScrollBar::handle:horizontal {
    background-color: #c1c1c1;
    min-width: 20px;
    border-radius: 3px;
}
QScrollBar::handle:horizontal:hover {
    background-color: #a0a0a0;
}
QScrollBar::add-line, QScrollBar::sub-line {
    width: 0;
    height: 0;
}
QScrollBar::add-page, QScrollBar::sub-page {
    background: none;
}
QSizeGrip {
    background-color: transparent;
    width: 12px;
    height: 12px;
}
QStatusBar QSizeGrip {
    background-color: transparent;
}
QToolTip {
    background-color: #f3f3f3;
    color: #1e1e1e;
    border: 1px solid #c8c8c8;
}
"""

# Syntax highlighting colors per theme
DARK_SYNTAX = {
    "keyword": "#569CD6",
    "instruction": "#C586C0",
    "type": "#4EC9B0",
    "number": "#B5CEA8",
    "string": "#CE9178",
    "label": "#DCDCAA",
    "comment": "#6A9955",
}

LIGHT_SYNTAX = {
    "keyword": "#0000FF",
    "instruction": "#AF00DB",
    "type": "#267F99",
    "number": "#098658",
    "string": "#A31515",
    "label": "#795E26",
    "comment": "#008000",
}

# Diff highlight colors per theme
DARK_DIFF = {
    "removed": QColor(80, 30, 30),
    "added": QColor(30, 70, 30),
}

LIGHT_DIFF = {
    "removed": QColor(255, 220, 220),
    "added": QColor(220, 255, 220),
}

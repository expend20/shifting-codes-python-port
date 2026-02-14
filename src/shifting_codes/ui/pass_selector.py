"""Pass selector widget with checkboxes and reordering."""

from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from shifting_codes.passes import PassRegistry
from shifting_codes.passes.base import FunctionPass, ModulePass


class PassSelector(QWidget):
    """Widget for selecting and ordering obfuscation passes."""

    apply_requested = pyqtSignal(list)  # Emits list of (name, pass_class) tuples

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._setup_ui()
        self._populate()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self._list = QListWidget()
        layout.addWidget(self._list)

        btn_layout = QHBoxLayout()
        self._up_btn = QPushButton("Move Up")
        self._down_btn = QPushButton("Move Down")
        self._apply_btn = QPushButton("Apply Selected Passes")

        btn_layout.addWidget(self._up_btn)
        btn_layout.addWidget(self._down_btn)
        btn_layout.addStretch()
        btn_layout.addWidget(self._apply_btn)
        layout.addLayout(btn_layout)

        self._up_btn.clicked.connect(self._move_up)
        self._down_btn.clicked.connect(self._move_down)
        self._apply_btn.clicked.connect(self._on_apply)

    @staticmethod
    def _display_name(name: str) -> str:
        return name.replace("_", " ").title()

    def _populate(self):
        for name, pass_cls in PassRegistry.all_passes().items():
            info = pass_cls.info()
            item = QListWidgetItem(self._display_name(info.name))
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Unchecked)
            item.setData(Qt.ItemDataRole.UserRole, name)
            item.setToolTip(info.description)
            self._list.addItem(item)

    def _move_up(self):
        row = self._list.currentRow()
        if row > 0:
            item = self._list.takeItem(row)
            self._list.insertItem(row - 1, item)
            self._list.setCurrentRow(row - 1)

    def _move_down(self):
        row = self._list.currentRow()
        if 0 <= row < self._list.count() - 1:
            item = self._list.takeItem(row)
            self._list.insertItem(row + 1, item)
            self._list.setCurrentRow(row + 1)

    def _on_apply(self):
        selected = []
        for i in range(self._list.count()):
            item = self._list.item(i)
            if item.checkState() == Qt.CheckState.Checked:
                name = item.data(Qt.ItemDataRole.UserRole)
                pass_cls = PassRegistry.get(name)
                if pass_cls:
                    selected.append((name, pass_cls))
        self.apply_requested.emit(selected)

    def get_selected_pass_names(self) -> list[str]:
        """Return ordered list of checked pass names."""
        result = []
        for i in range(self._list.count()):
            item = self._list.item(i)
            if item.checkState() == Qt.CheckState.Checked:
                result.append(item.data(Qt.ItemDataRole.UserRole))
        return result

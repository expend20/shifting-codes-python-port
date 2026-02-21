"""Widget for selecting which functions to obfuscate."""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QCheckBox,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QStyle,
    QStyledItemDelegate,
    QVBoxLayout,
    QWidget,
)


class _NoFocusDelegate(QStyledItemDelegate):
    """Item delegate that suppresses the focus rectangle."""

    def paint(self, painter, option, index):
        option.state &= ~QStyle.StateFlag.State_HasFocus
        super().paint(painter, option, index)


class FunctionSelector(QWidget):
    """Checkable list of IR function names for selective obfuscation."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._updating_select_all = False
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 6, 0)
        layout.setSpacing(2)

        layout.addWidget(QLabel("Functions to Obfuscate"))

        self._select_all = QCheckBox("Select All")
        self._select_all.stateChanged.connect(self._on_select_all_changed)
        layout.addWidget(self._select_all)

        self._list = QListWidget()
        self._list.setItemDelegate(_NoFocusDelegate(self._list))
        self._list.itemChanged.connect(self._on_item_changed)
        layout.addWidget(self._list)

        note = QLabel("Module passes always apply to the whole module")
        note.setStyleSheet("font-size: 10px; font-style: italic; opacity: 0.7;")
        layout.addWidget(note)

    def set_functions(
        self,
        names: list[str],
        annotated_names: set[str] | None = None,
    ) -> None:
        """Populate the list with function names.

        Args:
            names: IR function names (declarations already filtered out).
            annotated_names: If provided, only these are pre-checked.
                If None, all functions are pre-checked (IR-only mode).
        """
        self._list.clear()
        for name in names:
            # VM interpreter created by VirtualizationPass — always check it
            # so subsequent passes obfuscate it automatically.
            is_vm = name.startswith("__vm_")
            display = f"{name}  [VM interpreter]" if is_vm else name
            item = QListWidgetItem(display)
            item.setData(Qt.ItemDataRole.UserRole, name)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            if is_vm:
                item.setCheckState(Qt.CheckState.Checked)
                item.setToolTip(
                    "Created by VirtualizationPass — select obfuscation "
                    "passes and apply again to harden the interpreter"
                )
            elif annotated_names is None:
                item.setCheckState(Qt.CheckState.Checked)
            elif name in annotated_names or any(
                ann in name for ann in annotated_names
            ):
                # Substring match handles C++ name mangling:
                # annotation "encrypt" matches IR "_Z7encrypti"
                item.setCheckState(Qt.CheckState.Checked)
            else:
                item.setCheckState(Qt.CheckState.Unchecked)
            self._list.addItem(item)
        self._sync_select_all()

    def get_selected_names(self) -> set[str]:
        """Return the set of checked function names."""
        result: set[str] = set()
        for i in range(self._list.count()):
            item = self._list.item(i)
            if item.checkState() == Qt.CheckState.Checked:
                # Use stored name (UserRole) which is the real IR name,
                # falling back to display text for backwards compat.
                name = item.data(Qt.ItemDataRole.UserRole) or item.text()
                result.add(name)
        return result

    def _on_select_all_changed(self, state):
        if self._updating_select_all:
            return
        check = Qt.CheckState.Checked if state == Qt.CheckState.Checked.value else Qt.CheckState.Unchecked
        for i in range(self._list.count()):
            self._list.item(i).setCheckState(check)

    def _on_item_changed(self):
        self._sync_select_all()

    def _sync_select_all(self):
        """Update the Select All checkbox to reflect the list state."""
        count = self._list.count()
        if count == 0:
            return
        checked = sum(
            1 for i in range(count)
            if self._list.item(i).checkState() == Qt.CheckState.Checked
        )
        self._updating_select_all = True
        if checked == count:
            self._select_all.setCheckState(Qt.CheckState.Checked)
        elif checked == 0:
            self._select_all.setCheckState(Qt.CheckState.Unchecked)
        else:
            self._select_all.setCheckState(Qt.CheckState.PartiallyChecked)
        self._updating_select_all = False

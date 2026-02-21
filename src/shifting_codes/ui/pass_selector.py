"""Pass selector widget with checkboxes and reordering."""

from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox,
    QHBoxLayout,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QStyle,
    QStyledItemDelegate,
    QVBoxLayout,
    QWidget,
)

from shifting_codes.passes import PassRegistry
from shifting_codes.passes.base import FunctionPass, ModulePass


class _NoFocusDelegate(QStyledItemDelegate):
    """Item delegate that suppresses the focus rectangle."""

    def paint(self, painter, option, index):
        option.state &= ~QStyle.StateFlag.State_HasFocus
        super().paint(painter, option, index)


class PassSelector(QWidget):
    """Widget for selecting and ordering obfuscation passes."""

    apply_requested = pyqtSignal(list)  # Emits list of (name, pass_class) tuples

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._updating_select_all = False
        self._setup_ui()
        self._populate()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)

        self._select_all = QCheckBox("Select All")
        self._select_all.stateChanged.connect(self._on_select_all_changed)
        layout.addWidget(self._select_all)

        self._list = QListWidget()
        self._list.setItemDelegate(_NoFocusDelegate(self._list))
        self._list.itemChanged.connect(self._on_item_changed)
        layout.addWidget(self._list)

        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(6)
        btn_layout.setContentsMargins(0, 4, 0, 0)
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
        # Strip variant suffixes — the [Pluto]/[Polaris] tag from the
        # description is appended separately by _populate().
        for suffix in ("_pluto", "_polaris"):
            if name.endswith(suffix):
                name = name[: -len(suffix)]
                break
        return name.replace("_", " ").title()

    # Preferred display order for passes in the UI.
    _PASS_ORDER = [
        "global_encryption",
        "global_encryption_pluto",
        "bogus_control_flow",
        "bogus_control_flow_pluto",
        "indirect_call",
        "indirect_call_pluto",
        "indirect_branch",
        "mba_obfuscation",
        "flattening",
        "flattening_pluto",
        "substitution",
        "string_encryption",
        "merge_function",
        "alias_access",
        "custom_cc",
        "anti_disassembly",
    ]

    def _populate(self):
        all_passes = PassRegistry.all_passes()
        # Show passes in the preferred order, then any new/unknown ones.
        ordered = [n for n in self._PASS_ORDER if n in all_passes]
        ordered += [n for n in all_passes if n not in self._PASS_ORDER]
        for name in ordered:
            pass_cls = all_passes[name]
            info = pass_cls.info()
            label = self._display_name(info.name)
            # Pull [Pluto]/[Polaris] tag from description if present
            desc = info.description
            if desc.startswith("["):
                tag_end = desc.find("]")
                if tag_end != -1:
                    label += "  " + desc[: tag_end + 1]
            item = QListWidgetItem(label)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Unchecked)
            item.setData(Qt.ItemDataRole.UserRole, name)
            item.setToolTip(desc)
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

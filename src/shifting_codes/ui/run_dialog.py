"""Dialog for configuring function call arguments before compile & run."""

from __future__ import annotations

import struct

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from shifting_codes.ui.compiler import ArgValue, IRFunction, discover_functions


# XTEA test vectors for auto-detection
_XTEA_KEY = [0x01234567, 0x89ABCDEF, 0xFEDCBA98, 0x76543210]
_XTEA_V = [0xDEADBEEF, 0xCAFEBABE]
_XTEA_ROUNDS = 32


class _ArgWidget(QWidget):
    """Widget for editing a single function argument."""

    def __init__(self, param_name: str, type_str: str, parent=None):
        super().__init__(parent)
        self.param_name = param_name
        self.type_str = type_str

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        if type_str == "ptr":
            self._hex_edit = QLineEdit()
            self._hex_edit.setPlaceholderText("hex bytes (e.g. deadbeef)")
            self._size_spin = QSpinBox()
            self._size_spin.setRange(1, 4096)
            self._size_spin.setValue(8)
            self._size_spin.setSuffix(" bytes")
            layout.addWidget(self._hex_edit, stretch=3)
            layout.addWidget(QLabel("size:"))
            layout.addWidget(self._size_spin, stretch=1)
        else:
            self._value_edit = QLineEdit()
            self._value_edit.setPlaceholderText("decimal or 0x hex")
            layout.addWidget(self._value_edit)

    def get_arg_value(self) -> ArgValue:
        if self.type_str == "ptr":
            hex_str = self._hex_edit.text().strip().replace(" ", "")
            buf = bytes.fromhex(hex_str) if hex_str else b"\x00" * self._size_spin.value()
            return ArgValue(
                param_name=self.param_name,
                type_str=self.type_str,
                value=buf,
                buffer_size=len(buf),
            )
        else:
            text = self._value_edit.text().strip()
            val = int(text, 0) if text else 0
            return ArgValue(
                param_name=self.param_name,
                type_str=self.type_str,
                value=val,
            )

    def set_hex(self, data: bytes):
        """Set value for a ptr arg from bytes."""
        if self.type_str == "ptr":
            self._hex_edit.setText(data.hex())
            self._size_spin.setValue(len(data))

    def set_int(self, value: int):
        """Set value for a scalar arg."""
        if self.type_str != "ptr":
            self._value_edit.setText(str(value))


class RunConfigDialog(QDialog):
    """Dialog for selecting a function and configuring arguments."""

    def __init__(self, ir_text: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Compile & Run")
        self.setMinimumWidth(500)

        self._ir_text = ir_text
        self._functions = discover_functions(ir_text)
        self._arg_widgets: list[_ArgWidget] = []

        self._setup_ui()

        if self._functions:
            self._on_function_changed(0)

    def _setup_ui(self):
        layout = QVBoxLayout(self)

        # Function selector
        func_layout = QHBoxLayout()
        func_layout.addWidget(QLabel("Function:"))
        self._func_combo = QComboBox()
        for f in self._functions:
            sig = f"{f.return_type} @{f.name}({', '.join(f.param_types)})"
            self._func_combo.addItem(sig)
        func_layout.addWidget(self._func_combo, stretch=1)
        layout.addLayout(func_layout)

        # Argument form (dynamically populated)
        self._arg_form = QFormLayout()
        layout.addLayout(self._arg_form)

        # Compare checkbox
        self._compare_cb = QCheckBox("Compare original vs obfuscated")
        self._compare_cb.setChecked(True)
        layout.addWidget(self._compare_cb)

        # Buttons
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self._func_combo.currentIndexChanged.connect(self._on_function_changed)

    def _on_function_changed(self, index: int):
        # Clear old arg widgets
        while self._arg_form.rowCount() > 0:
            self._arg_form.removeRow(0)
        self._arg_widgets.clear()

        if index < 0 or index >= len(self._functions):
            return

        func = self._functions[index]
        for pname, ptype in zip(func.param_names, func.param_types):
            w = _ArgWidget(pname, ptype)
            self._arg_widgets.append(w)
            self._arg_form.addRow(f"%{pname} ({ptype}):", w)

        # Auto-detect XTEA and pre-fill
        if func.name == "xtea_encrypt" and len(func.param_types) == 3:
            self._prefill_xtea()

    def _prefill_xtea(self):
        """Pre-fill XTEA test vector values."""
        if len(self._arg_widgets) >= 3:
            # v buffer: two uint32 values
            v_bytes = struct.pack("<2I", *_XTEA_V)
            self._arg_widgets[0].set_hex(v_bytes)

            # key buffer: four uint32 values
            key_bytes = struct.pack("<4I", *_XTEA_KEY)
            self._arg_widgets[1].set_hex(key_bytes)

            # num_rounds
            self._arg_widgets[2].set_int(_XTEA_ROUNDS)

    def get_result(self) -> tuple[IRFunction, list[ArgValue], bool] | None:
        """Return (function, args, compare) or None if no function selected."""
        idx = self._func_combo.currentIndex()
        if idx < 0 or idx >= len(self._functions):
            return None

        func = self._functions[idx]
        args = [w.get_arg_value() for w in self._arg_widgets]
        compare = self._compare_cb.isChecked()
        return func, args, compare

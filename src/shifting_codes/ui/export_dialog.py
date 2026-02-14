"""Dialog for choosing export type and output path."""

from __future__ import annotations

import platform

from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
)


class ExportDialog(QDialog):
    """Dialog that lets the user pick Object File or Executable and an output path."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Export")
        self.setMinimumWidth(450)

        is_windows = platform.system() == "Windows"
        self._obj_ext = ".obj" if is_windows else ".o"
        self._exe_ext = ".exe" if is_windows else ""

        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)

        # Export type
        type_layout = QHBoxLayout()
        type_layout.addWidget(QLabel("Export as:"))
        self._type_combo = QComboBox()
        self._type_combo.addItem(f"Executable ({self._exe_ext or 'ELF'})", "executable")
        self._type_combo.addItem(f"Object File ({self._obj_ext})", "object")
        type_layout.addWidget(self._type_combo, stretch=1)
        layout.addLayout(type_layout)

        # Output path
        path_layout = QHBoxLayout()
        path_layout.addWidget(QLabel("Output:"))
        self._path_edit = QLineEdit()
        self._path_edit.setPlaceholderText("Choose output file...")
        path_layout.addWidget(self._path_edit, stretch=1)
        self._browse_btn = QPushButton("Browse...")
        path_layout.addWidget(self._browse_btn)
        layout.addLayout(path_layout)

        # Buttons
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self._browse_btn.clicked.connect(self._browse)
        self._type_combo.currentIndexChanged.connect(self._on_type_changed)

    def _on_type_changed(self, index: int):
        # Clear path when type changes since extension differs
        self._path_edit.clear()

    def _browse(self):
        export_type = self._type_combo.currentData()
        if export_type == "object":
            ext_filter = f"Object Files (*{self._obj_ext});;All Files (*)"
            default_name = f"output{self._obj_ext}"
        else:
            if self._exe_ext:
                ext_filter = f"Executables (*{self._exe_ext});;All Files (*)"
                default_name = f"output{self._exe_ext}"
            else:
                ext_filter = "All Files (*)"
                default_name = "output"

        path, _ = QFileDialog.getSaveFileName(
            self, "Export To", default_name, ext_filter
        )
        if path:
            self._path_edit.setText(path)

    def get_result(self) -> tuple[str, str] | None:
        """Return (export_type, output_path) or None if no path set."""
        path = self._path_edit.text().strip()
        if not path:
            return None
        export_type = self._type_combo.currentData()
        return export_type, path

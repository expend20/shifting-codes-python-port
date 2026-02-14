"""Main application window."""

from __future__ import annotations

import time

from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtWidgets import (
    QApplication,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QSplitter,
    QStatusBar,
    QVBoxLayout,
    QWidget,
)

import llvm

from shifting_codes.passes import PassPipeline
from shifting_codes.passes.base import FunctionPass, ModulePass

# Import all passes so they register with PassRegistry
import shifting_codes.passes.substitution  # noqa: F401
import shifting_codes.passes.mba_obfuscation  # noqa: F401
import shifting_codes.passes.bogus_control_flow  # noqa: F401
import shifting_codes.passes.flattening  # noqa: F401
import shifting_codes.passes.global_encryption  # noqa: F401
import shifting_codes.passes.indirect_call  # noqa: F401
from shifting_codes.ui.diff_view import DiffView
from shifting_codes.ui.ir_editor import IREditor
from shifting_codes.ui.pass_selector import PassSelector
from shifting_codes.ui.theme import (
    DARK_DIFF, DARK_STYLESHEET, DARK_SYNTAX,
    LIGHT_DIFF, LIGHT_STYLESHEET, LIGHT_SYNTAX,
)
from shifting_codes.utils.crypto import CryptoRandom


class PassWorker(QThread):
    """Worker thread that runs obfuscation passes off the UI thread."""

    finished = pyqtSignal(str, float)  # result_ir, elapsed_seconds
    error = pyqtSignal(str)

    def __init__(self, ir_text: str, pass_classes: list[tuple[str, type]], parent=None):
        super().__init__(parent)
        self.ir_text = ir_text
        self.pass_classes = pass_classes

    def run(self):
        try:
            start = time.perf_counter()
            with llvm.create_context() as ctx:
                with ctx.parse_ir(self.ir_text) as mod:
                    pipeline = PassPipeline()
                    for name, pass_cls in self.pass_classes:
                        rng = CryptoRandom()
                        p = pass_cls(rng=rng)
                        pipeline.add(p)

                    pipeline.run(mod, ctx)

                    if not mod.verify():
                        self.error.emit(f"Verification failed: {mod.get_verification_error()}")
                        return

                    result = mod.to_string()
            elapsed = time.perf_counter() - start
            self.finished.emit(result, elapsed)
        except Exception as e:
            self.error.emit(str(e))


class MainWindow(QMainWindow):
    """Main window for the Shifting Codes obfuscation workbench."""

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Shifting Codes - LLVM Obfuscation Workbench")
        self.resize(1200, 800)

        self._worker: PassWorker | None = None
        self._original_ir = ""
        self._is_dark = True

        self._setup_ui()
        self._connect_signals()
        self._load_xtea_demo()

    def _setup_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)
        main_layout.setContentsMargins(4, 4, 4, 4)

        # Vertical splitter between input area and output area
        v_splitter = QSplitter(Qt.Orientation.Vertical)

        # Top area: input + pass selector
        top_splitter = QSplitter()

        # Left: IR editor
        left_panel = QWidget()
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.addWidget(QLabel("Input IR"))
        self._ir_editor = IREditor()
        left_layout.addWidget(self._ir_editor)

        btn_layout = QHBoxLayout()
        self._load_ll_btn = QPushButton("Load .ll")
        self._load_bc_btn = QPushButton("Load .bc")
        self._paste_btn = QPushButton("Paste")
        btn_layout.addWidget(self._load_ll_btn)
        btn_layout.addWidget(self._load_bc_btn)
        btn_layout.addWidget(self._paste_btn)
        btn_layout.addStretch()
        left_layout.addLayout(btn_layout)

        top_splitter.addWidget(left_panel)

        # Right: pass selector
        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.addWidget(QLabel("Pass Pipeline"))
        self._pass_selector = PassSelector()
        right_layout.addWidget(self._pass_selector)

        top_splitter.addWidget(right_panel)
        top_splitter.setSizes([700, 300])

        v_splitter.addWidget(top_splitter)

        # Bottom: diff view
        self._diff_view = DiffView()
        v_splitter.addWidget(self._diff_view)
        v_splitter.setSizes([400, 400])

        main_layout.addWidget(v_splitter)

        # Status bar
        self._status_bar = QStatusBar()
        self.setStatusBar(self._status_bar)
        self._theme_btn = QPushButton("Light Theme")
        self._theme_btn.setFixedHeight(22)
        self._status_bar.addPermanentWidget(self._theme_btn)
        self._status_bar.showMessage("Ready")

    def _connect_signals(self):
        self._load_ll_btn.clicked.connect(self._load_ll_file)
        self._load_bc_btn.clicked.connect(self._load_bc_file)
        self._paste_btn.clicked.connect(self._paste_from_clipboard)
        self._pass_selector.apply_requested.connect(self._apply_passes)
        self._theme_btn.clicked.connect(self._toggle_theme)

    def _toggle_theme(self):
        app = QApplication.instance()
        if self._is_dark:
            app.setStyleSheet(LIGHT_STYLESHEET)
            self._ir_editor.set_syntax_colors(LIGHT_SYNTAX)
            self._diff_view.set_theme(LIGHT_SYNTAX, LIGHT_DIFF)
            self._theme_btn.setText("Dark Theme")
        else:
            app.setStyleSheet(DARK_STYLESHEET)
            self._ir_editor.set_syntax_colors(DARK_SYNTAX)
            self._diff_view.set_theme(DARK_SYNTAX, DARK_DIFF)
            self._theme_btn.setText("Light Theme")
        self._is_dark = not self._is_dark

    def _load_ll_file(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Open LLVM IR File", "", "LLVM IR Files (*.ll);;All Files (*)"
        )
        if path:
            with open(path, "r") as f:
                text = f.read()
            self._ir_editor.setPlainText(text)
            self._status_bar.showMessage(f"Loaded: {path}")

    def _load_bc_file(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Open LLVM Bitcode File", "", "Bitcode Files (*.bc);;All Files (*)"
        )
        if path:
            try:
                with llvm.create_context() as ctx:
                    with ctx.parse_bitcode(path) as mod:
                        text = mod.to_string()
                self._ir_editor.setPlainText(text)
                self._status_bar.showMessage(f"Loaded: {path}")
            except Exception as e:
                self._status_bar.showMessage(f"Error loading bitcode: {e}")

    def _paste_from_clipboard(self):
        clipboard = QApplication.clipboard()
        if clipboard:
            self._ir_editor.setPlainText(clipboard.text())
            self._status_bar.showMessage("Pasted from clipboard")

    def _apply_passes(self, pass_list: list[tuple[str, type]]):
        if not pass_list:
            self._status_bar.showMessage("No passes selected")
            return

        ir_text = self._ir_editor.toPlainText().strip()
        if not ir_text:
            self._status_bar.showMessage("No IR input")
            return

        self._original_ir = ir_text
        self._status_bar.showMessage("Running passes...")
        self._pass_selector.setEnabled(False)

        self._worker = PassWorker(ir_text, pass_list, self)
        self._worker.finished.connect(self._on_passes_done)
        self._worker.error.connect(self._on_passes_error)
        self._worker.start()

    def _on_passes_done(self, result_ir: str, elapsed: float):
        self._diff_view.set_both(self._original_ir, result_ir)
        self._status_bar.showMessage(f"Done in {elapsed:.3f}s — module verified OK")
        self._pass_selector.setEnabled(True)
        self._worker = None

    def _on_passes_error(self, error_msg: str):
        self._status_bar.showMessage(f"Error: {error_msg}")
        self._pass_selector.setEnabled(True)
        self._worker = None

    def _load_xtea_demo(self):
        """Pre-populate the editor with XTEA IR as a demo."""
        try:
            from shifting_codes.xtea.builder import build_xtea_encrypt

            with llvm.create_context() as ctx:
                with ctx.create_module("xtea") as mod:
                    build_xtea_encrypt(ctx, mod)
                    ir_text = mod.to_string()
            self._ir_editor.setPlainText(ir_text)
            self._status_bar.showMessage("Loaded XTEA demo IR")
        except Exception as e:
            self._status_bar.showMessage(f"Could not load XTEA demo: {e}")

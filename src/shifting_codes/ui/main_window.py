"""Main application window."""

from __future__ import annotations

import os
import tempfile
import time

from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtWidgets import (
    QApplication,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QSplitter,
    QStatusBar,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

import llvm

from shifting_codes.passes import PassPipeline
from shifting_codes.passes.base import FunctionPass, ModulePass

# Import all passes so they register with PassRegistry (order defines UI listing)
import shifting_codes.passes.global_encryption  # noqa: F401
import shifting_codes.passes.bogus_control_flow  # noqa: F401
import shifting_codes.passes.indirect_call  # noqa: F401
import shifting_codes.passes.mba_obfuscation  # noqa: F401
import shifting_codes.passes.flattening  # noqa: F401
import shifting_codes.passes.substitution  # noqa: F401
import shifting_codes.passes.indirect_branch  # noqa: F401
import shifting_codes.passes.merge_function  # noqa: F401
import shifting_codes.passes.alias_access  # noqa: F401
import shifting_codes.passes.custom_cc  # noqa: F401
from shifting_codes.ui.compiler import (
    ExportWorker, check_clang, discover_functions,
)
from shifting_codes.ui.diff_view import DiffView
from shifting_codes.ui.export_dialog import ExportDialog
from shifting_codes.ui.function_selector import FunctionSelector
from shifting_codes.ui.ir_editor import IREditor
from shifting_codes.ui.pass_selector import PassSelector
from shifting_codes.ui.source_editor import SourceEditor
from shifting_codes.ui.source_parser import compile_c_to_ir, parse_annotations
from shifting_codes.ui.theme import (
    DARK_C_SYNTAX, DARK_DIFF, DARK_RUN, DARK_STYLESHEET, DARK_SYNTAX,
    LIGHT_C_SYNTAX, LIGHT_DIFF, LIGHT_RUN, LIGHT_STYLESHEET, LIGHT_SYNTAX,
)
from shifting_codes.utils.crypto import CryptoRandom


# ---------------------------------------------------------------------------
# Demo C source — serial number checker
# ---------------------------------------------------------------------------

_SERIAL_DEMO_SOURCE = """\
// Serial Number Checker -- Obfuscation Demo
//
// Functions marked with @obfuscate are pre-selected for obfuscation.
// Try applying Substitution + MBA + Bogus Control Flow, then Compile & Run
// or Export to see the result.
//
// Valid serial numbers for testing:
//   SHFT-0500-CODE-XRAY   (Basic tier)
//   DEMO-2500-LLVM-PASS   (Pro tier)
//   PROD-7000-OBFS-KEYS   (Enterprise tier)

extern int printf(const char *, ...);

// @obfuscate
static int check_serial(const char *serial) {
    // Verify length (expect 19 chars: XXXX-NNNN-XXXX-XXXX)
    int len = 0;
    while (serial[len] != '\\0') len++;
    if (len != 19) return 0;

    // Check dashes at positions 4, 9, 14
    if (serial[4] != '-' || serial[9] != '-' || serial[14] != '-')
        return 0;

    // Compute a weighted checksum over all characters
    unsigned int sum = 0;
    for (int i = 0; i < 19; i++) {
        sum = sum * 31 + (unsigned char)serial[i];
    }

    // Accept if checksum matches any known product key
    return sum == 0x3EE56CB4u   // SHFT-0500-CODE-XRAY
        || sum == 0x3952CB47u   // DEMO-2500-LLVM-PASS
        || sum == 0xF36594C3u;  // PROD-7000-OBFS-KEYS
}

// @obfuscate
static int derive_license_tier(const char *serial) {
    // Extract the numeric segment (positions 5-8)
    int tier = 0;
    for (int i = 5; i < 9; i++) {
        char c = serial[i];
        if (c < '0' || c > '9') return -1;
        tier = tier * 10 + (c - '0');
    }
    if (tier < 1000) return 0;       // Basic
    if (tier < 5000) return 1;       // Pro
    return 2;                         // Enterprise
}

static const char *tier_name(int tier) {
    if (tier == 0) return "Basic";
    if (tier == 1) return "Pro";
    if (tier == 2) return "Enterprise";
    return "Unknown";
}

int main(int argc, char **argv) {
    if (argc != 2) {
        printf("Usage: serial_check <ABCD-1234-EFGH-5678>\\n");
        return 1;
    }

    const char *serial = argv[1];

    if (!check_serial(serial)) {
        printf("Invalid serial number.\\n");
        return 1;
    }

    int tier = derive_license_tier(serial);
    printf("Serial accepted -- license tier: %s\\n", tier_name(tier));
    return 0;
}
"""


class PassWorker(QThread):
    """Worker thread that runs obfuscation passes off the UI thread."""

    finished = pyqtSignal(str, float)  # result_ir, elapsed_seconds
    error = pyqtSignal(str)

    def __init__(
        self,
        ir_text: str,
        pass_classes: list[tuple[str, type]],
        selected_functions: set[str] | None = None,
        parent=None,
    ):
        super().__init__(parent)
        self.ir_text = ir_text
        self.pass_classes = pass_classes
        self.selected_functions = selected_functions

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

                    pipeline.run(mod, ctx, selected_functions=self.selected_functions)

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
        self._export_worker: ExportWorker | None = None
        self._original_ir = ""
        self._obfuscated_ir: str | None = None
        self._is_dark = True
        self._source_text: str | None = None
        self._annotated_names: set[str] | None = None

        self._setup_ui()
        self._connect_signals()
        self._load_demo()

    def _setup_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)
        main_layout.setContentsMargins(10, 10, 10, 6)

        # Vertical splitter between input area and output area
        v_splitter = QSplitter(Qt.Orientation.Vertical)

        # Top area: input + right panel
        top_splitter = QSplitter()

        # Left: tabbed source + IR editor
        left_panel = QWidget()
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(0, 0, 6, 0)

        self._input_tabs = QTabWidget()

        # C/C++ Source tab
        self._source_editor = SourceEditor()
        self._input_tabs.addTab(self._source_editor, "C/C++ Source")

        # LLVM IR tab
        self._ir_editor = IREditor()
        self._input_tabs.addTab(self._ir_editor, "LLVM IR")

        left_layout.addWidget(self._input_tabs)

        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(6)
        btn_layout.setContentsMargins(0, 0, 0, 6)
        self._load_c_btn = QPushButton("Load C/C++")
        self._load_ll_btn = QPushButton("Load .ll")
        self._load_bc_btn = QPushButton("Load .bc")
        self._paste_btn = QPushButton("Paste")
        self._save_ll_btn = QPushButton("Save .ll")
        btn_layout.addWidget(self._load_c_btn)
        btn_layout.addWidget(self._load_ll_btn)
        btn_layout.addWidget(self._load_bc_btn)
        btn_layout.addWidget(self._paste_btn)
        btn_layout.addWidget(self._save_ll_btn)
        btn_layout.addStretch()
        left_layout.addLayout(btn_layout)

        top_splitter.addWidget(left_panel)

        # Right: function selector + pass selector (split) + action buttons
        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(6, 0, 0, 0)

        # Vertical splitter between function selector and pass selector
        right_splitter = QSplitter(Qt.Orientation.Horizontal)

        # Function selector (hidden until IR loaded)
        self._function_selector = FunctionSelector()
        self._function_selector.setVisible(False)
        right_splitter.addWidget(self._function_selector)

        # Pass selector
        pass_panel = QWidget()
        pass_layout = QVBoxLayout(pass_panel)
        pass_layout.setContentsMargins(6, 0, 0, 0)
        pass_layout.addWidget(QLabel("Pass Pipeline"))
        self._pass_selector = PassSelector()
        pass_layout.addWidget(self._pass_selector)
        right_splitter.addWidget(pass_panel)

        right_splitter.setSizes([200, 300])
        right_layout.addWidget(right_splitter)

        # Action buttons
        action_layout = QHBoxLayout()
        action_layout.setSpacing(6)
        action_layout.setContentsMargins(0, 6, 0, 6)
        self._build_btn = QPushButton("Build")
        self._build_btn.setEnabled(False)
        action_layout.addWidget(self._build_btn)
        action_layout.addStretch()
        right_layout.addLayout(action_layout)

        top_splitter.addWidget(right_panel)
        top_splitter.setSizes([700, 300])

        v_splitter.addWidget(top_splitter)

        # Bottom: diff view (wrapped with top margin for spacing from splitter)
        self._diff_view = DiffView()
        bottom_panel = QWidget()
        bottom_layout = QVBoxLayout(bottom_panel)
        bottom_layout.setContentsMargins(0, 6, 0, 0)
        bottom_layout.setSpacing(0)
        bottom_layout.addWidget(self._diff_view)
        v_splitter.addWidget(bottom_panel)
        v_splitter.setSizes([400, 400])

        main_layout.addWidget(v_splitter)

        # Status bar
        self._status_bar = QStatusBar()
        self._status_bar.setSizeGripEnabled(False)
        self.setStatusBar(self._status_bar)
        self._theme_btn = QPushButton("Light Theme")
        self._theme_btn.setFixedHeight(22)
        self._status_bar.addPermanentWidget(self._theme_btn)
        self._status_bar.showMessage("Ready")

    def _connect_signals(self):
        self._load_c_btn.clicked.connect(self._load_c_file)
        self._load_ll_btn.clicked.connect(self._load_ll_file)
        self._load_bc_btn.clicked.connect(self._load_bc_file)
        self._paste_btn.clicked.connect(self._paste_from_clipboard)
        self._save_ll_btn.clicked.connect(self._save_ll_file)
        self._pass_selector.apply_requested.connect(self._apply_passes)
        self._build_btn.clicked.connect(self._build)
        self._theme_btn.clicked.connect(self._toggle_theme)

    def _toggle_theme(self):
        app = QApplication.instance()
        if self._is_dark:
            app.setStyleSheet(LIGHT_STYLESHEET)
            self._ir_editor.set_syntax_colors(LIGHT_SYNTAX)
            self._source_editor.set_syntax_colors(LIGHT_C_SYNTAX)
            self._diff_view.set_theme(LIGHT_SYNTAX, LIGHT_DIFF, LIGHT_RUN)
            self._theme_btn.setText("Dark Theme")
        else:
            app.setStyleSheet(DARK_STYLESHEET)
            self._ir_editor.set_syntax_colors(DARK_SYNTAX)
            self._source_editor.set_syntax_colors(DARK_C_SYNTAX)
            self._diff_view.set_theme(DARK_SYNTAX, DARK_DIFF, DARK_RUN)
            self._theme_btn.setText("Light Theme")
        self._is_dark = not self._is_dark

    # ----- Populate function selector -----

    def _populate_function_selector(self, ir_text: str, annotated_names: set[str] | None = None):
        """Populate the function selector from IR text.

        Args:
            ir_text: The LLVM IR text.
            annotated_names: Names from C source with @obfuscate annotation.
                If None, all functions are pre-checked (IR-only mode).
        """
        funcs = discover_functions(ir_text)
        names = [f.name for f in funcs]
        if names:
            self._function_selector.set_functions(names, annotated_names)
            self._function_selector.setVisible(True)
        else:
            self._function_selector.setVisible(False)

    # ----- Load C source into both tabs -----

    def _load_c_source(self, source_text: str, ir_text: str, annotated_names: set[str] | None):
        """Populate both tabs: source view and compiled IR."""
        self._source_text = source_text
        self._annotated_names = annotated_names
        self._source_editor.setPlainText(source_text)
        self._ir_editor.setPlainText(ir_text)
        self._populate_function_selector(ir_text, annotated_names)
        self._input_tabs.setCurrentWidget(self._source_editor)

    def _load_ir_only(self, ir_text: str):
        """Load IR without C source — switch to IR tab."""
        self._source_text = None
        self._annotated_names = None
        self._source_editor.clear()
        self._ir_editor.setPlainText(ir_text)
        self._populate_function_selector(ir_text)
        self._input_tabs.setCurrentWidget(self._ir_editor)

    # ----- Load files -----

    def _load_c_file(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Open C/C++ File", "",
            "C/C++ Files (*.c *.cpp *.cc *.cxx *.c++);;All Files (*)",
        )
        if not path:
            return

        # Check clang availability
        available, info = check_clang()
        if not available:
            self._status_bar.showMessage(f"clang required: {info}")
            return

        # Read source for annotation parsing
        with open(path, "r") as f:
            source_text = f.read()

        # Parse annotations
        annotations = parse_annotations(source_text)
        annotated_names = {a.name for a in annotations if a.annotated} or None

        # Compile to IR
        self._status_bar.showMessage(f"Compiling {path}...")
        success, ir_or_error, warnings = compile_c_to_ir(path)
        if not success:
            self._source_editor.setPlainText(source_text)
            self._input_tabs.setCurrentWidget(self._source_editor)
            self._status_bar.showMessage(f"Compile error: {ir_or_error}")
            return

        self._load_c_source(source_text, ir_or_error, annotated_names)
        msg = f"Loaded: {path}"
        if warnings:
            msg += f" (warnings: {warnings[:100]})"
        self._status_bar.showMessage(msg)

    def _load_ll_file(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Open LLVM IR File", "", "LLVM IR Files (*.ll);;All Files (*)"
        )
        if path:
            with open(path, "r") as f:
                text = f.read()
            self._load_ir_only(text)
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
                self._load_ir_only(text)
                self._status_bar.showMessage(f"Loaded: {path}")
            except Exception as e:
                self._status_bar.showMessage(f"Error loading bitcode: {e}")

    def _paste_from_clipboard(self):
        clipboard = QApplication.clipboard()
        if clipboard:
            text = clipboard.text()
            self._load_ir_only(text)
            self._status_bar.showMessage("Pasted from clipboard")

    # ----- Apply passes -----

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

        # Get selected functions from function selector
        selected_functions: set[str] | None = None
        if self._function_selector.isVisible():
            selected_functions = self._function_selector.get_selected_names()

        self._worker = PassWorker(ir_text, pass_list, selected_functions, self)
        self._worker.finished.connect(self._on_passes_done)
        self._worker.error.connect(self._on_passes_error)
        self._worker.start()

    def _on_passes_done(self, result_ir: str, elapsed: float):
        self._obfuscated_ir = result_ir
        self._diff_view.set_both(self._original_ir, result_ir)
        self._status_bar.showMessage(f"Done in {elapsed:.3f}s — module verified OK")
        self._pass_selector.setEnabled(True)
        self._build_btn.setEnabled(True)
        self._worker = None

    def _on_passes_error(self, error_msg: str):
        self._status_bar.showMessage(f"Error: {error_msg}")
        self._pass_selector.setEnabled(True)
        self._worker = None

    # ----- Save -----

    def _save_ll_file(self):
        # Prefer saving obfuscated IR if available, else the input
        ir_text = self._obfuscated_ir or self._ir_editor.toPlainText().strip()
        if not ir_text:
            self._status_bar.showMessage("No IR to save")
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Save LLVM IR File", "", "LLVM IR Files (*.ll);;All Files (*)"
        )
        if path:
            with open(path, "w") as f:
                f.write(ir_text)
            self._status_bar.showMessage(f"Saved: {path}")

    # ----- Build -----

    def _build(self):
        ir_text = self._obfuscated_ir or self._ir_editor.toPlainText().strip()
        if not ir_text:
            self._status_bar.showMessage("No IR to build")
            return

        dialog = ExportDialog(parent=self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        result = dialog.get_result()
        if result is None:
            self._status_bar.showMessage("No output path specified")

            return

        export_type, output_path = result

        # Prepare UI
        self._diff_view.run_output.clear()
        self._diff_view.show_run_tab()
        self._build_btn.setEnabled(False)
        self._status_bar.showMessage("Building...")

        # Launch worker
        self._export_worker = ExportWorker(
            ir_text=ir_text,
            output_path=output_path,
            export_type=export_type,
            parent=self,
        )
        self._export_worker.log.connect(self._diff_view.run_output.append_log)
        self._export_worker.finished.connect(self._on_build_finished)
        self._export_worker.error.connect(self._on_build_error)
        self._export_worker.start()

    def _on_build_finished(self, result):
        if result.success:
            self._diff_view.run_output.append_log(f"Build successful: {result.output_path}")
            self._status_bar.showMessage(f"Built: {result.output_path}")
        else:
            self._diff_view.run_output.show_error(f"Build failed: {result.error}")
            self._status_bar.showMessage("Build failed")
        self._build_btn.setEnabled(True)
        self._export_worker = None

    def _on_build_error(self, error_msg: str):
        self._diff_view.run_output.show_error(error_msg)
        self._build_btn.setEnabled(True)
        self._status_bar.showMessage(f"Build error: {error_msg}")
        self._export_worker = None

    # ----- Demo -----

    def _load_demo(self):
        """Load the default demo — serial checker C source if clang is available,
        otherwise fall back to XTEA IR."""
        available, _ = check_clang()
        if available:
            self._load_serial_demo()
        else:
            self._load_xtea_demo()

    def _load_serial_demo(self):
        """Compile the embedded serial-checker C source and load it."""
        source = _SERIAL_DEMO_SOURCE
        annotations = parse_annotations(source)
        annotated_names = {a.name for a in annotations if a.annotated} or None

        # Write to a temp file so clang can compile it
        tmp_fd, tmp_path = tempfile.mkstemp(suffix=".c", prefix="serial_demo_")
        try:
            os.write(tmp_fd, source.encode())
            os.close(tmp_fd)

            success, ir_or_error, warnings = compile_c_to_ir(tmp_path)
            if not success:
                # Show source anyway, report error
                self._source_editor.setPlainText(source)
                self._input_tabs.setCurrentWidget(self._source_editor)
                self._status_bar.showMessage(f"Demo compile error: {ir_or_error}")
                return
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

        self._load_c_source(source, ir_or_error, annotated_names)
        self._status_bar.showMessage(
            "Loaded serial checker demo — check_serial and derive_license_tier are pre-selected"
        )

    def _load_xtea_demo(self):
        """Fallback: pre-populate the editor with XTEA IR."""
        try:
            from shifting_codes.xtea.builder import build_xtea_encrypt

            with llvm.create_context() as ctx:
                with ctx.create_module("xtea") as mod:
                    build_xtea_encrypt(ctx, mod)
                    ir_text = mod.to_string()
            self._load_ir_only(ir_text)
            self._status_bar.showMessage("Loaded XTEA demo IR (clang not found for C demo)")
        except Exception as e:
            self._status_bar.showMessage(f"Could not load demo: {e}")

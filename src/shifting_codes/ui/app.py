"""QApplication entry point for the Shifting Codes UI."""

import os
import sys

# Ensure llvm-nanobind is importable
_llvm_build = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "..", "..", "..", "llvm-nanobind", "build")
)
if os.path.isdir(_llvm_build) and _llvm_build not in sys.path:
    sys.path.insert(0, _llvm_build)

from PyQt6.QtWidgets import QApplication

from shifting_codes.ui.main_window import MainWindow
from shifting_codes.ui.theme import DARK_STYLESHEET


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("Shifting Codes")
    app.setOrganizationName("ShiftingCodes")
    app.setStyleSheet(DARK_STYLESHEET)

    window = MainWindow()
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()

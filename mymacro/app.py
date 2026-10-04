"""Application entry point.

`platform_utils.bootstrap()` has to run before Qt is imported, so this module
is only imported after the bootstrap in `run.py`.
"""

from __future__ import annotations

import sys

from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication

from .ui.main_window import MainWindow


def main() -> int:
    app = QApplication(sys.argv)
    # Qt scaling is off so coordinates stay physical; nudge the font up so the
    # UI is still readable on a high-DPI display.
    app.setFont(QFont(app.font().family(), 10))
    app.setApplicationName("MyMacro")

    window = MainWindow()
    window.show()
    return app.exec()

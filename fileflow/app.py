from __future__ import annotations

import os
import sys
from pathlib import Path


def _prepare_frozen_qt_path() -> None:
    bundle_root = getattr(sys, "_MEIPASS", None)
    if not bundle_root or not hasattr(os, "add_dll_directory"):
        return
    root_text = str(Path(bundle_root))
    qt_bin = Path(bundle_root) / "PyQt6" / "Qt6" / "bin"
    os.environ["PATH"] = root_text + os.pathsep + os.environ.get("PATH", "")
    os.add_dll_directory(root_text)
    if qt_bin.exists():
        qt_bin_text = str(qt_bin)
        os.environ["PATH"] = qt_bin_text + os.pathsep + os.environ.get("PATH", "")
        os.add_dll_directory(qt_bin_text)


_prepare_frozen_qt_path()

from PyQt6.QtWidgets import QApplication

from .app_metadata import APP_NAME
from .storage import Database
from .ui.main_window import MainWindow


def default_database_path() -> Path:
    root = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / "FileFlow"
    root.mkdir(parents=True, exist_ok=True)
    return root / "fileflow.db"


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    database = Database(default_database_path())
    database.migrate()
    window = MainWindow(database=database)
    window.show()
    try:
        return app.exec()
    finally:
        database.close()


if __name__ == "__main__":
    raise SystemExit(main())

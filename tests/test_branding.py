import os
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]


class BrandingTests(unittest.TestCase):
    def test_exe_icon_is_committed_and_used_by_the_build(self):
        icon = ROOT / "icon.ico"
        self.assertTrue(icon.is_file())
        self.assertEqual(b"\x00\x00\x01\x00", icon.read_bytes()[:4])
        self.assertIn('"--icon"', (ROOT / "build.py").read_text(encoding="utf-8"))

    def test_window_icon_and_mark_render_at_every_size(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        try:
            from PyQt6.QtCore import QSize
            from PyQt6.QtWidgets import QApplication
            from fileflow.ui.branding import ICON_SIZES, app_icon, mark_pixmap
        except Exception as exc:
            self.skipTest(f"PyQt6 UI unavailable: {exc}")

        app = QApplication.instance() or QApplication([])
        icon = app_icon()
        self.assertFalse(icon.isNull())
        self.assertEqual(sorted(ICON_SIZES), sorted(size.width() for size in icon.availableSizes()))
        glyph = mark_pixmap(40, tile=False, device_pixel_ratio=2.0)
        self.assertEqual(QSize(80, 80), glyph.size())
        self.assertIsNotNone(app)

    def test_sidebar_shows_version_pill_and_vaultsoft_byline(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        try:
            from PyQt6.QtWidgets import QApplication, QLabel
            from fileflow.app_metadata import APP_VERSION
            from fileflow.ui.main_window import MainWindow
        except Exception as exc:
            self.skipTest(f"PyQt6 UI unavailable: {exc}")

        app = QApplication.instance() or QApplication([])
        window = MainWindow()
        pills = [label.text() for label in window.findChildren(QLabel) if label.objectName() == "versionPill"]
        self.assertIn(f"v{APP_VERSION}", pills)
        self.assertFalse(window.windowIcon().isNull())
        text = " ".join(label.text() for label in window.findChildren(QLabel))
        self.assertIn("by VaultSoft", text)
        window.close()
        self.assertIsNotNone(app)


    def test_undo_result_banner_survives_the_history_refresh(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        try:
            from PyQt6.QtWidgets import QApplication
            from fileflow.ui.main_window import MainWindow
            from fileflow.undo import UndoControllerState, UndoResult
        except Exception as exc:
            self.skipTest(f"PyQt6 UI unavailable: {exc}")

        app = QApplication.instance() or QApplication([])
        window = MainWindow()
        history_row = {
            "id": "batch-1",
            "started_at": None,
            "approved_at": None,
            "status": "SUCCEEDED",
            "attempted_count": 1,
            "succeeded_count": 1,
            "failed_count": 0,
            "recovery_count": 0,
            "summary_json": '{"source_root": "C:\\\\FileFlowTest\\\\Root"}',
        }
        result = UndoResult(next(iter(UndoControllerState)), "undo-1", "Restored 1 of 1 files.", attempted=1, succeeded=1)
        # Both steps re-select the history row, which resets the Undo banner.
        with patch.object(window.apply_controller, "history_rows", return_value=[history_row]):
            window._refresh_history()
            window.confirm_undo_button.setText("Undo 1 File")
            window._undo_finished(result)
            window._undo_worker_finished()

        self.assertEqual("Restored 1 of 1 files.", window.undo_summary_label.text())
        self.assertEqual("success", window.undo_summary_label.property("tone"))
        self.assertEqual("Undo Ready Files", window.confirm_undo_button.text())
        self.assertFalse(window.confirm_undo_button.isEnabled())
        window.close()
        self.assertIsNotNone(app)


if __name__ == "__main__":
    unittest.main()

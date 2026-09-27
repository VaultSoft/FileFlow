import os
import unittest
from pathlib import Path


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


if __name__ == "__main__":
    unittest.main()

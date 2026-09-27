"""Render icon.ico (the .exe icon) from the in-app FileFlow mark.

Run after changing fileflow/ui/branding.py; build.py embeds the result.
Needs Pillow (in requirements-build.txt) to pack the multi-size .ico.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

from fileflow.ui.branding import mark_pixmap

ROOT = Path(__file__).resolve().parent
ICON_PATH = ROOT / "icon.ico"
ICO_SIZES = [(16, 16), (20, 20), (24, 24), (32, 32), (40, 40), (48, 48), (64, 64), (128, 128), (256, 256)]


def main() -> int:
    from PIL import Image

    app = QApplication(sys.argv)
    frames = []
    with tempfile.TemporaryDirectory() as tmp:
        # Each size is drawn separately, so small frames get the heavier small-size stroke.
        for width, _ in ICO_SIZES:
            png = Path(tmp) / f"{width}.png"
            mark_pixmap(width).save(str(png), "PNG")
            with Image.open(png) as image:
                frames.append(image.convert("RGBA"))
    largest = frames[-1]
    largest.save(ICON_PATH, format="ICO", sizes=ICO_SIZES, append_images=frames[:-1])
    print(f"Wrote {ICON_PATH} ({ICON_PATH.stat().st_size} bytes)")
    del app
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

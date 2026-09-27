from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import PyQt6


ROOT = Path(__file__).resolve().parent
APP_NAME = "FileFlow"
VERSION = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
DIST_DIR = ROOT / "dist"
BUILD_DIR = ROOT / "build"
APP_DIST = DIST_DIR / APP_NAME
PORTABLE_ZIP = DIST_DIR / f"{APP_NAME}_v{VERSION}_Portable.zip"
ICON = ROOT / "icon.ico"  # rendered from fileflow/ui/branding.py by make_icon.py
LICENCE_FILES = ("LICENSE", "THIRD_PARTY_NOTICES.md")  # plus the LICENSES folder
QT_BIN = Path(PyQt6.__file__).resolve().parent / "Qt6" / "bin"
QT_ROOT_DLLS = ("Qt6Core.dll", "Qt6Gui.dll", "Qt6Widgets.dll", "Qt6Network.dll")


def main() -> int:
    for path in (BUILD_DIR, APP_DIST, PORTABLE_ZIP):
        if path.exists():
            if path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink()

    command = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--windowed",
        "--name",
        APP_NAME,
        "--contents-directory",
        ".",
        "--workpath",
        str(BUILD_DIR / "pyinstaller"),
        "--specpath",
        str(BUILD_DIR),
        "--distpath",
        str(DIST_DIR),
        "--icon",
        str(ICON),
        "--add-data",
        f"{ROOT / 'VERSION'};.",
    ]
    for dll_name in QT_ROOT_DLLS:
        dll_path = QT_BIN / dll_name
        if dll_path.exists():
            command.extend(("--add-binary", f"{dll_path};."))
    command.append(str(ROOT / "fileflow_launcher.py"))
    subprocess.run(command, cwd=ROOT, check=True)

    executable = APP_DIST / f"{APP_NAME}.exe"
    if not executable.exists():
        raise FileNotFoundError(f"Packaged executable was not created: {executable}")

    for stale_icu in APP_DIST.glob("icu*.dll"):
        stale_icu.unlink()

    # GPL-3.0 and Qt's LGPL-3.0 require the licence texts to travel with the binaries.
    for notice in LICENCE_FILES:
        shutil.copy2(ROOT / notice, APP_DIST / notice)
    shutil.copytree(ROOT / "LICENSES", APP_DIST / "LICENSES")

    shutil.make_archive(str(PORTABLE_ZIP.with_suffix("")), "zip", root_dir=DIST_DIR, base_dir=APP_NAME)
    if not PORTABLE_ZIP.exists():
        raise FileNotFoundError(f"Portable ZIP was not created: {PORTABLE_ZIP}")

    print(f"Built {executable}")
    print(f"Built {PORTABLE_ZIP}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

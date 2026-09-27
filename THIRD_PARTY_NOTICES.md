# Third-party notices

FileFlow is free software, released under the GNU General Public License
version 3 only (GPL-3.0-only). The full text is in [`LICENSE`](LICENSE).

The portable Windows build (`FileFlow_vX.Y.Z_Portable.zip`) also contains the
components below, each under its own licence. Their licence texts are in
[`LICENSES/`](LICENSES) and ship inside the ZIP.

| Component | Version | Licence | Where it is in the ZIP |
|---|---|---|---|
| PyQt6 (Riverbank Computing) | 6.11.0 | GPL-3.0-only ([`LICENSE`](LICENSE)) | `PyQt6/Qt*.pyd` |
| Qt 6 (The Qt Company and contributors), from the PyQt6-Qt6 wheel | 6.11.0 | LGPL-3.0 ([`LICENSES/LGPL-3.0.txt`](LICENSES/LGPL-3.0.txt), which builds on [`LICENSE`](LICENSE)) | `Qt6*.dll`, `PyQt6/Qt6/` |
| Mesa llvmpipe, Qt's software OpenGL fallback (from the PyQt6-Qt6 wheel) | 11.2.2 | MIT (Mesa); includes LLVM, Apache-2.0 WITH LLVM-exception | `PyQt6/Qt6/bin/opengl32sw.dll` |
| PyQt6-sip | 13.11.1 | BSD-2-Clause ([`LICENSES/PyQt6-sip-BSD-2-Clause.txt`](LICENSES/PyQt6-sip-BSD-2-Clause.txt)) | `PyQt6/sip.*.pyd` |
| CPython | 3.11.9 | PSF-2.0 ([`LICENSES/Python-3.11-LICENSE.txt`](LICENSES/Python-3.11-LICENSE.txt)) | `python311.dll`, `python3.dll`, `*.pyd` |
| Libraries in the CPython Windows build: OpenSSL 3.0.13, libffi, SQLite 3.45.1, bzip2, xz | as shipped with CPython 3.11.9 | Apache-2.0 (OpenSSL), MIT (libffi), public domain (SQLite), bzip2 and xz licences; all in [`LICENSES/Python-3.11-LICENSE.txt`](LICENSES/Python-3.11-LICENSE.txt), "Additional Conditions for this Windows binary build" | `libcrypto-3.dll`, `libffi-8.dll`, `sqlite3.dll`, `_bz2.pyd`, `_lzma.pyd` |
| PyInstaller bootloader | 6.20.0 | GPL-2.0-or-later with the PyInstaller bootloader exception | `FileFlow.exe` |
| Microsoft Visual C++ runtime and Universal CRT | as redistributed by Python and Qt | Microsoft redistributable runtime files | `VCRUNTIME140*.dll`, `MSVCP140*.dll`, `ucrtbase.dll`, `api-ms-win-*.dll` |

Build-only tools that are **not** in the ZIP: PyInstaller's Python package
(GPL-2.0-or-later with exception) and Pillow (HPND, used by `make_icon.py`).

## Why GPL-3.0

FileFlow's interface is built on PyQt6, which Riverbank Computing distributes
under GPL-3.0-only (or a commercial licence). A program distributed with the
GPL edition of PyQt6 must be under GPL-3.0-compatible terms, so FileFlow uses
GPL-3.0-only. Everything else in the table is permissive or LGPL, and all of it
is compatible with that.

## Source code

- FileFlow: <https://github.com/VaultSoft/FileFlow>. Each release is tagged,
  e.g. `v0.9.0`, and the tag is the exact source of that release's ZIP.
- PyQt6 and PyQt6-sip: <https://pypi.org/project/PyQt6/> and
  <https://pypi.org/project/PyQt6-sip/> (source distributions).
- Qt 6: <https://download.qt.io/official_releases/qt/>
- CPython: <https://www.python.org/downloads/source/>
- Mesa: <https://mesa3d.org/>

## Replacing the Qt libraries

Qt is used as separate, unmodified DLLs (`Qt6*.dll` beside `FileFlow.exe` and
under `PyQt6/Qt6/`), so you can replace them with your own compatible build
of Qt 6.11 as LGPL-3.0 allows. The FileFlow source and `build.py` in the
repository are everything needed to rebuild FileFlow itself.

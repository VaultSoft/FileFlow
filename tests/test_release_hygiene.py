import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SKIP_DIRS = {".git", "build", "dist", "release", "__pycache__", ".venv", "venv"}
TEXT_SUFFIXES = {".py", ".md", ".txt", ".spec", ".json", ".cfg", ".toml", ".ini", ""}
# Example paths must use a placeholder user, never a real profile name.
PROFILE_PATH = re.compile(r"[A-Za-z]:\\+Users\\+(?!Demo\b|YourName\b|Public\b)[A-Za-z0-9._-]+", re.IGNORECASE)


def _text_files():
    for path in ROOT.rglob("*"):
        if any(part in SKIP_DIRS for part in path.relative_to(ROOT).parts):
            continue
        if path.is_file() and path.suffix.lower() in TEXT_SUFFIXES and path.parent.name != "LICENSES":
            yield path


class ReleaseHygieneTests(unittest.TestCase):
    def test_gpl3_licence_and_third_party_texts_are_present(self):
        self.assertIn("GNU GENERAL PUBLIC LICENSE", (ROOT / "LICENSE").read_text(encoding="utf-8")[:200])
        self.assertIn("Version 3, 29 June 2007", (ROOT / "LICENSE").read_text(encoding="utf-8")[:200])
        for name in ("LGPL-3.0.txt", "PyQt6-sip-BSD-2-Clause.txt", "Python-3.11-LICENSE.txt"):
            self.assertTrue((ROOT / "LICENSES" / name).is_file(), name)
        notices = (ROOT / "THIRD_PARTY_NOTICES.md").read_text(encoding="utf-8")
        self.assertIn("GPL-3.0-only", notices)
        self.assertIn("LGPL-3.0", notices)

    def test_build_ships_the_licence_texts_in_the_zip(self):
        build = (ROOT / "build.py").read_text(encoding="utf-8")
        self.assertIn('LICENCE_FILES = ("LICENSE", "THIRD_PARTY_NOTICES.md")', build)
        self.assertIn('shutil.copytree(ROOT / "LICENSES", APP_DIST / "LICENSES")', build)

    def test_no_developer_profile_paths_in_the_repository(self):
        hits = []
        for path in _text_files():
            text = path.read_text(encoding="utf-8", errors="ignore")
            for match in PROFILE_PATH.finditer(text):
                hits.append(f"{path.relative_to(ROOT)}: {match.group(0)}")
        self.assertEqual([], hits)


if __name__ == "__main__":
    unittest.main()

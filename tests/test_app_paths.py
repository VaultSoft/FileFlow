import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fileflow.app import default_database_path


class ProductionDataPathTests(unittest.TestCase):
    def test_database_path_uses_localappdata_and_ignores_working_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture = Path(tmp)
            local_app_data = fixture / "LocalAppData"
            first_cwd = fixture / "FirstWorkingDirectory"
            second_cwd = fixture / "SecondWorkingDirectory"
            first_cwd.mkdir()
            second_cwd.mkdir()
            original_cwd = Path.cwd()

            try:
                with patch.dict(
                    os.environ,
                    {
                        "LOCALAPPDATA": str(local_app_data),
                        "APPDATA": str(fixture / "RoamingAppData"),
                        "USERPROFILE": str(fixture / "DifferentProfile"),
                    },
                ):
                    os.chdir(first_cwd)
                    first_path = default_database_path()
                    os.chdir(second_cwd)
                    second_path = default_database_path()
            finally:
                os.chdir(original_cwd)

            expected = local_app_data / "FileFlow" / "fileflow.db"
            self.assertEqual(expected, first_path)
            self.assertEqual(expected, second_path)
            self.assertTrue(expected.parent.is_dir())
            self.assertFalse(expected.exists())


if __name__ == "__main__":
    unittest.main()

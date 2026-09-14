import sys
import tempfile
import unittest
from pathlib import Path

from fileflow.safety import WindowsFileIdentityProvider


class WindowsIdentityProviderTests(unittest.TestCase):
    @unittest.skipUnless(sys.platform == "win32", "Windows identity integration requires Windows")
    def test_identity_is_stable_for_same_file_and_changes_after_replacement(self):
        provider = WindowsFileIdentityProvider()
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "identity.txt"
            target.write_text("first", encoding="utf-8")
            first = provider.snapshot(str(target))
            second = provider.snapshot(str(target))
            self.assertTrue(first.supported, first.error)
            self.assertTrue(second.supported, second.error)
            self.assertEqual(first.snapshot.identity, second.snapshot.identity)

            target.unlink()
            target.write_text("second", encoding="utf-8")
            replacement = provider.snapshot(str(target))
            self.assertTrue(replacement.supported, replacement.error)
            self.assertNotEqual(first.snapshot.identity, replacement.snapshot.identity)


if __name__ == "__main__":
    unittest.main()

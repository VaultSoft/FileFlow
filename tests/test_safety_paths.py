import unittest
from unittest.mock import patch

from fileflow.models import SafetyReason
from fileflow.safety import FakeReparseInspector, PathChainSafety, WindowsPathPolicy


def use_placeholder_profile(test: unittest.TestCase) -> None:
    # Fixtures live under C:\Users\Demo; paths in any profile but the current one are protected.
    profile = patch.dict("os.environ", {"USERPROFILE": r"C:\Users\Demo"})
    profile.start()
    test.addCleanup(profile.stop)


class WindowsPathPolicyTests(unittest.TestCase):
    def setUp(self):
        use_placeholder_profile(self)
        self.policy = WindowsPathPolicy()

    def test_accepts_absolute_local_child_beneath_root(self):
        decision = self.policy.classify(r"C:\Users\Demo\Downloads\Photo.JPG", r"C:\Users\Demo\Downloads")
        self.assertTrue(decision.allowed)
        self.assertEqual(r"C:\Users\Demo\Downloads\Photo.JPG", decision.normalized_path)

    def test_blocks_empty_and_relative_paths(self):
        self.assertEqual(SafetyReason.EMPTY_PATH, self.policy.classify("").reason)
        self.assertEqual(SafetyReason.RELATIVE_PATH, self.policy.classify(r"folder\file.txt").reason)

    def test_blocks_drive_relative_paths(self):
        decision = self.policy.classify(r"C:folder\file.txt")
        self.assertEqual(SafetyReason.DRIVE_RELATIVE, decision.reason)

    def test_blocks_filesystem_roots(self):
        decision = self.policy.classify(r"C:\\")
        self.assertEqual(SafetyReason.FILESYSTEM_ROOT, decision.reason)

    def test_blocks_unc_and_extended_paths(self):
        self.assertEqual(SafetyReason.UNC_PATH, self.policy.classify(r"\\server\share\file.txt").reason)
        self.assertEqual(SafetyReason.EXTENDED_PATH, self.policy.classify(r"\\?\C:\Users\Demo\file.txt").reason)

    def test_blocks_ads_trailing_dot_trailing_space_and_reserved_names(self):
        self.assertEqual(SafetyReason.ADS_SYNTAX, self.policy.classify(r"C:\Users\Demo\file.txt:secret").reason)
        self.assertEqual(SafetyReason.TRAILING_DOT, self.policy.classify(r"C:\Users\Demo\bad.").reason)
        self.assertEqual(SafetyReason.TRAILING_SPACE, self.policy.classify(r"C:\Users\Demo\bad ").reason)
        self.assertEqual(SafetyReason.RESERVED_DEVICE_NAME, self.policy.classify(r"C:\Users\Demo\NUL.txt").reason)

    def test_blocks_ambiguous_normalization_and_long_paths(self):
        decomposed = "Cafe\u0301.txt"
        self.assertEqual(SafetyReason.AMBIGUOUS_NORMALIZATION, self.policy.classify(r"C:\Users\Demo\\" + decomposed).reason)
        self.assertEqual(SafetyReason.AMBIGUOUS_NORMALIZATION, self.policy.classify(r"C:\Users\Demo\Downloads\.\file.txt").reason)
        self.assertEqual(SafetyReason.AMBIGUOUS_NORMALIZATION, self.policy.classify(r"C:\Users\Demo\Downloads\Sub\..\file.txt").reason)
        long_name = "a" * 260
        self.assertEqual(SafetyReason.UNSUPPORTED_LONG_PATH, self.policy.classify(r"C:\Users\Demo\\" + long_name).reason)

    def test_blocks_path_escape_under_approved_root(self):
        decision = self.policy.classify(r"C:\Users\Demo\Desktop\file.txt", r"C:\Users\Demo\Downloads")
        self.assertEqual(SafetyReason.PATH_ESCAPE, decision.reason)

    def test_case_insensitive_root_check_and_collision(self):
        decision = self.policy.classify(r"c:\users\demo\downloads\file.txt", r"C:\Users\Demo\Downloads")
        self.assertTrue(decision.allowed)
        self.assertTrue(self.policy.has_case_collision(r"C:\A\Photo.jpg", [r"c:\a\photo.JPG"]))

    def test_blocks_protected_roots(self):
        self.assertEqual(SafetyReason.PROTECTED_ROOT, self.policy.classify(r"C:\Windows\System32\cmd.exe").reason)
        self.assertEqual(SafetyReason.PROTECTED_ROOT, self.policy.classify(r"C:\Program Files\App\app.exe").reason)
        self.assertEqual(SafetyReason.PROTECTED_ROOT, self.policy.classify(r"C:\Users\Demo").reason)


class ReparseChainSafetyTests(unittest.TestCase):
    def setUp(self):
        use_placeholder_profile(self)
        self.policy = WindowsPathPolicy()

    def test_blocks_approved_root_when_root_is_reparse(self):
        chain = PathChainSafety(self.policy, FakeReparseInspector({r"C:\Users\Demo\Downloads"}))
        decision = chain.classify_chain(r"C:\Users\Demo\Downloads\file.txt", r"C:\Users\Demo\Downloads")
        self.assertEqual(SafetyReason.REPARSE_POINT, decision.reason)

    def test_blocks_nested_child_redirect(self):
        chain = PathChainSafety(self.policy, FakeReparseInspector({r"C:\Users\Demo\Downloads\Nested"}))
        decision = chain.classify_chain(r"C:\Users\Demo\Downloads\Nested\file.txt", r"C:\Users\Demo\Downloads")
        self.assertEqual(SafetyReason.REPARSE_POINT, decision.reason)

    def test_allows_normal_real_path_chain_with_fake_inspector(self):
        chain = PathChainSafety(self.policy, FakeReparseInspector())
        decision = chain.classify_chain(r"C:\Users\Demo\Downloads\file.txt", r"C:\Users\Demo\Downloads")
        self.assertTrue(decision.allowed)

    def test_fails_closed_when_reparse_inspection_fails(self):
        chain = PathChainSafety(self.policy, FakeReparseInspector(failing_paths={r"C:\Users\Demo\Downloads"}))
        decision = chain.classify_chain(r"C:\Users\Demo\Downloads\file.txt", r"C:\Users\Demo\Downloads")
        self.assertEqual(SafetyReason.REPARSE_INSPECTION_FAILED, decision.reason)

    def test_fails_closed_when_reparse_inspector_raises_at_root(self):
        class RaisingInspector:
            def inspect(self, path):
                raise RuntimeError("inspector unavailable")

        chain = PathChainSafety(self.policy, RaisingInspector())
        decision = chain.classify_chain(r"C:\Users\Demo\Downloads")
        self.assertEqual(SafetyReason.REPARSE_INSPECTION_FAILED, decision.reason)
        self.assertIn("inspector unavailable", decision.error.details["error"])


if __name__ == "__main__":
    unittest.main()

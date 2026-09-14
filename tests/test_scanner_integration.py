import tempfile
import unittest
from pathlib import Path

from fileflow.models import FileIdentity, IdentitySnapshot, MetadataSnapshot, SafetyReason, ScannedItemKind
from fileflow.scanner import ImmediateChildScanner
from fileflow.safety import ConservativeCloudClassifier, FakeIdentityProvider, FakeReparseInspector, PathChainSafety, WindowsPathPolicy


def snapshot(path, file_id=None, file_type="file"):
    return IdentitySnapshot(
        FileIdentity("VOL", file_id or path.casefold(), file_type, 1),
        MetadataSnapshot(path, 10, 1, 1),
    )


class ScannerIntegrationTests(unittest.TestCase):
    def test_scanner_only_classifies_immediate_children_without_recursion(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            child = root / "photo.jpg"
            child.write_text("x", encoding="utf-8")
            nested = root / "Nested"
            nested.mkdir()
            (nested / "hidden.jpg").write_text("x", encoding="utf-8")

            policy = WindowsPathPolicy()
            root_normalized = policy.normalize(str(root))
            child_normalized = policy.normalize(str(child))
            identities = {
                root_normalized: snapshot(root_normalized, "root", "directory"),
                child_normalized: snapshot(child_normalized, "photo"),
            }
            scanner = ImmediateChildScanner(
                policy,
                PathChainSafety(policy, FakeReparseInspector()),
                FakeIdentityProvider(identities),
                ConservativeCloudClassifier(),
            )

            items = scanner.scan(str(root))
            paths = {Path(item.path).name: item for item in items}
            self.assertIn("photo.jpg", paths)
            self.assertIn("Nested", paths)
            self.assertNotIn("hidden.jpg", paths)
            self.assertEqual(ScannedItemKind.DIRECTORY, paths["Nested"].kind)
            self.assertEqual(SafetyReason.DIRECTORY_SKIPPED, paths["Nested"].safety.reason)

    def test_missing_root_enumeration_becomes_structured_scan_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "missing"
            policy = WindowsPathPolicy()
            root_normalized = policy.normalize(str(missing))
            scanner = ImmediateChildScanner(
                policy,
                PathChainSafety(policy, FakeReparseInspector()),
                FakeIdentityProvider({root_normalized: snapshot(root_normalized, "root", "directory")}),
                ConservativeCloudClassifier(),
            )
            items = scanner.scan(str(missing))
            self.assertEqual(1, len(items))
            self.assertEqual(SafetyReason.SCAN_FAILED, items[0].safety.reason)
            self.assertIsNotNone(items[0].safety.error)

    def test_identity_provider_exception_becomes_structured_scan_failure(self):
        class RaisingIdentityProvider:
            def snapshot(self, logical_path):
                raise RuntimeError("identity backend failed")

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            child = root / "photo.jpg"
            child.write_text("x", encoding="utf-8")
            policy = WindowsPathPolicy()
            scanner = ImmediateChildScanner(
                policy,
                PathChainSafety(policy, FakeReparseInspector()),
                RaisingIdentityProvider(),
                ConservativeCloudClassifier(),
            )
            items = scanner.scan(str(root))
            self.assertEqual(1, len(items))
            self.assertEqual(SafetyReason.SCAN_FAILED, items[0].safety.reason)
            self.assertIn("identity backend failed", items[0].safety.error.details["error"])


if __name__ == "__main__":
    unittest.main()

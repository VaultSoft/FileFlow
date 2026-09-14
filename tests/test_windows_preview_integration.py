import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from fileflow.models import PlannedOperationStatus, RevalidationReason, RevalidationStatus, SafetyReason
from fileflow.preview_workflow import PreviewWorkflowService
from fileflow.ui.presentation import COLLISION, READY, present_analysis


def require_windows(test_func):
    return unittest.skipUnless(sys.platform == "win32", "real Windows filesystem integration")(test_func)


def create_junction(link: Path, target: Path) -> bool:
    result = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(link), str(target)],
        capture_output=True,
        text=True,
        check=False,
    )
    return result.returncode == 0


class WindowsPreviewIntegrationTests(unittest.TestCase):
    @require_windows
    def test_real_temp_folder_preview_and_summary(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "Images").mkdir()
            (root / "photo.jpg").write_text("image", encoding="utf-8")
            nested = root / "Nested"
            nested.mkdir()
            (nested / "hidden.jpg").write_text("hidden", encoding="utf-8")

            analysis = PreviewWorkflowService().analyse_folder(str(root))
            presentation = present_analysis(analysis)

            self.assertIsNotNone(analysis.plan)
            self.assertEqual(1, presentation.summary.total_files)
            self.assertEqual(2, presentation.summary.skipped_subdirectories)
            self.assertEqual(READY, presentation.rows[0].status)
            self.assertEqual(str(root / "Images" / "photo.jpg"), presentation.rows[0].destination)

    @require_windows
    def test_real_destination_collision_is_presented(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "Images").mkdir()
            (root / "photo.jpg").write_text("image", encoding="utf-8")
            (root / "Images" / "photo.jpg").write_text("existing", encoding="utf-8")

            analysis = PreviewWorkflowService().analyse_folder(str(root))
            presentation = present_analysis(analysis)

            self.assertEqual(COLLISION, presentation.rows[0].status)
            self.assertEqual("DESTINATION_EXISTS", presentation.rows[0].collision_status)

    @require_windows
    def test_real_source_replacement_makes_preview_stale(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "Images").mkdir()
            source = root / "photo.jpg"
            source.write_text("image", encoding="utf-8")
            service = PreviewWorkflowService()
            analysis = service.analyse_folder(str(root))
            self.assertIsNotNone(analysis.plan)

            source.unlink()
            source.write_text("replacement", encoding="utf-8")
            result = service.revalidate_plan(analysis.plan)

            self.assertEqual(RevalidationStatus.STALE, result.status)
            self.assertIn(RevalidationReason.SOURCE_IDENTITY_CHANGED, result.reasons)

    @require_windows
    def test_real_root_identity_change_makes_preview_stale(self):
        with tempfile.TemporaryDirectory() as tmp:
            parent = Path(tmp)
            root = parent / "Root"
            root.mkdir()
            (root / "Images").mkdir()
            (root / "photo.jpg").write_text("image", encoding="utf-8")
            service = PreviewWorkflowService()
            analysis = service.analyse_folder(str(root))
            self.assertIsNotNone(analysis.plan)

            old_root = parent / "OldRoot"
            root.rename(old_root)
            root.mkdir()
            (root / "Images").mkdir()
            (root / "photo.jpg").write_text("image", encoding="utf-8")
            result = service.revalidate_plan(analysis.plan)

            self.assertIn(result.status, (RevalidationStatus.STALE, RevalidationStatus.BLOCKED))
            self.assertIn(RevalidationReason.ROOT_IDENTITY_CHANGED, result.reasons)

    @require_windows
    def test_real_selected_root_junction_is_blocked(self):
        with tempfile.TemporaryDirectory() as tmp:
            parent = Path(tmp)
            target = parent / "Target"
            target.mkdir()
            link = parent / "SelectedRoot"
            if not create_junction(link, target):
                self.skipTest("directory junction could not be created")

            validation = PreviewWorkflowService().validate_folder(str(link))

            self.assertFalse(validation.allowed)
            self.assertEqual(SafetyReason.REPARSE_POINT, validation.decision.reason)

    @require_windows
    def test_real_destination_parent_junction_is_blocked(self):
        with tempfile.TemporaryDirectory() as tmp:
            parent = Path(tmp)
            root = parent / "Root"
            target = parent / "OutsideImages"
            root.mkdir()
            target.mkdir()
            (root / "photo.jpg").write_text("image", encoding="utf-8")
            if not create_junction(root / "Images", target):
                self.skipTest("directory junction could not be created")

            analysis = PreviewWorkflowService().analyse_folder(str(root))
            self.assertIsNotNone(analysis.plan)
            photo_operation = next(operation for operation in analysis.plan.operations if operation.source_path.endswith("photo.jpg"))

            self.assertEqual(PlannedOperationStatus.BLOCKED, photo_operation.safety_status)
            self.assertEqual(SafetyReason.REPARSE_POINT.value, photo_operation.structured_error.code.value)

    @require_windows
    def test_real_nested_junction_is_not_traversed(self):
        with tempfile.TemporaryDirectory() as tmp:
            parent = Path(tmp)
            root = parent / "Root"
            target = parent / "OutsideNested"
            root.mkdir()
            target.mkdir()
            (target / "hidden.jpg").write_text("hidden", encoding="utf-8")
            if not create_junction(root / "Nested", target):
                self.skipTest("directory junction could not be created")

            analysis = PreviewWorkflowService().analyse_folder(str(root))
            scanned_names = {Path(item.path).name: item for item in analysis.scanned_items}

            self.assertIn("Nested", scanned_names)
            self.assertEqual(SafetyReason.REPARSE_POINT, scanned_names["Nested"].safety.reason)
            self.assertNotIn("hidden.jpg", scanned_names)

    @require_windows
    def test_real_selected_root_symlink_is_blocked_when_available(self):
        with tempfile.TemporaryDirectory() as tmp:
            parent = Path(tmp)
            target = parent / "Target"
            target.mkdir()
            link = parent / "SelectedSymlink"
            try:
                link.symlink_to(target, target_is_directory=True)
            except OSError as exc:
                self.skipTest(f"directory symlink unavailable: {exc}")

            validation = PreviewWorkflowService().validate_folder(str(link))

            self.assertFalse(validation.allowed)
            self.assertEqual(SafetyReason.REPARSE_POINT, validation.decision.reason)


if __name__ == "__main__":
    unittest.main()

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fileflow.models import (
    ConflictStatus,
    ErrorCode,
    FileIdentity,
    IdentitySnapshot,
    MetadataSnapshot,
    OperationIntent,
    PlanStatus,
    PlannedOperation,
    PlannedOperationStatus,
    PreviewPlan,
    RevalidationReason,
    RevalidationResult,
    RevalidationStatus,
    SafetyDecision,
    SafetyReason,
    ScannedItem,
    ScannedItemKind,
    Severity,
    StructuredError,
)
from fileflow.preview_workflow import FolderValidation, PreviewAnalysis, PreviewWorkflowService
from fileflow.safety import FakeIdentityProvider, FakeReparseInspector, PathChainSafety, WindowsPathPolicy
from fileflow.ui.presentation import (
    BLOCKED,
    COLLISION,
    READY,
    UNSUPPORTED,
    present_analysis,
    present_revalidation,
    safety_decision_text,
)


def snapshot(path, file_id=None, file_type="file", size=100):
    return IdentitySnapshot(
        FileIdentity("VOL", file_id or path.casefold(), file_type, 1),
        MetadataSnapshot(path, size, 10, 5),
    )


def operation(index, status, *, conflict=ConflictStatus.NONE, error=None):
    return PlannedOperation(
        id=f"op-{index}",
        operation_type=OperationIntent.MOVE,
        source_path=rf"C:\FileFlowTest\Root\file{index}.jpg",
        destination_path=rf"C:\FileFlowTest\Root\Images\file{index}.jpg",
        source_root=r"C:\FileFlowTest\Root",
        rule_snapshot=None,
        category_snapshot=None,
        reason="matched test rule",
        safety_status=status,
        conflict_status=conflict,
        reversible=False,
        source_identity=snapshot(rf"C:\FileFlowTest\Root\file{index}.jpg", size=index),
        preview_index=index,
        structured_error=error,
    )


class PreviewUiTests(unittest.TestCase):
    def test_summary_counts_and_row_status_mapping(self):
        root = r"C:\FileFlowTest\Root"
        items = (
            ScannedItem(root + r"\file1.jpg", "file1.jpg", ScannedItemKind.FILE, SafetyDecision.safe(root + r"\file1.jpg"), snapshot("1", size=10)),
            ScannedItem(root + r"\file2.jpg", "file2.jpg", ScannedItemKind.FILE, SafetyDecision.safe(root + r"\file2.jpg"), snapshot("2", size=20)),
            ScannedItem(
                root + r"\Nested",
                "Nested",
                ScannedItemKind.DIRECTORY,
                SafetyDecision.unsupported(SafetyReason.DIRECTORY_SKIPPED, "Skipped"),
            ),
        )
        errors = StructuredError(ErrorCode.DESTINATION_EXISTS, Severity.OPERATION_BLOCKING, "Destination exists.")
        plan = PreviewPlan(
            id="plan",
            profile_id="default",
            source_root=root,
            source_root_normalized=root,
            source_root_identity=snapshot(root, "root", "directory"),
            destination_root=root,
            destination_root_identity=snapshot(root, "root", "directory"),
            status=PlanStatus.BLOCKED,
            rule_set_version=1,
            category_version=1,
            safety_policy_version=1,
            operations=(
                operation(1, PlannedOperationStatus.PLANNED),
                operation(2, PlannedOperationStatus.BLOCKED, conflict=ConflictStatus.DESTINATION_EXISTS, error=errors),
                operation(3, PlannedOperationStatus.UNSUPPORTED),
                operation(4, PlannedOperationStatus.BLOCKED),
            ),
            rule_snapshots=(),
            category_snapshots=(),
            created_at="now",
        )
        analysis = PreviewAnalysis(FolderValidation(root, SafetyDecision.safe(root), snapshot(root, "root", "directory")), items, plan)

        presentation = present_analysis(analysis)

        self.assertEqual((READY, COLLISION, UNSUPPORTED, BLOCKED), tuple(row.status for row in presentation.rows))
        self.assertEqual(2, presentation.summary.total_files)
        self.assertEqual(1, presentation.summary.ready)
        self.assertEqual(1, presentation.summary.collisions)
        self.assertEqual(1, presentation.summary.unsupported)
        self.assertEqual(1, presentation.summary.blocked)
        self.assertEqual(1, presentation.summary.skipped_subdirectories)
        self.assertEqual(30, presentation.summary.total_bytes)
        self.assertFalse(presentation.can_apply)

    def test_user_facing_safety_reason_text(self):
        decision = SafetyDecision.block(SafetyReason.REPARSE_POINT, "raw")
        self.assertIn("junction", safety_decision_text(decision))

    def test_stale_preview_presentation_requires_reanalysis(self):
        result = RevalidationResult(RevalidationStatus.STALE, (RevalidationReason.DESTINATION_APPEARED,), ())
        presentation = present_revalidation(result)
        self.assertEqual("STALE", presentation.status)
        self.assertIn("out of date", presentation.title)
        self.assertIn("Analyse again", presentation.message)
        self.assertIn("destination appeared", presentation.reasons[0])

    def test_folder_selection_blocks_reparse_root(self):
        policy = WindowsPathPolicy()
        root = r"C:\Users\Josh\Downloads\FileFlowRoot"
        service = PreviewWorkflowService(
            path_policy=policy,
            chain_safety=PathChainSafety(policy, FakeReparseInspector({root})),
            identity_provider=FakeIdentityProvider({root: snapshot(root, "root", "directory")}),
        )
        validation = service.validate_folder(root)
        self.assertFalse(validation.allowed)
        self.assertEqual(SafetyReason.REPARSE_POINT, validation.decision.reason)

    def test_main_window_apply_button_starts_disabled(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        try:
            from PyQt6.QtWidgets import QApplication
            from fileflow.ui.main_window import MainWindow
        except Exception as exc:
            self.skipTest(f"PyQt6 UI unavailable: {exc}")

        app = QApplication.instance() or QApplication([])
        window = MainWindow()
        self.assertFalse(window.apply_button.isEnabled())
        window.close()
        self.assertIsNotNone(app)

    def test_main_window_undo_preview_controls_are_present_and_safe_initially(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        try:
            from PyQt6.QtWidgets import QApplication
            from fileflow.ui.main_window import MainWindow
        except Exception as exc:
            self.skipTest(f"PyQt6 UI unavailable: {exc}")

        app = QApplication.instance() or QApplication([])
        window = MainWindow()
        self.assertEqual("Preview Undo", window.preview_undo_button.text())
        self.assertFalse(window.preview_undo_button.isEnabled())
        self.assertFalse(window.confirm_undo_button.isEnabled())
        self.assertEqual(5, window.undo_table.columnCount())
        window.close()
        self.assertIsNotNone(app)

    def test_rules_page_lists_builtin_categories_and_tester_is_non_mutating(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        try:
            from PyQt6.QtWidgets import QApplication
            from fileflow.ui.main_window import MainWindow
        except Exception as exc:
            self.skipTest(f"PyQt6 UI unavailable: {exc}")

        app = QApplication.instance() or QApplication([])
        window = MainWindow()
        self.assertEqual(len(window.service.categories), window.rules_table.rowCount())
        self.assertEqual("Active", window.rules_table.item(0, 0).text())
        window.rule_test_input.setText("report.PDF")
        window._test_rule_filename()
        self.assertIn("would match Documents", window.rule_test_result.text())
        self.assertFalse(window.apply_button.isEnabled())
        self.assertIsNone(window.current_analysis)
        window.close()
        self.assertIsNotNone(app)

    def test_settings_page_states_fixed_release_boundaries(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        try:
            from PyQt6.QtWidgets import QApplication, QLabel
            from fileflow.ui.main_window import MainWindow
        except Exception as exc:
            self.skipTest(f"PyQt6 UI unavailable: {exc}")

        app = QApplication.instance() or QApplication([])
        window = MainWindow()
        text = " ".join(label.text() for label in window.settings_page.findChildren(QLabel))
        self.assertIn("Same-volume moves only", text)
        self.assertIn("no overwrite or auto-rename", text)
        self.assertIn("must already exist", text)
        self.assertIn("Recovery", text)
        window.close()
        self.assertIsNotNone(app)

    @unittest.skipUnless(os.name == "nt", "real Windows filesystem integration")
    def test_validate_preview_uses_actionable_subset_and_lists_missing_folders(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        try:
            from PyQt6.QtWidgets import QApplication
            from fileflow.apply_controller import ApplyState
            from fileflow.ui.main_window import MainWindow
        except Exception as exc:
            self.skipTest(f"PyQt6 UI unavailable: {exc}")

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            documents = root / "Documents"
            documents.mkdir()
            (root / "ok.pdf").write_text("ready", encoding="utf-8")
            (root / "clash.pdf").write_text("source", encoding="utf-8")
            (root / "photo.jpg").write_text("image", encoding="utf-8")
            (documents / "clash.pdf").write_text("existing", encoding="utf-8")
            service = PreviewWorkflowService()
            analysis = service.analyse_folder(str(root))
            self.assertIsNotNone(analysis.plan)

            app = QApplication.instance() or QApplication([])
            window = MainWindow(service=service)
            window.selected_folder = str(root)
            window.current_analysis = analysis
            window.current_presentation = present_analysis(analysis)
            window.apply_state = ApplyState.PREVIEW_VALID
            window._render_presentation(window.current_presentation)

            self.assertFalse(window.missing_folders_banner.isHidden())
            self.assertIn("Images", window.missing_folders_banner.text())
            self.assertIn("Create these folders", window.missing_folders_banner.text())
            self.assertEqual(3, window.table.rowCount())

            window.validate_preview()

            self.assertIn("Preview is current", window.status_label.text())
            self.assertIn("1 ready, 2 blocked", window.status_label.text())
            self.assertTrue(window.apply_button.isEnabled())
            self.assertTrue(window.reanalyse_button.isEnabled())
            self.assertEqual(2, window.current_presentation.summary.blocked + window.current_presentation.summary.collisions)
            self.assertTrue((root / "ok.pdf").exists())
            self.assertTrue((root / "photo.jpg").exists())
            self.assertFalse((root / "Images").exists())
            window.close()
            self.assertIsNotNone(app)

    def test_apply_confirmation_defaults_and_escapes_to_cancel(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        try:
            from PyQt6.QtWidgets import QApplication, QMessageBox
            from fileflow.apply_controller import ApplyConfirmationSummary
            from fileflow.ui.main_window import MainWindow
        except Exception as exc:
            self.skipTest(f"PyQt6 UI unavailable: {exc}")

        class FakeMessageBox:
            ButtonRole = QMessageBox.ButtonRole

            def __init__(self, parent):
                self.buttons = []
                self.default = None
                self.escape = None
                self.clicked = None

            def setWindowTitle(self, title):
                self.title = title

            def setText(self, text):
                self.text = text

            def addButton(self, label, role):
                button = object()
                self.buttons.append((label, role, button))
                if role == QMessageBox.ButtonRole.RejectRole:
                    self.clicked = button
                return button

            def setDefaultButton(self, button):
                self.default = button

            def setEscapeButton(self, button):
                self.escape = button

            def exec(self):
                cancel = next(button for label, role, button in self.buttons if label == "Cancel")
                if self.default is not cancel or self.escape is not cancel:
                    raise AssertionError("Cancel was not the default and escape action")

            def clickedButton(self):
                return self.clicked

        app = QApplication.instance() or QApplication([])
        window = MainWindow()
        summary = ApplyConfirmationSummary(
            1,
            10,
            r"C:\FileFlowTest\Root",
            (("Documents", 1),),
            0,
            0,
            0,
            "Move only the exact ready file.",
        )
        with patch("fileflow.ui.main_window.QMessageBox", FakeMessageBox):
            self.assertFalse(window._confirm_apply(summary))
        window.close()
        self.assertIsNotNone(app)

    def test_undo_confirmation_defaults_and_escapes_to_cancel(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        try:
            from PyQt6.QtWidgets import QApplication, QMessageBox
            from fileflow.ui.main_window import MainWindow
            from fileflow.undo import UndoConfirmationSummary
        except Exception as exc:
            self.skipTest(f"PyQt6 UI unavailable: {exc}")

        class FakeMessageBox:
            ButtonRole = QMessageBox.ButtonRole

            def __init__(self, parent):
                self.buttons = []
                self.default = None
                self.escape = None
                self.clicked = None

            def setWindowTitle(self, title):
                self.title = title

            def setText(self, text):
                self.text = text

            def addButton(self, label, role):
                button = object()
                self.buttons.append((label, role, button))
                if role == QMessageBox.ButtonRole.RejectRole:
                    self.clicked = button
                return button

            def setDefaultButton(self, button):
                self.default = button

            def setEscapeButton(self, button):
                self.escape = button

            def exec(self):
                cancel = next(button for label, role, button in self.buttons if label == "Cancel")
                if self.default is not cancel or self.escape is not cancel:
                    raise AssertionError("Cancel was not the default and escape action")

            def clickedButton(self):
                return self.clicked

        app = QApplication.instance() or QApplication([])
        window = MainWindow()
        summary = UndoConfirmationSummary(1, 0, 0, 0, "FileFlow will move these files back to their original locations.")
        with patch("fileflow.ui.main_window.QMessageBox", FakeMessageBox):
            self.assertFalse(window._confirm_undo(summary))
        window.close()
        self.assertIsNotNone(app)


if __name__ == "__main__":
    unittest.main()

import json
import subprocess
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from fileflow.apply_controller import ApplyController, ApplyState
from fileflow.journal import JournalCoordinator
from fileflow.models import ErrorCode, FileIdentity, IdentitySnapshot, JournalState, Severity, StructuredError
from fileflow.operations.same_volume_move import SameVolumeMoveExecutor
from fileflow.preview_workflow import PreviewWorkflowService
from fileflow.safety import IdentityResult, WindowsFileIdentityProvider
from fileflow.storage import Database
from fileflow.undo import (
    UndoController,
    UndoControllerState,
    UndoJournalCoordinator,
    UndoMoveExecutor,
    UndoOperationStatus,
    UndoRecoveryClassification,
    UndoRecoveryInspector,
)


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


def apply_files(root: Path, database: Database, *names: str):
    (root / "Documents").mkdir(exist_ok=True)
    for name in names:
        (root / name).write_text(f"content:{name}", encoding="utf-8")
    plan = PreviewWorkflowService().analyse_folder(str(root)).plan
    if plan is None:
        raise AssertionError("Apply preview failed")
    plan = plan.__class__(**{**plan.__dict__, "operations": tuple(sorted(plan.operations, key=lambda operation: operation.source_path))})
    result = ApplyController(database).confirm_and_apply(plan, lambda summary: True)
    if result.state != ApplyState.COMPLETE or result.batch_id is None:
        raise AssertionError(f"Apply failed: {result}")
    return plan, result.batch_id


class FailingExistingPathIdentityProvider:
    def __init__(self, failing_paths):
        self.inner = WindowsFileIdentityProvider()
        self.failing_paths = {str(path).replace("/", "\\").casefold() for path in failing_paths}

    def snapshot(self, logical_path: str) -> IdentityResult:
        key = str(logical_path).replace("/", "\\").casefold()
        if key in self.failing_paths and Path(logical_path).exists():
            return IdentityResult(
                None,
                StructuredError(
                    ErrorCode.VERIFICATION_FAILED,
                    Severity.RECOVERY,
                    "Injected post-Undo identity failure.",
                    {"path": logical_path},
                ),
            )
        return self.inner.snapshot(logical_path)


class ThirdRootSnapshotChangesVolume:
    def __init__(self, root: Path):
        self.inner = WindowsFileIdentityProvider()
        self.root_key = str(root).replace("/", "\\").casefold()
        self.root_calls = 0

    def snapshot(self, logical_path: str) -> IdentityResult:
        result = self.inner.snapshot(logical_path)
        key = str(logical_path).replace("/", "\\").casefold()
        if key != self.root_key or not result.supported or result.snapshot is None:
            return result
        self.root_calls += 1
        if self.root_calls == 3:
            original = result.snapshot
            changed = IdentitySnapshot(
                FileIdentity("OTHER_VOLUME", original.identity.file_id, original.identity.file_type, original.identity.link_count),
                original.metadata,
            )
            return IdentityResult(changed)
        return result


class UndoIntegrationTests(unittest.TestCase):
    @require_windows
    def test_successful_single_file_undo_restores_exact_original_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = Database(":memory:")
            db.migrate()
            try:
                _, apply_batch = apply_files(root, db, "a.pdf")
                controller = UndoController(db)
                plan = controller.create_plan(apply_batch)

                self.assertEqual(1, len(plan.ready_operations))
                result = controller.confirm_and_undo(plan, lambda summary: True)

                self.assertEqual(UndoControllerState.COMPLETE, result.state)
                self.assertEqual(1, result.succeeded)
                self.assertTrue((root / "a.pdf").exists())
                self.assertFalse((root / "Documents" / "a.pdf").exists())
                self.assertEqual("content:a.pdf", (root / "a.pdf").read_text(encoding="utf-8"))
            finally:
                db.close()

    @require_windows
    def test_multi_file_undo(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = Database(":memory:")
            db.migrate()
            try:
                _, apply_batch = apply_files(root, db, "a.pdf", "b.pdf")
                controller = UndoController(db)
                result = controller.confirm_and_undo(controller.create_plan(apply_batch), lambda summary: True)

                self.assertEqual(2, result.succeeded)
                self.assertTrue((root / "a.pdf").exists())
                self.assertTrue((root / "b.pdf").exists())
                self.assertFalse((root / "Documents" / "a.pdf").exists())
                self.assertFalse((root / "Documents" / "b.pdf").exists())
            finally:
                db.close()

    @require_windows
    def test_edited_file_with_same_identity_is_allowed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = Database(":memory:")
            db.migrate()
            try:
                _, apply_batch = apply_files(root, db, "a.pdf")
                moved = root / "Documents" / "a.pdf"
                moved.write_text("edited after apply", encoding="utf-8")
                controller = UndoController(db)
                plan = controller.create_plan(apply_batch)

                self.assertTrue(plan.ready_operations[0].metadata_changed)
                result = controller.confirm_and_undo(plan, lambda summary: True)

                self.assertEqual(1, result.succeeded)
                self.assertEqual("edited after apply", (root / "a.pdf").read_text(encoding="utf-8"))
            finally:
                db.close()

    @require_windows
    def test_destination_identity_replaced_is_blocked(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = Database(":memory:")
            db.migrate()
            try:
                _, apply_batch = apply_files(root, db, "a.pdf")
                moved = root / "Documents" / "a.pdf"
                moved.unlink()
                moved.write_text("replacement", encoding="utf-8")
                plan = UndoController(db).create_plan(apply_batch)

                self.assertEqual(UndoOperationStatus.BLOCKED, plan.operations[0].status)
                self.assertEqual(ErrorCode.SOURCE_IDENTITY_CHANGED, plan.operations[0].reason.code)
                self.assertEqual("replacement", moved.read_text(encoding="utf-8"))
            finally:
                db.close()

    @require_windows
    def test_missing_or_manually_moved_destination_is_blocked_without_search(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = Database(":memory:")
            db.migrate()
            try:
                _, apply_batch = apply_files(root, db, "a.pdf")
                moved = root / "Documents" / "a.pdf"
                elsewhere = root / "Documents" / "elsewhere.pdf"
                moved.rename(elsewhere)
                plan = UndoController(db).create_plan(apply_batch)

                self.assertEqual(UndoOperationStatus.BLOCKED, plan.operations[0].status)
                self.assertEqual(ErrorCode.SOURCE_MISSING, plan.operations[0].reason.code)
                self.assertTrue(elsewhere.exists())
            finally:
                db.close()

    @require_windows
    def test_original_path_occupied_blocks_and_never_overwrites(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = Database(":memory:")
            db.migrate()
            try:
                _, apply_batch = apply_files(root, db, "a.pdf")
                (root / "a.pdf").write_text("occupant", encoding="utf-8")
                controller = UndoController(db)
                plan = controller.create_plan(apply_batch)

                self.assertEqual(UndoOperationStatus.BLOCKED, plan.operations[0].status)
                result = controller.confirm_and_undo(plan, lambda summary: True)
                self.assertEqual(UndoControllerState.PREVIEW_BLOCKED, result.state)
                self.assertEqual("occupant", (root / "a.pdf").read_text(encoding="utf-8"))
                self.assertTrue((root / "Documents" / "a.pdf").exists())
            finally:
                db.close()

    @require_windows
    def test_original_path_junction_blocks(self):
        with tempfile.TemporaryDirectory() as tmp:
            parent = Path(tmp)
            root = parent / "Root"
            outside = parent / "Outside"
            root.mkdir()
            outside.mkdir()
            db = Database(":memory:")
            db.migrate()
            try:
                _, apply_batch = apply_files(root, db, "a.pdf")
                if not create_junction(root / "a.pdf", outside):
                    self.skipTest("directory junction could not be created")
                plan = UndoController(db).create_plan(apply_batch)

                self.assertEqual(UndoOperationStatus.BLOCKED, plan.operations[0].status)
                self.assertTrue((root / "Documents" / "a.pdf").exists())
                self.assertFalse((outside / "a.pdf").exists())
            finally:
                db.close()

    @require_windows
    def test_same_volume_proof_failure_blocks_before_rename(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = Database(":memory:")
            db.migrate()
            try:
                _, apply_batch = apply_files(root, db, "a.pdf")
                factory = lambda: UndoMoveExecutor(identity_provider=ThirdRootSnapshotChangesVolume(root))
                plan = UndoController(db, executor_factory=factory).create_plan(apply_batch)

                self.assertEqual(UndoOperationStatus.BLOCKED, plan.operations[0].status)
                self.assertEqual(ErrorCode.CROSS_VOLUME_FAILURE, plan.operations[0].reason.code)
                self.assertTrue((root / "Documents" / "a.pdf").exists())
                self.assertFalse((root / "a.pdf").exists())
            finally:
                db.close()

    @require_windows
    def test_destination_path_junction_blocks(self):
        with tempfile.TemporaryDirectory() as tmp:
            parent = Path(tmp)
            root = parent / "Root"
            outside = parent / "Outside"
            root.mkdir()
            outside.mkdir()
            db = Database(":memory:")
            db.migrate()
            try:
                _, apply_batch = apply_files(root, db, "a.pdf")
                moved = root / "Documents" / "a.pdf"
                moved.unlink()
                if not create_junction(moved, outside):
                    self.skipTest("directory junction could not be created")
                plan = UndoController(db).create_plan(apply_batch)

                self.assertEqual(UndoOperationStatus.BLOCKED, plan.operations[0].status)
                self.assertFalse((root / "a.pdf").exists())
            finally:
                db.close()

    @require_windows
    def test_first_revalidation_blocks_before_confirmation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = Database(":memory:")
            db.migrate()
            try:
                _, apply_batch = apply_files(root, db, "a.pdf")
                controller = UndoController(db)
                plan = controller.create_plan(apply_batch)
                (root / "a.pdf").write_text("late occupant", encoding="utf-8")
                prompts = []

                result = controller.confirm_and_undo(plan, lambda summary: prompts.append(summary) or True)

                self.assertEqual(UndoControllerState.PREVIEW_STALE, result.state)
                self.assertEqual([], prompts)
                self.assertTrue((root / "Documents" / "a.pdf").exists())
            finally:
                db.close()

    @require_windows
    def test_second_revalidation_blocks_change_after_confirmation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = Database(":memory:")
            db.migrate()
            try:
                _, apply_batch = apply_files(root, db, "a.pdf")
                controller = UndoController(db)
                plan = controller.create_plan(apply_batch)

                def confirm(summary):
                    (root / "a.pdf").write_text("after confirmation", encoding="utf-8")
                    return True

                result = controller.confirm_and_undo(plan, confirm)

                self.assertEqual(UndoControllerState.PREVIEW_STALE, result.state)
                self.assertEqual("after confirmation", (root / "a.pdf").read_text(encoding="utf-8"))
                self.assertTrue((root / "Documents" / "a.pdf").exists())
            finally:
                db.close()

    @require_windows
    def test_recovery_required_first_operation_stops_second(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = Database(":memory:")
            db.migrate()
            try:
                _, apply_batch = apply_files(root, db, "a.pdf", "b.pdf")
                first_restore = root / "a.pdf"
                factory = lambda: UndoMoveExecutor(identity_provider=FailingExistingPathIdentityProvider({first_restore}))
                controller = UndoController(db, executor_factory=factory)
                plan = controller.create_plan(apply_batch)

                result = controller.confirm_and_undo(plan, lambda summary: True)

                self.assertEqual(UndoControllerState.RECOVERY_REQUIRED, result.state)
                self.assertTrue(first_restore.exists())
                self.assertTrue((root / "Documents" / "b.pdf").exists())
                self.assertFalse((root / "b.pdf").exists())
            finally:
                db.close()

    @require_windows
    def test_interrupted_first_operation_stops_second(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = Database(":memory:")
            db.migrate()
            try:
                _, apply_batch = apply_files(root, db, "a.pdf", "b.pdf")

                def interrupt_first(source, destination):
                    if Path(source).name == "a.pdf":
                        raise InterruptedError("injected interruption")
                    Path(source).rename(destination)

                controller = UndoController(db, executor_factory=lambda: UndoMoveExecutor(rename_primitive=interrupt_first))
                result = controller.confirm_and_undo(controller.create_plan(apply_batch), lambda summary: True)

                self.assertEqual(UndoControllerState.RECOVERY_REQUIRED, result.state)
                self.assertTrue((root / "Documents" / "a.pdf").exists())
                self.assertTrue((root / "Documents" / "b.pdf").exists())
                self.assertFalse((root / "a.pdf").exists())
                self.assertFalse((root / "b.pdf").exists())
            finally:
                db.close()

    @require_windows
    def test_safe_pre_mutation_failure_allows_unrelated_undo(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = Database(":memory:")
            db.migrate()
            try:
                _, apply_batch = apply_files(root, db, "a.pdf", "b.pdf")
                injected = []

                def occupy_first(preflight):
                    if Path(preflight.operation.source_path).name == "a.pdf" and not injected:
                        Path(preflight.operation.restore_path).write_text("late occupant", encoding="utf-8")
                        injected.append(True)

                controller = UndoController(db, executor_factory=lambda: UndoMoveExecutor(before_rename=occupy_first))
                result = controller.confirm_and_undo(controller.create_plan(apply_batch), lambda summary: True)

                self.assertEqual(UndoControllerState.COMPLETE, result.state)
                self.assertEqual(1, result.failed)
                self.assertEqual(1, result.succeeded)
                self.assertEqual("late occupant", (root / "a.pdf").read_text(encoding="utf-8"))
                self.assertTrue((root / "Documents" / "a.pdf").exists())
                self.assertTrue((root / "b.pdf").exists())
                self.assertFalse((root / "Documents" / "b.pdf").exists())
            finally:
                db.close()

    @require_windows
    def test_partial_apply_batch_only_successes_enter_undo_eligibility(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            documents = root / "Documents"
            documents.mkdir()
            (root / "ok.pdf").write_text("ready", encoding="utf-8")
            (root / "blocked.pdf").write_text("source", encoding="utf-8")
            (documents / "blocked.pdf").write_text("existing", encoding="utf-8")
            db = Database(":memory:")
            db.migrate()
            try:
                plan = PreviewWorkflowService().analyse_folder(str(root)).plan
                applied = ApplyController(db).confirm_and_apply(plan, lambda summary: True)
                undo_plan = UndoController(db).create_plan(applied.batch_id)

                self.assertEqual(1, len(undo_plan.operations))
                self.assertEqual(1, len(undo_plan.ready_operations))
                self.assertEqual(1, undo_plan.excluded_apply_count)
            finally:
                db.close()

    @require_windows
    def test_successful_undo_prevents_duplicate_attempt(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = Database(":memory:")
            db.migrate()
            try:
                _, apply_batch = apply_files(root, db, "a.pdf")
                controller = UndoController(db)
                first = controller.confirm_and_undo(controller.create_plan(apply_batch), lambda summary: True)
                second_plan = controller.create_plan(apply_batch)

                self.assertEqual(1, first.succeeded)
                self.assertEqual(UndoOperationStatus.BLOCKED, second_plan.operations[0].status)
                self.assertIn("successful or unresolved Undo", second_plan.operations[0].reason.message)
            finally:
                db.close()

    @require_windows
    def test_modified_in_memory_undo_plan_cannot_execute(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = Database(":memory:")
            db.migrate()
            try:
                _, apply_batch = apply_files(root, db, "a.pdf")
                controller = UndoController(db)
                plan = controller.create_plan(apply_batch)
                changed_operation = replace(plan.operations[0], restore_path=str(root / "different.pdf"))
                changed_plan = replace(plan, operations=(changed_operation,))

                result = controller.confirm_and_undo(changed_plan, lambda summary: True)

                self.assertEqual(UndoControllerState.PREVIEW_BLOCKED, result.state)
                self.assertTrue((root / "Documents" / "a.pdf").exists())
                self.assertFalse((root / "different.pdf").exists())
            finally:
                db.close()

    @require_windows
    def test_unresolved_apply_blocks_undo(self):
        with tempfile.TemporaryDirectory() as tmp:
            parent = Path(tmp)
            first_root = parent / "First"
            second_root = parent / "Second"
            first_root.mkdir()
            second_root.mkdir()
            db = Database(":memory:")
            db.migrate()
            try:
                _, apply_batch = apply_files(first_root, db, "a.pdf")
                second_plan = apply_files_preview(second_root, "b.pdf")
                JournalCoordinator(db, SameVolumeMoveExecutor()).execute_real_move_batch(second_plan, stop_after=JournalState.INTENT_RECORDED)
                controller = UndoController(db)
                undo_plan = controller.create_plan(apply_batch)

                self.assertFalse(controller.readiness(undo_plan).can_undo)
                self.assertEqual(UndoControllerState.RECOVERY_REQUIRED, controller.readiness(undo_plan).state)
            finally:
                db.close()

    @require_windows
    def test_unresolved_undo_blocks_apply_and_repeat_undo(self):
        with tempfile.TemporaryDirectory() as tmp:
            parent = Path(tmp)
            first_root = parent / "First"
            second_root = parent / "Second"
            first_root.mkdir()
            second_root.mkdir()
            db = Database(":memory:")
            db.migrate()
            try:
                _, apply_batch = apply_files(first_root, db, "a.pdf")
                undo_controller = UndoController(db)
                undo_plan = undo_controller.create_plan(apply_batch)
                UndoJournalCoordinator(db, UndoMoveExecutor()).execute_batch(undo_plan, stop_after=JournalState.INTENT_RECORDED)
                new_apply_plan = apply_files_preview(second_root, "b.pdf")

                self.assertFalse(ApplyController(db).readiness(new_apply_plan).can_apply)
                repeat_plan = undo_controller.create_plan(apply_batch)
                self.assertEqual(UndoOperationStatus.BLOCKED, repeat_plan.operations[0].status)
            finally:
                db.close()

    @require_windows
    def test_apply_history_is_immutable_and_undo_history_persists_after_reopen(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "Root"
            root.mkdir()
            database_path = Path(tmp) / "fileflow.db"
            db = Database(database_path)
            db.migrate()
            _, apply_batch = apply_files(root, db, "a.pdf")
            before_batch = dict(db.connection.execute("SELECT * FROM operation_batch WHERE id = ?", (apply_batch,)).fetchone())
            before_execution = dict(db.connection.execute("SELECT * FROM executed_operation WHERE batch_id = ?", (apply_batch,)).fetchone())
            controller = UndoController(db)
            result = controller.confirm_and_undo(controller.create_plan(apply_batch), lambda summary: True)
            after_batch = dict(db.connection.execute("SELECT * FROM operation_batch WHERE id = ?", (apply_batch,)).fetchone())
            after_execution = dict(db.connection.execute("SELECT * FROM executed_operation WHERE batch_id = ?", (apply_batch,)).fetchone())
            db.close()

            reopened = Database(database_path)
            reopened.migrate()
            try:
                self.assertEqual(before_batch, after_batch)
                self.assertEqual(before_execution, after_execution)
                rows = UndoController(reopened).list_batches()
                self.assertEqual(1, len(rows))
                self.assertEqual(result.batch_id, rows[0]["id"])
                self.assertEqual(1, rows[0]["succeeded_count"])
            finally:
                reopened.close()


class UndoRecoveryTests(unittest.TestCase):
    @require_windows
    def test_recovery_cases_likely_not_undone_and_likely_completed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = Database(":memory:")
            db.migrate()
            try:
                _, apply_batch = apply_files(root, db, "a.pdf")
                row = db.connection.execute("SELECT * FROM executed_operation WHERE batch_id = ?", (apply_batch,)).fetchone()
                inspector = UndoRecoveryInspector()

                not_undone = inspector.inspect(row["destination"], row["source_before"], row["identity_after_json"])
                Path(row["destination"]).rename(Path(row["source_before"]))
                completed = inspector.inspect(row["destination"], row["source_before"], row["identity_after_json"])

                self.assertEqual(UndoRecoveryClassification.LIKELY_NOT_UNDONE, not_undone.classification)
                self.assertEqual(UndoRecoveryClassification.LIKELY_UNDO_COMPLETED, completed.classification)
            finally:
                db.close()

    @require_windows
    def test_recovery_cases_both_neither_and_wrong_restore_identity(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = Database(":memory:")
            db.migrate()
            try:
                _, apply_batch = apply_files(root, db, "a.pdf")
                row = db.connection.execute("SELECT * FROM executed_operation WHERE batch_id = ?", (apply_batch,)).fetchone()
                source = Path(row["destination"])
                restore = Path(row["source_before"])
                expected_json = row["identity_after_json"]
                inspector = UndoRecoveryInspector()

                restore.write_text("wrong", encoding="utf-8")
                both = inspector.inspect(str(source), str(restore), expected_json)
                restore.unlink()
                source.unlink()
                neither = inspector.inspect(str(source), str(restore), expected_json)
                restore.write_text("wrong again", encoding="utf-8")
                wrong = inspector.inspect(str(source), str(restore), expected_json)

                self.assertEqual(UndoRecoveryClassification.CONFLICT_RECOVERY_REQUIRED, both.classification)
                self.assertEqual(UndoRecoveryClassification.MISSING_RECOVERY_REQUIRED, neither.classification)
                self.assertEqual(UndoRecoveryClassification.CONFLICT_RECOVERY_REQUIRED, wrong.classification)
            finally:
                db.close()


def apply_files_preview(root: Path, *names: str):
    (root / "Documents").mkdir(exist_ok=True)
    for name in names:
        (root / name).write_text(name, encoding="utf-8")
    plan = PreviewWorkflowService().analyse_folder(str(root)).plan
    if plan is None:
        raise AssertionError("preview failed")
    return plan


if __name__ == "__main__":
    unittest.main()

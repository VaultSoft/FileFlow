import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from fileflow.apply_controller import ApplyController, ApplyState, MAX_APPLY_OPERATIONS
from fileflow.journal import JournalCoordinator
from fileflow.models import ErrorCode, IdentitySnapshot, JournalState, Severity, StructuredError
from fileflow.operations.same_volume_move import SameVolumeMoveExecutor
from fileflow.preview_workflow import PreviewWorkflowService
from fileflow.safety import IdentityResult, WindowsFileIdentityProvider
from fileflow.storage import Database


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


class FailingPathIdentityProvider:
    def __init__(self, failing_paths):
        self.inner = WindowsFileIdentityProvider()
        self.failing_paths = {str(path).replace("/", "\\").casefold() for path in failing_paths}

    def snapshot(self, logical_path: str) -> IdentityResult:
        if str(logical_path).replace("/", "\\").casefold() in self.failing_paths:
            return IdentityResult(
                None,
                StructuredError(
                    ErrorCode.VERIFICATION_FAILED,
                    Severity.RECOVERY,
                    "Injected identity failure.",
                    {"path": logical_path},
                ),
            )
        return self.inner.snapshot(logical_path)


def make_plan(root: Path, *names: str):
    (root / "Documents").mkdir(exist_ok=True)
    for name in names:
        (root / name).write_text(name, encoding="utf-8")
    plan = PreviewWorkflowService().analyse_folder(str(root)).plan
    if plan is None:
        raise AssertionError("preview failed")
    return plan.__class__(**{**plan.__dict__, "operations": tuple(sorted(plan.operations, key=lambda operation: operation.source_path))})


class ApplyControllerTests(unittest.TestCase):
    @require_windows
    def test_cancel_and_close_confirmation_do_not_invoke_executor(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plan = make_plan(root, "a.pdf")
            db = Database(":memory:")
            db.migrate()
            controller = ApplyController(db)
            try:
                called = []
                result = controller.confirm_and_apply(plan, lambda summary: called.append(summary) and False)

                self.assertEqual(ApplyState.PREVIEW_VALID, result.state)
                self.assertEqual(1, len(called))
                self.assertTrue((root / "a.pdf").exists())
                self.assertFalse((root / "Documents" / "a.pdf").exists())
                self.assertEqual(0, len(controller.history_rows()))
            finally:
                db.close()

    @require_windows
    def test_affirmative_runs_only_after_two_revalidations(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plan = make_plan(root, "a.pdf")
            db = Database(":memory:")
            db.migrate()
            controller = ApplyController(db)
            try:
                result = controller.confirm_and_apply(plan, lambda summary: True)

                self.assertEqual(ApplyState.COMPLETE, result.state)
                self.assertFalse((root / "a.pdf").exists())
                self.assertTrue((root / "Documents" / "a.pdf").exists())
                self.assertEqual(1, result.succeeded)
            finally:
                db.close()

    @require_windows
    def test_stale_before_confirmation_blocks_without_prompt(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plan = make_plan(root, "a.pdf")
            (root / "Documents" / "a.pdf").write_text("collision", encoding="utf-8")
            db = Database(":memory:")
            db.migrate()
            controller = ApplyController(db)
            try:
                prompts = []
                result = controller.confirm_and_apply(plan, lambda summary: prompts.append(summary) or True)

                self.assertEqual(ApplyState.PREVIEW_STALE, result.state)
                self.assertEqual([], prompts)
                self.assertTrue((root / "a.pdf").exists())
            finally:
                db.close()

    @require_windows
    def test_stale_after_confirmation_blocks_without_move(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plan = make_plan(root, "a.pdf")
            db = Database(":memory:")
            db.migrate()
            controller = ApplyController(db)

            def confirm(summary):
                (root / "Documents" / "a.pdf").write_text("late collision", encoding="utf-8")
                return True

            try:
                result = controller.confirm_and_apply(plan, confirm)

                self.assertEqual(ApplyState.PREVIEW_STALE, result.state)
                self.assertTrue((root / "a.pdf").exists())
                self.assertEqual("late collision", (root / "Documents" / "a.pdf").read_text(encoding="utf-8"))
            finally:
                db.close()

    @require_windows
    def test_known_blocked_collision_is_excluded_while_ready_file_moves(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            documents = root / "Documents"
            documents.mkdir()
            (root / "ok.pdf").write_text("ready", encoding="utf-8")
            (root / "clash.pdf").write_text("source", encoding="utf-8")
            (documents / "clash.pdf").write_text("existing", encoding="utf-8")
            plan = PreviewWorkflowService().analyse_folder(str(root)).plan
            self.assertIsNotNone(plan)
            db = Database(":memory:")
            db.migrate()
            controller = ApplyController(db)
            confirmations = []
            try:
                result = controller.confirm_and_apply(
                    plan,
                    lambda summary: confirmations.append(summary) or True,
                )

                self.assertEqual(ApplyState.COMPLETE, result.state)
                self.assertEqual(1, len(confirmations))
                self.assertEqual(1, confirmations[0].operation_count)
                self.assertEqual(1, confirmations[0].blocked_count)
                self.assertIn("Blocked preview rows will not be moved", confirmations[0].message)
                self.assertEqual(1, result.succeeded)
                self.assertEqual(1, result.blocked)
                self.assertFalse((root / "ok.pdf").exists())
                self.assertEqual("ready", (documents / "ok.pdf").read_text(encoding="utf-8"))
                self.assertEqual("source", (root / "clash.pdf").read_text(encoding="utf-8"))
                self.assertEqual("existing", (documents / "clash.pdf").read_text(encoding="utf-8"))
            finally:
                db.close()

    @require_windows
    def test_mixed_blocked_rows_do_not_invalidate_actionable_preview(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            documents = root / "Documents"
            documents.mkdir()
            (root / "ok.pdf").write_text("ready", encoding="utf-8")
            (root / "clash.pdf").write_text("source", encoding="utf-8")
            (root / "photo.jpg").write_text("image", encoding="utf-8")
            (documents / "clash.pdf").write_text("existing", encoding="utf-8")
            plan = PreviewWorkflowService().analyse_folder(str(root)).plan
            self.assertIsNotNone(plan)
            db = Database(":memory:")
            db.migrate()
            controller = ApplyController(db)
            try:
                readiness = controller.validate_before_confirmation(plan)

                self.assertTrue(readiness.can_apply)
                self.assertEqual(1, readiness.ready_count)
                self.assertEqual(2, readiness.blocked_count)

                result = controller.confirm_and_apply(plan, lambda summary: True)

                self.assertEqual(ApplyState.COMPLETE, result.state)
                self.assertEqual(1, result.succeeded)
                self.assertEqual(2, result.blocked)
                self.assertEqual("ready", (documents / "ok.pdf").read_text(encoding="utf-8"))
                self.assertEqual("source", (root / "clash.pdf").read_text(encoding="utf-8"))
                self.assertEqual("image", (root / "photo.jpg").read_text(encoding="utf-8"))
                self.assertEqual("existing", (documents / "clash.pdf").read_text(encoding="utf-8"))
                self.assertFalse((root / "Images").exists())
            finally:
                db.close()

    @require_windows
    def test_actionable_file_becoming_stale_after_confirmation_still_blocks_mixed_plan(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            documents = root / "Documents"
            documents.mkdir()
            (root / "ok.pdf").write_text("ready", encoding="utf-8")
            (root / "clash.pdf").write_text("source", encoding="utf-8")
            (documents / "clash.pdf").write_text("existing", encoding="utf-8")
            plan = PreviewWorkflowService().analyse_folder(str(root)).plan
            self.assertIsNotNone(plan)
            db = Database(":memory:")
            db.migrate()
            controller = ApplyController(db)

            def make_actionable_destination_stale(summary):
                self.assertEqual(1, summary.operation_count)
                (documents / "ok.pdf").write_text("late collision", encoding="utf-8")
                return True

            try:
                result = controller.confirm_and_apply(plan, make_actionable_destination_stale)

                self.assertEqual(ApplyState.PREVIEW_STALE, result.state)
                self.assertEqual("ready", (root / "ok.pdf").read_text(encoding="utf-8"))
                self.assertEqual("late collision", (documents / "ok.pdf").read_text(encoding="utf-8"))
                self.assertEqual("source", (root / "clash.pdf").read_text(encoding="utf-8"))
                self.assertEqual("existing", (documents / "clash.pdf").read_text(encoding="utf-8"))
                self.assertEqual(0, len(controller.history_rows()))
            finally:
                db.close()

    @require_windows
    def test_multiple_successful_moves_and_history(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plan = make_plan(root, "a.pdf", "b.pdf")
            db = Database(":memory:")
            db.migrate()
            controller = ApplyController(db)
            try:
                result = controller.confirm_and_apply(plan, lambda summary: True)
                rows = controller.history_rows()

                self.assertEqual(ApplyState.COMPLETE, result.state)
                self.assertEqual(2, result.succeeded)
                self.assertTrue((root / "Documents" / "a.pdf").exists())
                self.assertTrue((root / "Documents" / "b.pdf").exists())
                self.assertEqual(1, len(rows))
                self.assertEqual(2, rows[0]["succeeded_count"])
            finally:
                db.close()

    @require_windows
    def test_destination_parent_junction_after_confirmation_blocks_without_move(self):
        with tempfile.TemporaryDirectory() as tmp:
            parent = Path(tmp)
            root = parent / "Root"
            outside = parent / "Outside"
            root.mkdir()
            outside.mkdir()
            plan = make_plan(root, "a.pdf")
            db = Database(":memory:")
            db.migrate()
            controller = ApplyController(db)

            def confirm(summary):
                documents = root / "Documents"
                documents.rmdir()
                if not create_junction(documents, outside):
                    self.skipTest("directory junction could not be created")
                return True

            try:
                result = controller.confirm_and_apply(plan, confirm)

                self.assertEqual(ApplyState.PREVIEW_BLOCKED, result.state)
                self.assertTrue((root / "a.pdf").exists())
                self.assertFalse((outside / "a.pdf").exists())
            finally:
                db.close()

    @require_windows
    def test_recovery_required_first_operation_leaves_second_untouched_and_locks_out(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plan = make_plan(root, "a.pdf", "b.pdf")
            first, second = plan.operations
            db = Database(":memory:")
            db.migrate()
            controller = ApplyController(
                db,
                executor_factory=lambda: SameVolumeMoveExecutor(identity_provider=FailingPathIdentityProvider({first.destination_path})),
            )
            try:
                result = controller.confirm_and_apply(plan, lambda summary: True)

                self.assertEqual(ApplyState.RECOVERY_REQUIRED, result.state)
                self.assertTrue((root / "Documents" / "a.pdf").exists())
                self.assertTrue(Path(second.source_path).exists())
                self.assertFalse(Path(second.destination_path).exists())
                self.assertFalse(controller.readiness(plan).can_apply)
                self.assertEqual(1, len(controller.operations_requiring_recovery()))
            finally:
                db.close()

    @require_windows
    def test_unresolved_previous_batch_blocks_new_apply(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plan = make_plan(root, "a.pdf")
            db = Database(":memory:")
            db.migrate()
            JournalCoordinator(db, SameVolumeMoveExecutor()).execute_real_move_batch(plan, stop_after=JournalState.INTENT_RECORDED)
            controller = ApplyController(db)
            try:
                readiness = controller.readiness(plan)

                self.assertFalse(readiness.can_apply)
                self.assertEqual(ApplyState.RECOVERY_REQUIRED, readiness.state)
            finally:
                db.close()

    @require_windows
    def test_batch_limit_blocks_apply(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plan = make_plan(root, *(f"{index}.pdf" for index in range(MAX_APPLY_OPERATIONS + 1)))
            db = Database(":memory:")
            db.migrate()
            controller = ApplyController(db)
            try:
                readiness = controller.readiness(plan)

                self.assertFalse(readiness.can_apply)
                self.assertIn(str(MAX_APPLY_OPERATIONS), readiness.message)
            finally:
                db.close()


if __name__ == "__main__":
    unittest.main()

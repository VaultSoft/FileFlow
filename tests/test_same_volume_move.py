import json
import subprocess
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from fileflow.journal import JournalCoordinator, JournalExecutionBlocked
from fileflow.models import (
    ErrorCode,
    FileIdentity,
    IdentitySnapshot,
    JournalState,
    MetadataSnapshot,
    PlannedOperationStatus,
    SafetyDecision,
    ScannedItem,
    ScannedItemKind,
    dataclass_to_jsonable,
)
from fileflow.operations.same_volume_move import (
    DestinationOccupancyInspector,
    OccupancyResult,
    OccupancyStatus,
    RecoveryClassification,
    SameVolumeMoveExecutor,
    SameVolumeMoveRecoveryInspector,
)
from fileflow.planner import PreviewPlanner
from fileflow.preview_workflow import PreviewWorkflowService
from fileflow.rules import default_categories, default_rules
from fileflow.safety import CloudInfo, FakeIdentityProvider, FakeReparseInspector, PathChainSafety, WindowsPathPolicy
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


class StaticOccupancy(DestinationOccupancyInspector):
    def __init__(self, statuses=None, default=OccupancyStatus.FREE):
        self.statuses = {path.replace("/", "\\").casefold(): status for path, status in (statuses or {}).items()}
        self.default = default

    def inspect(self, path: str) -> OccupancyResult:
        status = self.statuses.get(path.replace("/", "\\").casefold(), self.default)
        if status == OccupancyStatus.FREE:
            return OccupancyResult(OccupancyStatus.FREE)
        if status == OccupancyStatus.UNKNOWN:
            return OccupancyResult(
                OccupancyStatus.UNKNOWN,
                _error(ErrorCode.UNKNOWN_IO_ERROR, "Injected unknown occupancy.", path),
            )
        return OccupancyResult(
            OccupancyStatus.OCCUPIED,
            _error(ErrorCode.DESTINATION_EXISTS, "Injected occupied path.", path),
        )


class SafeCloudClassifier:
    def classify(self, path: str, reparse_info=None):
        return CloudInfo(True)


def _error(code, message, path):
    from fileflow.models import Severity, StructuredError

    return StructuredError(code, Severity.OPERATION_BLOCKING, message, {"path": path})


def fake_snapshot(path, file_id=None, file_type="file", volume_id="VOL"):
    return IdentitySnapshot(
        FileIdentity(volume_id, file_id or path.replace("/", "\\").casefold(), file_type, 1),
        MetadataSnapshot(path, 100, 10, 5),
    )


def make_fake_plan(*, source_volume="VOL", destination_parent_volume="VOL", failing_reparse_paths=None):
    policy = WindowsPathPolicy()
    root = policy.normalize(r"C:\FileFlowTest\Root")
    source = policy.normalize(r"C:\FileFlowTest\Root\doc.pdf")
    destination_parent = policy.normalize(r"C:\FileFlowTest\Root\Documents")
    identities_for_planning = {
        root: fake_snapshot(root, "root", "directory", source_volume),
        source: fake_snapshot(source, "source", "file", source_volume),
    }
    item = ScannedItem(source, "doc.pdf", ScannedItemKind.FILE, SafetyDecision.safe(source), identities_for_planning[source])
    plan = PreviewPlanner(
        policy,
        PathChainSafety(policy, FakeReparseInspector()),
        FakeIdentityProvider(identities_for_planning),
        entry_exists=lambda path: policy.normalize(path).casefold() == destination_parent.casefold(),
    ).create_plan(
        profile_id="default",
        source_root=root,
        destination_root=root,
        items=(item,),
        rules=default_rules(default_categories()),
        categories=default_categories(),
    )
    identities_for_execution = {
        root: identities_for_planning[root],
        source: identities_for_planning[source],
        destination_parent: fake_snapshot(destination_parent, "dest-parent", "directory", destination_parent_volume),
    }
    executor = SameVolumeMoveExecutor(
        chain_safety=PathChainSafety(policy, FakeReparseInspector(failing_paths=set(failing_reparse_paths or ()))),
        identity_provider=FakeIdentityProvider(identities_for_execution),
        cloud_classifier=SafeCloudClassifier(),
        occupancy=StaticOccupancy({source: OccupancyStatus.OCCUPIED, plan.operations[0].destination_path: OccupancyStatus.FREE}),
        directory_entries=lambda parent: (),
        rename_primitive=lambda source_path, destination_path: None,
    )
    return plan, executor


def planned_file(root: Path, name="doc.pdf"):
    (root / "Documents").mkdir(exist_ok=True)
    source = root / name
    source.write_text("source", encoding="utf-8")
    analysis = PreviewWorkflowService().analyse_folder(str(root))
    if analysis.plan is None:
        raise AssertionError(f"preview failed: {analysis.validation.decision}")
    operation = next(operation for operation in analysis.plan.operations if Path(operation.source_path).name == name)
    if operation.safety_status != PlannedOperationStatus.PLANNED:
        raise AssertionError(operation.structured_error)
    return analysis.plan, operation


def execute_real(plan, executor=None, stop_after=None):
    db = Database(":memory:")
    db.migrate()
    coordinator = JournalCoordinator(db, executor or SameVolumeMoveExecutor())
    batch_id = coordinator.execute_real_move_batch(plan, stop_after=stop_after)
    return db, coordinator, batch_id


class SameVolumeMoveExecutorTests(unittest.TestCase):
    @require_windows
    def test_real_same_volume_move_records_intent_before_mutation_and_verifies_identity(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plan, operation = planned_file(root)
            destination = Path(operation.destination_path)

            db, coordinator, batch_id = execute_real(plan)
            try:
                row = db.connection.execute(
                    "SELECT result, error_code, identity_before_json, identity_after_json FROM executed_operation WHERE batch_id = ?",
                    (batch_id,),
                ).fetchone()
                history = coordinator.operation_state_history(operation.id)

                self.assertFalse(Path(operation.source_path).exists())
                self.assertTrue(destination.exists())
                self.assertEqual(JournalState.SUCCEEDED.value, row["result"])
                self.assertIsNone(row["error_code"])
                self.assertEqual(
                    (
                        JournalState.INTENT_RECORDED.value,
                        JournalState.IN_PROGRESS.value,
                        JournalState.VERIFYING.value,
                        JournalState.SUCCEEDED.value,
                    ),
                    history,
                )
                before = json.loads(row["identity_before_json"])
                after = json.loads(row["identity_after_json"])
                self.assertEqual(before["identity"], after["identity"])
            finally:
                db.close()

    @require_windows
    def test_replaced_source_after_preview_is_blocked_without_move(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plan, operation = planned_file(root)
            source = Path(operation.source_path)
            source.unlink()
            source.write_text("replacement", encoding="utf-8")

            db, _, batch_id = execute_real(plan)
            try:
                row = db.connection.execute(
                    "SELECT result, error_code FROM executed_operation WHERE batch_id = ?",
                    (batch_id,),
                ).fetchone()

                self.assertTrue(source.exists())
                self.assertFalse(Path(operation.destination_path).exists())
                self.assertEqual(JournalState.BLOCKED.value, row["result"])
                self.assertEqual(ErrorCode.SOURCE_IDENTITY_CHANGED.value, row["error_code"])
            finally:
                db.close()

    @require_windows
    def test_destination_that_appears_before_execution_is_blocked(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plan, operation = planned_file(root)
            destination = Path(operation.destination_path)
            destination.write_text("occupied", encoding="utf-8")

            db, _, batch_id = execute_real(plan)
            try:
                row = db.connection.execute(
                    "SELECT result, error_code FROM executed_operation WHERE batch_id = ?",
                    (batch_id,),
                ).fetchone()

                self.assertTrue(Path(operation.source_path).exists())
                self.assertEqual("occupied", destination.read_text(encoding="utf-8"))
                self.assertEqual(JournalState.BLOCKED.value, row["result"])
                self.assertIn(row["error_code"], {ErrorCode.DESTINATION_APPEARED.value, ErrorCode.DESTINATION_EXISTS.value})
            finally:
                db.close()

    @require_windows
    def test_dangling_destination_symlink_counts_as_occupied(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plan, operation = planned_file(root)
            destination = Path(operation.destination_path)
            try:
                destination.symlink_to(root / "missing.pdf")
            except OSError as exc:
                self.skipTest(f"file symlink unavailable: {exc}")

            db, _, batch_id = execute_real(plan)
            try:
                row = db.connection.execute(
                    "SELECT result, error_code FROM executed_operation WHERE batch_id = ?",
                    (batch_id,),
                ).fetchone()

                self.assertTrue(Path(operation.source_path).exists())
                self.assertTrue(destination.is_symlink())
                self.assertEqual(JournalState.BLOCKED.value, row["result"])
                self.assertIn(row["error_code"], {ErrorCode.DESTINATION_APPEARED.value, ErrorCode.DESTINATION_EXISTS.value})
            finally:
                db.close()

    @require_windows
    def test_destination_created_between_final_check_and_rename_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plan, operation = planned_file(root)

            def create_destination(preflight):
                Path(preflight.operation.destination_path).write_text("late", encoding="utf-8")

            db, _, batch_id = execute_real(plan, SameVolumeMoveExecutor(before_rename=create_destination))
            try:
                row = db.connection.execute(
                    "SELECT result, error_code FROM executed_operation WHERE batch_id = ?",
                    (batch_id,),
                ).fetchone()

                self.assertTrue(Path(operation.source_path).exists())
                self.assertEqual("late", Path(operation.destination_path).read_text(encoding="utf-8"))
                self.assertEqual(JournalState.FAILED.value, row["result"])
                self.assertEqual(ErrorCode.DESTINATION_EXISTS.value, row["error_code"])
            finally:
                db.close()

    @require_windows
    def test_source_modified_between_final_check_and_rename_is_blocked(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plan, operation = planned_file(root)

            def modify_source(preflight):
                Path(preflight.operation.source_path).write_text("changed", encoding="utf-8")

            db, _, batch_id = execute_real(plan, SameVolumeMoveExecutor(before_rename=modify_source))
            try:
                row = db.connection.execute(
                    "SELECT result, error_code FROM executed_operation WHERE batch_id = ?",
                    (batch_id,),
                ).fetchone()

                self.assertTrue(Path(operation.source_path).exists())
                self.assertFalse(Path(operation.destination_path).exists())
                self.assertEqual(JournalState.FAILED.value, row["result"])
                self.assertEqual(ErrorCode.SOURCE_IDENTITY_CHANGED.value, row["error_code"])
            finally:
                db.close()

    @require_windows
    def test_destination_parent_replaced_between_final_check_and_rename_is_blocked(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plan, operation = planned_file(root)
            destination_parent = Path(operation.destination_path).parent

            def replace_parent(preflight):
                destination_parent.rmdir()
                destination_parent.mkdir()

            db, _, batch_id = execute_real(plan, SameVolumeMoveExecutor(before_rename=replace_parent))
            try:
                row = db.connection.execute(
                    "SELECT result, error_code FROM executed_operation WHERE batch_id = ?",
                    (batch_id,),
                ).fetchone()

                self.assertTrue(Path(operation.source_path).exists())
                self.assertFalse(Path(operation.destination_path).exists())
                self.assertEqual(JournalState.FAILED.value, row["result"])
                self.assertEqual(ErrorCode.DESTINATION_PARENT_CHANGED.value, row["error_code"])
            finally:
                db.close()

    @require_windows
    def test_approved_root_that_becomes_junction_is_blocked(self):
        with tempfile.TemporaryDirectory() as tmp:
            parent = Path(tmp)
            root = parent / "Root"
            outside = parent / "Outside"
            root.mkdir()
            outside.mkdir()
            plan, operation = planned_file(root)

            moved_root = parent / "OriginalRoot"
            root.rename(moved_root)
            if not create_junction(root, outside):
                self.skipTest("directory junction could not be created")

            db, _, batch_id = execute_real(plan)
            try:
                row = db.connection.execute(
                    "SELECT result, error_code FROM executed_operation WHERE batch_id = ?",
                    (batch_id,),
                ).fetchone()

                self.assertEqual(JournalState.BLOCKED.value, row["result"])
                self.assertEqual(ErrorCode.REPARSE_POINT.value, row["error_code"])
            finally:
                db.close()

    @require_windows
    def test_nested_destination_parent_junction_is_blocked(self):
        with tempfile.TemporaryDirectory() as tmp:
            parent = Path(tmp)
            root = parent / "Root"
            outside = parent / "OutsideDocuments"
            root.mkdir()
            outside.mkdir()
            plan, operation = planned_file(root)
            documents = root / "Documents"
            documents.rmdir()
            if not create_junction(documents, outside):
                self.skipTest("directory junction could not be created")

            db, _, batch_id = execute_real(plan)
            try:
                row = db.connection.execute(
                    "SELECT result, error_code FROM executed_operation WHERE batch_id = ?",
                    (batch_id,),
                ).fetchone()

                self.assertTrue(Path(operation.source_path).exists())
                self.assertFalse((outside / "doc.pdf").exists())
                self.assertEqual(JournalState.BLOCKED.value, row["result"])
                self.assertEqual(ErrorCode.REPARSE_POINT.value, row["error_code"])
            finally:
                db.close()

    @require_windows
    def test_interrupted_real_move_stops_batch_and_remains_recoverable(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "Documents").mkdir()
            first = root / "a.pdf"
            second = root / "b.pdf"
            first.write_text("a", encoding="utf-8")
            second.write_text("b", encoding="utf-8")
            plan = PreviewWorkflowService().analyse_folder(str(root)).plan
            self.assertIsNotNone(plan)
            plan = replace(plan, operations=tuple(sorted(plan.operations, key=lambda operation: operation.source_path)))

            def interrupt(source, destination):
                raise InterruptedError("test interruption")

            db, coordinator, batch_id = execute_real(plan, SameVolumeMoveExecutor(rename_primitive=interrupt))
            try:
                rows = tuple(db.connection.execute("SELECT result FROM executed_operation WHERE batch_id = ?", (batch_id,)))
                batch = db.connection.execute("SELECT status, recovery_required FROM operation_batch WHERE id = ?", (batch_id,)).fetchone()

                self.assertEqual(1, len(rows))
                self.assertEqual(JournalState.INTERRUPTED.value, rows[0]["result"])
                self.assertEqual(JournalState.INTERRUPTED.value, batch["status"])
                self.assertEqual(0, batch["recovery_required"])
                self.assertTrue(first.exists())
                self.assertTrue(second.exists())
                self.assertEqual(1, len(coordinator.operations_requiring_recovery()))
                with self.assertRaises(JournalExecutionBlocked):
                    JournalCoordinator(db, SameVolumeMoveExecutor()).execute_real_move_batch(plan)
            finally:
                db.close()

    @require_windows
    def test_blocked_real_move_does_not_abort_unrelated_safe_move(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "Documents").mkdir()
            first = root / "a.pdf"
            second = root / "b.pdf"
            first.write_text("a", encoding="utf-8")
            second.write_text("b", encoding="utf-8")
            plan = PreviewWorkflowService().analyse_folder(str(root)).plan
            self.assertIsNotNone(plan)
            plan = replace(plan, operations=tuple(sorted(plan.operations, key=lambda operation: operation.source_path)))
            blocked_operation, safe_operation = plan.operations
            Path(blocked_operation.destination_path).write_text("occupied", encoding="utf-8")

            db, _, batch_id = execute_real(plan)
            try:
                rows = {
                    row["planned_operation_id"]: row["result"]
                    for row in db.connection.execute(
                        "SELECT planned_operation_id, result FROM executed_operation WHERE batch_id = ?",
                        (batch_id,),
                    )
                }
                batch = db.connection.execute("SELECT status FROM operation_batch WHERE id = ?", (batch_id,)).fetchone()

                self.assertEqual(JournalState.BLOCKED.value, rows[blocked_operation.id])
                self.assertEqual(JournalState.SUCCEEDED.value, rows[safe_operation.id])
                self.assertEqual("PARTIAL_FAILURE", batch["status"])
                self.assertTrue(Path(blocked_operation.source_path).exists())
                self.assertTrue(Path(safe_operation.destination_path).exists())
                self.assertFalse(Path(safe_operation.source_path).exists())
            finally:
                db.close()

    @require_windows
    def test_recovery_inspector_classifies_completed_and_unmoved_windows(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plan, operation = planned_file(root)
            executor = SameVolumeMoveExecutor()
            preflight = executor.prepare(plan, operation).preflight
            self.assertIsNotNone(preflight)
            identity_json = json.dumps(dataclass_to_jsonable(preflight.source_identity_before), sort_keys=True)
            inspector = SameVolumeMoveRecoveryInspector()

            unmoved = inspector.inspect(operation.source_path, operation.destination_path, identity_json)
            self.assertEqual(RecoveryClassification.LIKELY_NOT_MOVED, unmoved.classification)

            result = executor.move(preflight)
            self.assertEqual("SUCCEEDED", result.outcome.value)
            completed = inspector.inspect(operation.source_path, operation.destination_path, identity_json)
            self.assertEqual(RecoveryClassification.LIKELY_COMPLETED, completed.classification)

    def test_cross_volume_identity_mismatch_is_blocked_before_rename(self):
        plan, executor = make_fake_plan(source_volume="VOL-A", destination_parent_volume="VOL-B")

        result = executor.prepare(plan, plan.operations[0])

        self.assertFalse(result.allowed)
        self.assertEqual(ErrorCode.CROSS_VOLUME_FAILURE, result.error.code)

    def test_reparse_inspection_failure_blocks_conservatively(self):
        plan, executor = make_fake_plan(failing_reparse_paths={r"C:\FileFlowTest\Root\doc.pdf"})

        result = executor.prepare(plan, plan.operations[0])

        self.assertFalse(result.allowed)
        self.assertEqual(ErrorCode.REPARSE_POINT, result.error.code)

    def test_unknown_destination_occupancy_blocks_conservatively(self):
        plan, executor = make_fake_plan()
        operation = plan.operations[0]
        executor.occupancy = StaticOccupancy(
            {
                operation.source_path: OccupancyStatus.OCCUPIED,
                operation.destination_path: OccupancyStatus.UNKNOWN,
            }
        )

        result = executor.prepare(plan, operation)

        self.assertFalse(result.allowed)
        self.assertEqual(ErrorCode.UNKNOWN_IO_ERROR, result.error.code)


if __name__ == "__main__":
    unittest.main()

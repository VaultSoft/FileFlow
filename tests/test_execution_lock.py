import ntpath
import subprocess
import sys
import unittest
from dataclasses import replace

from fileflow.apply_controller import ApplyController, ApplyState
from fileflow.journal import (
    ExecutionLockState,
    JournalCoordinator,
    JournalExecutionBlocked,
    MockOperationExecutor,
)
from fileflow.models import (
    ErrorCode,
    FileIdentity,
    IdentitySnapshot,
    JournalState,
    MetadataSnapshot,
    SafetyDecision,
    ScannedItem,
    ScannedItemKind,
    Severity,
    StructuredError,
)
from fileflow.planner import PreviewPlanner
from fileflow.operations.same_volume_move import SameVolumeMoveExecutor
from fileflow.process_identity import (
    ProcessIdentity,
    ProcessOwnerState,
    ProcessQueryResult,
    ProcessQueryState,
    WindowsProcessIdentityProbe,
)
from fileflow.rules import default_categories, default_rules
from fileflow.safety import FakeIdentityProvider, FakeReparseInspector, PathChainSafety, WindowsPathPolicy
from fileflow.storage import Database



def snapshot(path, file_id=None, file_type="file"):
    return IdentitySnapshot(
        FileIdentity("VOL", file_id or path.casefold(), file_type, 1),
        MetadataSnapshot(path, 100, 10, 5),
    )


class FakeProcessProbe:
    def __init__(self, owner_state=ProcessOwnerState.ALIVE, identity=None):
        self.state = owner_state
        self.identity = identity or ProcessIdentity(4242, "111222333")

    def current_identity(self):
        return self.identity

    def owner_state(self, process_id, process_started_at):
        return self.state


def make_plan():
    policy = WindowsPathPolicy()
    root = r"C:\FileFlowTest\Root"
    source = r"C:\FileFlowTest\Root\doc.pdf"
    identities = {
        root: snapshot(root, "root", "directory"),
        source: snapshot(source, "doc"),
    }
    item = ScannedItem(source, "doc.pdf", ScannedItemKind.FILE, SafetyDecision.safe(source), identities[source])
    return PreviewPlanner(
        policy,
        PathChainSafety(policy, FakeReparseInspector()),
        FakeIdentityProvider(identities),
        entry_exists=lambda path: ntpath.splitext(ntpath.basename(policy.normalize(path)))[1] == "",
    ).create_plan(
        profile_id="default",
        source_root=root,
        destination_root=root,
        items=(item,),
        rules=default_rules(default_categories()),
        categories=default_categories(),
    )


def insert_lock(db, *, owner="owner-token", process_id=4242, started_at="111222333"):
    with db.connection:
        db.connection.execute(
            """
            INSERT INTO execution_lock(id, owner, acquired_at, process_id, process_started_at)
            VALUES (1, ?, '2026-01-01T00:00:00+00:00', ?, ?)
            """,
            (owner, process_id, started_at),
        )


class ExecutionLockTests(unittest.TestCase):
    def setUp(self):
        self.db = Database(":memory:")
        self.db.migrate()

    def tearDown(self):
        self.db.close()

    def coordinator(self, owner_state=ProcessOwnerState.ALIVE):
        return JournalCoordinator(
            self.db,
            MockOperationExecutor(),
            process_probe=FakeProcessProbe(owner_state),
        )

    def test_live_owner_without_operation_rows_keeps_apply_blocked(self):
        insert_lock(self.db)

        result = self.coordinator(ProcessOwnerState.ALIVE).reconcile_execution_lock()

        self.assertEqual(ExecutionLockState.ACTIVE, result.state)
        self.assertTrue(result.blocks_apply)
        self.assertEqual(1, self.db.connection.execute("SELECT COUNT(*) FROM execution_lock").fetchone()[0])

    def test_dead_owner_without_unresolved_rows_is_safely_recovered(self):
        insert_lock(self.db)

        result = self.coordinator(ProcessOwnerState.DEAD).reconcile_execution_lock()

        self.assertEqual(ExecutionLockState.RECOVERED, result.state)
        self.assertFalse(result.blocks_apply)
        self.assertEqual(0, self.db.connection.execute("SELECT COUNT(*) FROM execution_lock").fetchone()[0])

    def test_dead_owner_with_each_unresolved_state_keeps_apply_blocked(self):
        for state in (
            JournalState.INTENT_RECORDED,
            JournalState.IN_PROGRESS,
            JournalState.VERIFYING,
            JournalState.RECOVERY_REQUIRED,
        ):
            with self.subTest(state=state):
                db = Database(":memory:")
                db.migrate()
                try:
                    coordinator = JournalCoordinator(db, MockOperationExecutor())
                    batch_id = coordinator.execute_mock_batch(
                        make_plan(),
                        stop_after=JournalState.INTENT_RECORDED if state == JournalState.RECOVERY_REQUIRED else state,
                    )
                    if state == JournalState.RECOVERY_REQUIRED:
                        coordinator.mark_recovery_required_for_batch(batch_id)
                    insert_lock(db)

                    result = JournalCoordinator(
                        db,
                        MockOperationExecutor(),
                        process_probe=FakeProcessProbe(ProcessOwnerState.DEAD),
                    ).reconcile_execution_lock()

                    self.assertEqual(ExecutionLockState.UNRESOLVED_WORK, result.state)
                    self.assertTrue(result.blocks_apply)
                    self.assertEqual(1, db.connection.execute("SELECT COUNT(*) FROM execution_lock").fetchone()[0])
                finally:
                    db.close()

    def test_unknown_or_legacy_owner_identity_fails_closed(self):
        insert_lock(self.db)
        result = self.coordinator(ProcessOwnerState.UNKNOWN).reconcile_execution_lock()
        self.assertEqual(ExecutionLockState.UNKNOWN_OWNER, result.state)
        self.assertTrue(result.blocks_apply)

        with self.db.connection:
            self.db.connection.execute("DELETE FROM execution_lock")
            self.db.connection.execute(
                "INSERT INTO execution_lock(id, owner, acquired_at) VALUES (1, 'legacy', 'old')"
            )
        legacy = self.coordinator(ProcessOwnerState.DEAD).reconcile_execution_lock()
        self.assertEqual(ExecutionLockState.UNKNOWN_OWNER, legacy.state)

    def test_only_exact_owner_identity_can_release_lock(self):
        coordinator = self.coordinator(ProcessOwnerState.ALIVE)
        owner = coordinator._acquire_execution_lock()

        self.assertFalse(coordinator._release_execution_lock(replace(owner, token="different-token")))
        self.assertFalse(coordinator._release_execution_lock(replace(owner, process_started_at="reused-pid")))
        self.assertEqual(1, self.db.connection.execute("SELECT COUNT(*) FROM execution_lock").fetchone()[0])
        self.assertTrue(coordinator._release_execution_lock(owner))
        self.assertEqual(0, self.db.connection.execute("SELECT COUNT(*) FROM execution_lock").fetchone()[0])

    def test_apply_readiness_recovers_dead_empty_lock(self):
        insert_lock(self.db)
        controller = ApplyController(
            self.db,
            process_probe=FakeProcessProbe(ProcessOwnerState.DEAD),
        )

        readiness = controller.readiness(None)

        self.assertEqual(ApplyState.NO_PREVIEW, readiness.state)
        self.assertEqual(0, self.db.connection.execute("SELECT COUNT(*) FROM execution_lock").fetchone()[0])

    @unittest.skipUnless(sys.platform == "win32", "Windows process identity integration")
    def test_windows_probe_distinguishes_live_owner_from_reused_pid_identity(self):
        probe = WindowsProcessIdentityProbe()
        identity = probe.current_identity()

        self.assertIsNotNone(identity)
        self.assertEqual(
            ProcessOwnerState.ALIVE,
            probe.owner_state(identity.process_id, identity.process_started_at),
        )
        self.assertEqual(
            ProcessOwnerState.DEAD,
            probe.owner_state(identity.process_id, identity.process_started_at + "-different"),
        )

    @unittest.skipUnless(sys.platform == "win32", "Windows process identity integration")
    def test_windows_probe_reports_exited_process_dead_while_handle_remains_open(self):
        probe = WindowsProcessIdentityProbe()
        process = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(0.2)"],
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        try:
            query = probe.process_query(process.pid)
            self.assertEqual(ProcessQueryState.ACTIVE, query.state)
            self.assertIsNotNone(query.process_started_at)
            process.wait(timeout=5)

            self.assertEqual(
                ProcessOwnerState.DEAD,
                probe.owner_state(process.pid, query.process_started_at),
            )
        finally:
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=5)

    def test_exit_code_query_failure_is_unknown(self):
        probe = WindowsProcessIdentityProbe(
            process_query=lambda process_id: ProcessQueryResult(
                ProcessQueryState.UNKNOWN,
                reason="exit_code_query_failed",
            )
        )
        self.assertEqual(ProcessOwnerState.UNKNOWN, probe.owner_state(10, "started"))

    def test_access_denied_process_query_is_unknown(self):
        probe = WindowsProcessIdentityProbe(
            process_query=lambda process_id: ProcessQueryResult(
                ProcessQueryState.UNKNOWN,
                reason="open_process_failed:5",
            )
        )
        self.assertEqual(ProcessOwnerState.UNKNOWN, probe.owner_state(10, "started"))

    def test_injected_live_and_reused_pid_decisions_retain_start_time_guard(self):
        probe = WindowsProcessIdentityProbe(
            process_query=lambda process_id: ProcessQueryResult(ProcessQueryState.ACTIVE, "actual-start")
        )
        self.assertEqual(ProcessOwnerState.ALIVE, probe.owner_state(10, "actual-start"))
        self.assertEqual(ProcessOwnerState.DEAD, probe.owner_state(10, "old-start"))

    def test_original_execution_error_is_preserved_when_lock_release_also_fails(self):
        coordinator = JournalCoordinator(
            self.db,
            SameVolumeMoveExecutor(),
            process_probe=FakeProcessProbe(ProcessOwnerState.ALIVE),
        )

        def fail_start(batch_id):
            raise RuntimeError("original execution failure")

        coordinator._mark_batch_started = fail_start
        coordinator._release_execution_lock = lambda owner: False

        with self.assertRaisesRegex(RuntimeError, "original execution failure") as captured:
            coordinator.execute_real_move_batch(make_plan())

        self.assertTrue(hasattr(captured.exception, "lock_release_error"))
        self.assertIn("could not safely release", str(captured.exception.lock_release_error))
        self.assertTrue(any("could not safely release" in note for note in captured.exception.__notes__))

    def test_apply_rechecks_unresolved_work_after_acquiring_lock(self):
        coordinator = JournalCoordinator(
            self.db,
            SameVolumeMoveExecutor(),
            process_probe=FakeProcessProbe(ProcessOwnerState.ALIVE),
        )
        calls = []

        def unresolved_check(plan_id):
            calls.append(plan_id)
            if len(calls) == 2:
                raise JournalExecutionBlocked(
                    StructuredError(
                        ErrorCode.RECOVERY_REQUIRED,
                        Severity.RECOVERY,
                        "Injected unresolved work after lock acquisition.",
                    )
                )

        coordinator._raise_if_any_unresolved_real_work = unresolved_check

        with self.assertRaisesRegex(JournalExecutionBlocked, "Injected unresolved work"):
            coordinator.execute_real_move_batch(make_plan())

        self.assertEqual(2, len(calls))
        self.assertEqual(0, self.db.connection.execute("SELECT COUNT(*) FROM execution_lock").fetchone()[0])
        self.assertEqual(0, self.db.connection.execute("SELECT COUNT(*) FROM operation_batch").fetchone()[0])


if __name__ == "__main__":
    unittest.main()

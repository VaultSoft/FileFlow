import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from fileflow.journal import JournalCoordinator, JournalStateMachine, MockExecutorConfig, MockOperationExecutor
from fileflow.models import ExecutionOutcome, JournalState, PlannedOperationStatus, RevalidationReason, RevalidationStatus, SafetyDecision, ScannedItem, ScannedItemKind
from fileflow.planner import PlanRevalidator, PreviewPlanner
from fileflow.rules import default_categories, default_rules
from fileflow.safety import FakeIdentityProvider, FakeReparseInspector, PathChainSafety, WindowsPathPolicy
from fileflow.storage import Database, PlanRepository

from fileflow.models import FileIdentity, IdentitySnapshot, MetadataSnapshot


def snapshot(path, file_id=None, file_type="file"):
    return IdentitySnapshot(
        FileIdentity("VOL", file_id or path.casefold(), file_type, 1),
        MetadataSnapshot(path, 100, 10, 5),
    )


class StorageAndJournalTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Database(":memory:")
        self.db.migrate()
        self.policy = WindowsPathPolicy()
        self.chain = PathChainSafety(self.policy, FakeReparseInspector())

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def make_plan(self):
        root = r"C:\FileFlowTest\Root"
        source = r"C:\FileFlowTest\Root\doc.pdf"
        identities = {root: snapshot(root, "root", "directory"), source: snapshot(source, "doc")}
        item = ScannedItem(source, "doc.pdf", ScannedItemKind.FILE, SafetyDecision.safe(source), identities[source])
        return PreviewPlanner(self.policy, self.chain, FakeIdentityProvider(identities)).create_plan(
            profile_id="default",
            source_root=root,
            destination_root=root,
            items=(item,),
            rules=default_rules(default_categories()),
            categories=default_categories(),
        )

    def test_migrations_are_idempotent_and_plan_persists(self):
        self.db.migrate()
        plan = self.make_plan()
        repo = PlanRepository(self.db)
        repo.save_plan(plan)
        self.assertEqual(1, repo.plan_count())
        self.assertEqual(1, repo.operation_count())

    def test_plan_reload_preserves_snapshots_and_detects_rule_change(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "fileflow.db"
            db = Database(db_path)
            db.migrate()
            plan = self.make_plan()
            collided = PreviewPlanner(self.policy, self.chain, FakeIdentityProvider({
                plan.source_root_normalized: plan.source_root_identity,
                plan.operations[0].source_path: plan.operations[0].source_identity,
            })).with_destination_collisions(plan, (plan.operations[0].destination_path.lower(),))
            PlanRepository(db).save_plan(collided)
            plan_id = collided.id
            db.close()

            reopened = Database(db_path)
            reopened.migrate()
            loaded = PlanRepository(reopened).load_plan(plan_id)
            self.assertIsNotNone(loaded)
            self.assertEqual(collided.operations[0].destination_path, loaded.operations[0].destination_path)
            self.assertEqual(collided.operations[0].source_identity, loaded.operations[0].source_identity)
            self.assertEqual(collided.operations[0].conflict_status, loaded.operations[0].conflict_status)
            self.assertEqual(collided.operations[0].structured_error, loaded.operations[0].structured_error)

            changed_rules = tuple(replace(rule, extensions=rule.extensions + (".changed",)) for rule in default_rules(default_categories()))
            result = PlanRevalidator(self.policy, self.chain, FakeIdentityProvider({
                loaded.source_root_normalized: loaded.source_root_identity,
                loaded.operations[0].source_path: loaded.operations[0].source_identity,
            })).revalidate(loaded, changed_rules, default_categories())
            self.assertEqual(RevalidationStatus.STALE, result.status)
            self.assertIn(RevalidationReason.RULE_SNAPSHOT_CHANGED, result.reasons)
            reopened.close()

    def test_state_machine_rejects_illegal_transition(self):
        machine = JournalStateMachine()
        self.assertEqual(JournalState.APPROVED, machine.transition(JournalState.PLANNED, JournalState.APPROVED))
        with self.assertRaises(ValueError):
            machine.transition(JournalState.PLANNED, JournalState.SUCCEEDED)

    def test_mock_journal_records_success(self):
        plan = self.make_plan()
        repo = PlanRepository(self.db)
        repo.save_plan(plan)
        batch_id = JournalCoordinator(self.db, MockOperationExecutor()).execute_mock_batch(plan)
        row = self.db.connection.execute("SELECT result FROM executed_operation WHERE batch_id = ?", (batch_id,)).fetchone()
        self.assertEqual(JournalState.SUCCEEDED.value, row["result"])
        history = JournalCoordinator(self.db, MockOperationExecutor()).operation_state_history(plan.operations[0].id)
        self.assertEqual(
            (
                JournalState.INTENT_RECORDED.value,
                JournalState.IN_PROGRESS.value,
                JournalState.VERIFYING.value,
                JournalState.SUCCEEDED.value,
            ),
            history,
        )

    def test_mock_journal_records_individual_failure_without_aborting_safe_work(self):
        plan = self.make_plan()
        op_id = plan.operations[0].id
        executor = MockOperationExecutor(MockExecutorConfig({op_id: ExecutionOutcome.FAILED}))
        batch_id = JournalCoordinator(self.db, executor).execute_mock_batch(plan)
        row = self.db.connection.execute("SELECT result, error_code FROM executed_operation WHERE batch_id = ?", (batch_id,)).fetchone()
        self.assertEqual(JournalState.FAILED.value, row["result"])
        self.assertIsNotNone(row["error_code"])

    def test_verification_failure_does_not_become_success(self):
        plan = self.make_plan()
        op_id = plan.operations[0].id
        executor = MockOperationExecutor(MockExecutorConfig({op_id: ExecutionOutcome.VERIFICATION_FAILED}))
        batch_id = JournalCoordinator(self.db, executor).execute_mock_batch(plan)
        row = self.db.connection.execute("SELECT result FROM executed_operation WHERE batch_id = ?", (batch_id,)).fetchone()
        self.assertEqual(JournalState.FAILED.value, row["result"])
        history = JournalCoordinator(self.db, MockOperationExecutor()).operation_state_history(op_id)
        self.assertNotIn(JournalState.SUCCEEDED.value, history)

    def test_interruption_is_persisted_and_recoverable_after_restart(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "fileflow.db"
            db = Database(db_path)
            db.migrate()
            policy = WindowsPathPolicy()
            chain = PathChainSafety(policy, FakeReparseInspector())
            root = r"C:\FileFlowTest\Root"
            source = r"C:\FileFlowTest\Root\doc.pdf"
            identities = {root: snapshot(root, "root", "directory"), source: snapshot(source, "doc")}
            item = ScannedItem(source, "doc.pdf", ScannedItemKind.FILE, SafetyDecision.safe(source), identities[source])
            plan = PreviewPlanner(policy, chain, FakeIdentityProvider(identities)).create_plan(
                profile_id="default",
                source_root=root,
                destination_root=root,
                items=(item,),
                rules=default_rules(default_categories()),
                categories=default_categories(),
            )
            batch_id = JournalCoordinator(
                db,
                MockOperationExecutor(MockExecutorConfig({plan.operations[0].id: ExecutionOutcome.INTERRUPTED})),
            ).execute_mock_batch(plan)
            db.close()

            reopened = Database(db_path)
            coordinator = JournalCoordinator(reopened, MockOperationExecutor())
            self.assertEqual(1, coordinator.mark_recovery_required())
            recovery_rows = coordinator.operations_requiring_recovery()
            self.assertEqual(1, len(recovery_rows))
            self.assertEqual(JournalState.RECOVERY_REQUIRED.value, recovery_rows[0]["result"])
            batch = reopened.connection.execute("SELECT status, recovery_required FROM operation_batch WHERE id = ?", (batch_id,)).fetchone()
            self.assertEqual(JournalState.RECOVERY_REQUIRED.value, batch["status"])
            self.assertEqual(1, batch["recovery_required"])
            reopened.close()

    def test_partial_batch_state_is_represented(self):
        plan = self.make_plan()
        second = replace(plan.operations[0], id="second-op", source_path=r"C:\FileFlowTest\Root\other.pdf")
        plan = replace(plan, operations=(plan.operations[0], second))
        executor = MockOperationExecutor(MockExecutorConfig({second.id: ExecutionOutcome.FAILED}))
        batch_id = JournalCoordinator(self.db, executor).execute_mock_batch(plan)
        batch = self.db.connection.execute("SELECT status FROM operation_batch WHERE id = ?", (batch_id,)).fetchone()
        self.assertEqual("PARTIAL_FAILURE", batch["status"])

    def test_blocked_operations_are_not_executed(self):
        plan = self.make_plan()
        blocked = plan.operations[0].__class__(
            **{**plan.operations[0].__dict__, "safety_status": PlannedOperationStatus.BLOCKED}
        )
        plan = plan.__class__(**{**plan.__dict__, "operations": (blocked,)})
        executor = MockOperationExecutor()
        batch_id = JournalCoordinator(self.db, executor).execute_mock_batch(plan)
        self.assertEqual([], executor.executed_operation_ids)
        row = self.db.connection.execute("SELECT result FROM executed_operation WHERE batch_id = ?", (batch_id,)).fetchone()
        self.assertEqual(JournalState.BLOCKED.value, row["result"])

    def test_recovery_marks_incomplete_states(self):
        plan = self.make_plan()
        batch_id = JournalCoordinator(self.db, MockOperationExecutor()).execute_mock_batch(plan)
        self.db.connection.execute(
            "UPDATE executed_operation SET result = ? WHERE batch_id = ?",
            (JournalState.IN_PROGRESS.value, batch_id),
        )
        count = JournalCoordinator(self.db, MockOperationExecutor()).mark_recovery_required()
        self.assertEqual(1, count)
        row = self.db.connection.execute("SELECT result FROM executed_operation WHERE batch_id = ?", (batch_id,)).fetchone()
        self.assertEqual(JournalState.RECOVERY_REQUIRED.value, row["result"])


if __name__ == "__main__":
    unittest.main()

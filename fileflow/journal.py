from __future__ import annotations

import json
import uuid
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Protocol

from .app_metadata import APP_VERSION
from .models import (
    ErrorCode,
    ExecutionOutcome,
    ExecutionResult,
    JournalState,
    PlannedOperation,
    PlannedOperationStatus,
    PreviewPlan,
    Severity,
    StructuredError,
    dataclass_to_jsonable,
)
from .operations.same_volume_move import SameVolumeMoveExecutor
from .storage import Database


LEGAL_TRANSITIONS: dict[JournalState, frozenset[JournalState]] = {
    JournalState.PLANNED: frozenset({JournalState.APPROVED, JournalState.BLOCKED}),
    JournalState.APPROVED: frozenset({JournalState.INTENT_RECORDED, JournalState.BLOCKED}),
    JournalState.INTENT_RECORDED: frozenset({JournalState.IN_PROGRESS, JournalState.RECOVERY_REQUIRED}),
    JournalState.IN_PROGRESS: frozenset({JournalState.VERIFYING, JournalState.FAILED, JournalState.INTERRUPTED, JournalState.RECOVERY_REQUIRED}),
    JournalState.VERIFYING: frozenset({JournalState.SUCCEEDED, JournalState.FAILED, JournalState.RECOVERY_REQUIRED}),
    JournalState.SUCCEEDED: frozenset(),
    JournalState.FAILED: frozenset(),
    JournalState.BLOCKED: frozenset(),
    JournalState.INTERRUPTED: frozenset({JournalState.RECOVERY_REQUIRED}),
    JournalState.RECOVERY_REQUIRED: frozenset(),
}


class JournalStateMachine:
    def transition(self, current: JournalState, requested: JournalState) -> JournalState:
        if requested not in LEGAL_TRANSITIONS[current]:
            raise ValueError(f"Illegal journal transition: {current.value} -> {requested.value}")
        return requested

    def recoverable_states(self) -> tuple[JournalState, ...]:
        return (JournalState.INTENT_RECORDED, JournalState.IN_PROGRESS, JournalState.VERIFYING, JournalState.INTERRUPTED)


class OperationExecutor(Protocol):
    def execute(self, operation: PlannedOperation) -> ExecutionResult:
        ...


@dataclass(frozen=True)
class MockExecutorConfig:
    outcomes: dict[str, ExecutionOutcome]


class MockOperationExecutor:
    def __init__(self, config: MockExecutorConfig | None = None):
        self.config = config or MockExecutorConfig({})
        self.executed_operation_ids: list[str] = []

    def execute(self, operation: PlannedOperation) -> ExecutionResult:
        self.executed_operation_ids.append(operation.id)
        outcome = self.config.outcomes.get(operation.id, ExecutionOutcome.SUCCEEDED)
        if outcome == ExecutionOutcome.SUCCEEDED:
            return ExecutionResult(operation.id, outcome)
        if outcome == ExecutionOutcome.INTERRUPTED:
            error = StructuredError(ErrorCode.INTERRUPTED, Severity.RECOVERABLE, "Mock operation interrupted.", {"operation_id": operation.id})
        elif outcome == ExecutionOutcome.VERIFICATION_FAILED:
            error = StructuredError(ErrorCode.UNKNOWN_IO_ERROR, Severity.RECOVERABLE, "Mock verification failed.", {"operation_id": operation.id})
        else:
            error = StructuredError(ErrorCode.UNKNOWN_IO_ERROR, Severity.RECOVERABLE, "Mock operation failed.", {"operation_id": operation.id})
        return ExecutionResult(operation.id, outcome, error)


class JournalExecutionBlocked(RuntimeError):
    def __init__(self, error: StructuredError):
        super().__init__(error.message)
        self.error = error
        self.status = JournalState.RECOVERY_REQUIRED


class JournalCoordinator:
    def __init__(self, database: Database, executor: OperationExecutor):
        self.database = database
        self.executor = executor
        self.state_machine = JournalStateMachine()

    def execute_mock_batch(self, plan: PreviewPlan, stop_after: JournalState | None = None) -> str:
        self._raise_if_plan_has_unresolved_work(plan.id)
        batch_id = str(uuid.uuid4())
        approved_at = datetime.now(timezone.utc).isoformat()
        with self.database.connection:
            self.database.connection.execute(
                """
                INSERT INTO operation_batch(
                    id, plan_id, profile_id, status, approved_at, app_version, summary_json, recovery_required
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 0)
                """,
                (batch_id, plan.id, plan.profile_id, JournalState.APPROVED.value, approved_at, APP_VERSION, "{}"),
            )
        self._mark_batch_started(batch_id)
        for operation in plan.operations:
            completed = self._execute_one(batch_id, operation, stop_after=stop_after)
            self._refresh_batch_status(batch_id)
            if not completed:
                return batch_id
        return batch_id

    def execute_real_move_batch(self, plan: PreviewPlan, stop_after: JournalState | None = None) -> str:
        self._raise_if_plan_has_unresolved_work(plan.id)
        if not isinstance(self.executor, SameVolumeMoveExecutor):
            raise TypeError("execute_real_move_batch requires SameVolumeMoveExecutor.")
        batch_id = str(uuid.uuid4())
        approved_at = datetime.now(timezone.utc).isoformat()
        with self.database.connection:
            self.database.connection.execute(
                """
                INSERT INTO operation_batch(
                    id, plan_id, profile_id, status, approved_at, app_version, summary_json, recovery_required
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 0)
                """,
                (batch_id, plan.id, plan.profile_id, JournalState.APPROVED.value, approved_at, APP_VERSION, "{}"),
            )
        self._mark_batch_started(batch_id)
        for operation in plan.operations:
            completed = self._execute_real_move_one(batch_id, plan, operation, stop_after=stop_after)
            self._refresh_batch_status(batch_id)
            if not completed:
                return batch_id
            row = self.database.connection.execute(
                "SELECT result FROM executed_operation WHERE batch_id = ? AND planned_operation_id = ? ORDER BY started_at DESC LIMIT 1",
                (batch_id, operation.id),
            ).fetchone()
            if row is not None and row["result"] in (JournalState.INTERRUPTED.value, JournalState.RECOVERY_REQUIRED.value):
                return batch_id
        return batch_id

    def _execute_one(self, batch_id: str, operation: PlannedOperation, stop_after: JournalState | None = None) -> bool:
        started_at = datetime.now(timezone.utc).isoformat()
        if operation.safety_status != PlannedOperationStatus.PLANNED:
            self._record_result(batch_id, operation, JournalState.BLOCKED, operation.structured_error, started_at)
            return True

        state = self.state_machine.transition(JournalState.APPROVED, JournalState.INTENT_RECORDED)
        with self.database.connection:
            executed_id = str(uuid.uuid4())
            self.database.connection.execute(
                """
                INSERT INTO executed_operation(
                    id, batch_id, planned_operation_id, operation_type, source_before, destination,
                    result, error_code, error_detail, metadata_before_json, metadata_after_json,
                    identity_before_json, identity_after_json, undo_eligible, undo_status, started_at, completed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    executed_id,
                    batch_id,
                    operation.id,
                    operation.operation_type.value,
                    operation.source_path,
                    operation.destination_path,
                    state.value,
                    None,
                    None,
                    "{}",
                    None,
                    "{}",
                    None,
                    0,
                    "UNDO_BLOCKED",
                    started_at,
                    None,
                ),
            )
            self._record_state_event(executed_id, batch_id, operation.id, state)
        if stop_after == state:
            return False
        state = self.state_machine.transition(state, JournalState.IN_PROGRESS)
        self._update_execution(executed_id, state, None)
        if stop_after == state:
            return False
        result = self.executor.execute(operation)
        if result.outcome == ExecutionOutcome.INTERRUPTED:
            state = self.state_machine.transition(state, JournalState.INTERRUPTED)
            self._update_execution(executed_id, state, result.error)
            return True

        state = self.state_machine.transition(state, JournalState.VERIFYING)
        self._update_execution(executed_id, state, None)
        if stop_after == state:
            return False
        if result.outcome == ExecutionOutcome.SUCCEEDED:
            state = self.state_machine.transition(state, JournalState.SUCCEEDED)
            self._update_execution(executed_id, state, None)
        else:
            state = self.state_machine.transition(state, JournalState.FAILED)
            self._update_execution(executed_id, state, result.error)
        return True

    def _execute_real_move_one(self, batch_id: str, plan: PreviewPlan, operation: PlannedOperation, stop_after: JournalState | None = None) -> bool:
        started_at = datetime.now(timezone.utc).isoformat()
        executor: SameVolumeMoveExecutor = self.executor
        preflight_result = executor.prepare(plan, operation)
        if not preflight_result.allowed or preflight_result.preflight is None:
            self._record_result(batch_id, operation, JournalState.BLOCKED, preflight_result.error, started_at)
            return True

        preflight = preflight_result.preflight
        state = self.state_machine.transition(JournalState.APPROVED, JournalState.INTENT_RECORDED)
        with self.database.connection:
            executed_id = str(uuid.uuid4())
            self.database.connection.execute(
                """
                INSERT INTO executed_operation(
                    id, batch_id, planned_operation_id, operation_type, source_before, destination,
                    result, error_code, error_detail, metadata_before_json, metadata_after_json,
                    identity_before_json, identity_after_json, undo_eligible, undo_status, started_at, completed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    executed_id,
                    batch_id,
                    operation.id,
                    operation.operation_type.value,
                    operation.source_path,
                    operation.destination_path,
                    state.value,
                    None,
                    None,
                    json.dumps(dataclass_to_jsonable(preflight.source_identity_before.metadata), sort_keys=True),
                    None,
                    json.dumps(dataclass_to_jsonable(preflight.source_identity_before), sort_keys=True),
                    None,
                    0,
                    "UNDO_BLOCKED",
                    started_at,
                    None,
                ),
            )
            self._record_state_event(executed_id, batch_id, operation.id, state)
        if stop_after == state:
            return False

        state = self.state_machine.transition(state, JournalState.IN_PROGRESS)
        self._update_execution(executed_id, state, None)
        if stop_after == state:
            return False

        move_result = executor.move(preflight)
        if move_result.outcome == ExecutionOutcome.INTERRUPTED:
            state = self.state_machine.transition(state, JournalState.INTERRUPTED)
            self._update_execution(executed_id, state, move_result.error)
            return True
        if move_result.outcome != ExecutionOutcome.SUCCEEDED:
            state = self.state_machine.transition(state, JournalState.FAILED)
            self._update_execution(executed_id, state, move_result.error)
            return True

        state = self.state_machine.transition(state, JournalState.VERIFYING)
        self._update_execution(executed_id, state, None)
        if stop_after == state:
            return False

        verification = executor.verify(preflight)
        if verification.outcome == ExecutionOutcome.SUCCEEDED:
            identity_after = executor.identity_provider.snapshot(operation.destination_path)
            state = self.state_machine.transition(state, JournalState.SUCCEEDED)
            self._update_execution(executed_id, state, None, identity_after.snapshot if identity_after.supported else None)
        else:
            state = self.state_machine.transition(state, JournalState.FAILED)
            self._update_execution(executed_id, state, verification.error)
        return True

    def _record_result(
        self,
        batch_id: str,
        operation: PlannedOperation,
        state: JournalState,
        error: StructuredError | None,
        started_at: str,
    ) -> None:
        with self.database.connection:
            executed_id = str(uuid.uuid4())
            self.database.connection.execute(
                """
                INSERT INTO executed_operation(
                    id, batch_id, planned_operation_id, operation_type, source_before, destination,
                    result, error_code, error_detail, metadata_before_json, metadata_after_json,
                    identity_before_json, identity_after_json, undo_eligible, undo_status, started_at, completed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    executed_id,
                    batch_id,
                    operation.id,
                    operation.operation_type.value,
                    operation.source_path,
                    operation.destination_path,
                    state.value,
                    error.code.value if error else None,
                    error.message if error else None,
                    "{}",
                    None,
                    "{}",
                    None,
                    0,
                    "UNDO_BLOCKED",
                    started_at,
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            self._record_state_event(executed_id, batch_id, operation.id, state)

    def _update_execution(self, executed_id: str, state: JournalState, error: StructuredError | None, identity_after=None) -> None:
        completed_at = None if state in self.state_machine.recoverable_states() else datetime.now(timezone.utc).isoformat()
        identity_after_json = json.dumps(dataclass_to_jsonable(identity_after), sort_keys=True) if identity_after is not None else None
        with self.database.connection:
            self.database.connection.execute(
                """
                UPDATE executed_operation
                SET result = ?, error_code = ?, error_detail = ?, completed_at = ?, identity_after_json = ?
                WHERE id = ?
                """,
                (
                    state.value,
                    error.code.value if error else None,
                    error.message if error else None,
                    completed_at,
                    identity_after_json,
                    executed_id,
                ),
            )
            row = self.database.connection.execute(
                "SELECT batch_id, planned_operation_id FROM executed_operation WHERE id = ?",
                (executed_id,),
            ).fetchone()
            if row is not None:
                self._record_state_event(executed_id, row["batch_id"], row["planned_operation_id"], state)

    def mark_recovery_required(self) -> int:
        recoverable = tuple(state.value for state in self.state_machine.recoverable_states())
        placeholders = ",".join("?" for _ in recoverable)
        with self.database.connection:
            cursor = self.database.connection.execute(
                f"UPDATE executed_operation SET result = ? WHERE result IN ({placeholders})",
                (JournalState.RECOVERY_REQUIRED.value, *recoverable),
            )
            for row in self.database.connection.execute(
                "SELECT id, batch_id, planned_operation_id FROM executed_operation WHERE result = ?",
                (JournalState.RECOVERY_REQUIRED.value,),
            ):
                self._record_state_event(row["id"], row["batch_id"], row["planned_operation_id"], JournalState.RECOVERY_REQUIRED)
            self.database.connection.execute(
                "UPDATE operation_batch SET status = ?, recovery_required = 1 WHERE id IN (SELECT DISTINCT batch_id FROM executed_operation WHERE result = ?)",
                (JournalState.RECOVERY_REQUIRED.value, JournalState.RECOVERY_REQUIRED.value),
            )
        return int(cursor.rowcount)

    def operations_requiring_recovery(self) -> tuple[sqlite3.Row, ...]:
        return tuple(
            self.database.connection.execute(
                """
                SELECT * FROM executed_operation
                WHERE result IN (?, ?, ?, ?, ?)
                ORDER BY started_at, planned_operation_id
                """,
                (
                    JournalState.INTENT_RECORDED.value,
                    JournalState.IN_PROGRESS.value,
                    JournalState.VERIFYING.value,
                    JournalState.INTERRUPTED.value,
                    JournalState.RECOVERY_REQUIRED.value,
                ),
            )
        )

    def operation_state_history(self, operation_id: str | None = None, *, batch_id: str | None = None, executed_operation_id: str | None = None) -> tuple[str, ...]:
        filters: list[str] = []
        params: list[str] = []
        if operation_id is not None:
            filters.append("planned_operation_id = ?")
            params.append(operation_id)
        if batch_id is not None:
            filters.append("batch_id = ?")
            params.append(batch_id)
        if executed_operation_id is not None:
            filters.append("executed_operation_id = ?")
            params.append(executed_operation_id)
        where = " AND ".join(filters) if filters else "1 = 1"
        return tuple(
            row["state"]
            for row in self.database.connection.execute(
                f"SELECT state FROM operation_state_event WHERE {where} ORDER BY created_at, rowid",
                tuple(params),
            )
        )

    def _record_state_event(self, executed_id: str, batch_id: str, operation_id: str, state: JournalState) -> None:
        self.database.connection.execute(
            """
            INSERT INTO operation_state_event(id, executed_operation_id, batch_id, planned_operation_id, state, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (str(uuid.uuid4()), executed_id, batch_id, operation_id, state.value, datetime.now(timezone.utc).isoformat()),
        )

    def _refresh_batch_status(self, batch_id: str) -> None:
        rows = tuple(
            self.database.connection.execute(
                "SELECT result FROM executed_operation WHERE batch_id = ?",
                (batch_id,),
            )
        )
        if not rows:
            return
        states = {row["result"] for row in rows}
        if JournalState.RECOVERY_REQUIRED.value in states:
            status = JournalState.RECOVERY_REQUIRED.value
            recovery_required = 1
            completed_at = None
        elif JournalState.INTERRUPTED.value in states:
            status = JournalState.INTERRUPTED.value
            recovery_required = 0
            completed_at = None
        elif any(state in states for state in (JournalState.INTENT_RECORDED.value, JournalState.IN_PROGRESS.value, JournalState.VERIFYING.value)):
            status = JournalState.IN_PROGRESS.value
            recovery_required = 0
            completed_at = None
        elif states == {JournalState.SUCCEEDED.value}:
            status = JournalState.SUCCEEDED.value
            recovery_required = 0
            completed_at = datetime.now(timezone.utc).isoformat()
        elif states == {JournalState.BLOCKED.value}:
            status = JournalState.BLOCKED.value
            recovery_required = 0
            completed_at = datetime.now(timezone.utc).isoformat()
        elif states == {JournalState.FAILED.value}:
            status = JournalState.FAILED.value
            recovery_required = 0
            completed_at = datetime.now(timezone.utc).isoformat()
        else:
            status = "PARTIAL_FAILURE"
            recovery_required = 0
            completed_at = datetime.now(timezone.utc).isoformat()
        with self.database.connection:
            self.database.connection.execute(
                "UPDATE operation_batch SET status = ?, completed_at = ?, recovery_required = ? WHERE id = ?",
                (status, completed_at, recovery_required, batch_id),
            )

    def _mark_batch_started(self, batch_id: str) -> None:
        with self.database.connection:
            self.database.connection.execute(
                "UPDATE operation_batch SET status = ?, started_at = ? WHERE id = ?",
                (JournalState.IN_PROGRESS.value, datetime.now(timezone.utc).isoformat(), batch_id),
            )

    def _raise_if_plan_has_unresolved_work(self, plan_id: str) -> None:
        unresolved = self.operations_requiring_recovery_for_plan(plan_id)
        if unresolved:
            raise JournalExecutionBlocked(
                StructuredError(
                    ErrorCode.RECOVERY_REQUIRED,
                    Severity.RECOVERY,
                    "This plan has unresolved journal work and must be recovered before another execution starts.",
                    {"plan_id": plan_id, "unresolved_count": len(unresolved)},
                )
            )

    def operations_requiring_recovery_for_plan(self, plan_id: str) -> tuple[sqlite3.Row, ...]:
        states = (
            JournalState.INTENT_RECORDED.value,
            JournalState.IN_PROGRESS.value,
            JournalState.VERIFYING.value,
            JournalState.INTERRUPTED.value,
            JournalState.RECOVERY_REQUIRED.value,
        )
        placeholders = ",".join("?" for _ in states)
        return tuple(
            self.database.connection.execute(
                f"""
                SELECT executed_operation.*
                FROM executed_operation
                JOIN operation_batch ON operation_batch.id = executed_operation.batch_id
                WHERE operation_batch.plan_id = ?
                  AND executed_operation.result IN ({placeholders})
                ORDER BY executed_operation.started_at, executed_operation.id
                """,
                (plan_id, *states),
            )
        )

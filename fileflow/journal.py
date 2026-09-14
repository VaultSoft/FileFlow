from __future__ import annotations

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
)
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


class JournalCoordinator:
    def __init__(self, database: Database, executor: OperationExecutor):
        self.database = database
        self.executor = executor
        self.state_machine = JournalStateMachine()

    def execute_mock_batch(self, plan: PreviewPlan) -> str:
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
        for operation in plan.operations:
            self._execute_one(batch_id, operation)
            self._refresh_batch_status(batch_id)
        return batch_id

    def _execute_one(self, batch_id: str, operation: PlannedOperation) -> None:
        started_at = datetime.now(timezone.utc).isoformat()
        if operation.safety_status != PlannedOperationStatus.PLANNED:
            self._record_result(batch_id, operation, JournalState.BLOCKED, operation.structured_error, started_at)
            return

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
            self._record_state_event(batch_id, operation.id, state)
        state = self.state_machine.transition(state, JournalState.IN_PROGRESS)
        self._update_latest(operation.id, state, None)
        result = self.executor.execute(operation)
        if result.outcome == ExecutionOutcome.SUCCEEDED:
            state = self.state_machine.transition(state, JournalState.VERIFYING)
            self._update_latest(operation.id, state, None)
            state = self.state_machine.transition(state, JournalState.SUCCEEDED)
            self._update_latest(operation.id, state, None)
        elif result.outcome == ExecutionOutcome.INTERRUPTED:
            state = self.state_machine.transition(state, JournalState.INTERRUPTED)
            self._update_latest(operation.id, state, result.error)
        else:
            state = self.state_machine.transition(state, JournalState.FAILED)
            self._update_latest(operation.id, state, result.error)

    def _record_result(
        self,
        batch_id: str,
        operation: PlannedOperation,
        state: JournalState,
        error: StructuredError | None,
        started_at: str,
    ) -> None:
        with self.database.connection:
            self.database.connection.execute(
                """
                INSERT INTO executed_operation(
                    id, batch_id, planned_operation_id, operation_type, source_before, destination,
                    result, error_code, error_detail, metadata_before_json, metadata_after_json,
                    identity_before_json, identity_after_json, undo_eligible, undo_status, started_at, completed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(uuid.uuid4()),
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
            self._record_state_event(batch_id, operation.id, state)

    def _update_latest(self, operation_id: str, state: JournalState, error: StructuredError | None) -> None:
        with self.database.connection:
            self.database.connection.execute(
                """
                UPDATE executed_operation
                SET result = ?, error_code = ?, error_detail = ?, completed_at = ?
                WHERE planned_operation_id = ?
                """,
                (
                    state.value,
                    error.code.value if error else None,
                    error.message if error else None,
                    datetime.now(timezone.utc).isoformat(),
                    operation_id,
                ),
            )
            row = self.database.connection.execute(
                "SELECT batch_id FROM executed_operation WHERE planned_operation_id = ?",
                (operation_id,),
            ).fetchone()
            if row is not None:
                self._record_state_event(row["batch_id"], operation_id, state)

    def mark_recovery_required(self) -> int:
        recoverable = tuple(state.value for state in self.state_machine.recoverable_states())
        placeholders = ",".join("?" for _ in recoverable)
        with self.database.connection:
            cursor = self.database.connection.execute(
                f"UPDATE executed_operation SET result = ? WHERE result IN ({placeholders})",
                (JournalState.RECOVERY_REQUIRED.value, *recoverable),
            )
            for row in self.database.connection.execute(
                "SELECT batch_id, planned_operation_id FROM executed_operation WHERE result = ?",
                (JournalState.RECOVERY_REQUIRED.value,),
            ):
                self._record_state_event(row["batch_id"], row["planned_operation_id"], JournalState.RECOVERY_REQUIRED)
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
                WHERE result IN (?, ?, ?, ?)
                ORDER BY started_at, planned_operation_id
                """,
                (
                    JournalState.INTENT_RECORDED.value,
                    JournalState.IN_PROGRESS.value,
                    JournalState.VERIFYING.value,
                    JournalState.RECOVERY_REQUIRED.value,
                ),
            )
        )

    def operation_state_history(self, operation_id: str) -> tuple[str, ...]:
        return tuple(
            row["state"]
            for row in self.database.connection.execute(
                "SELECT state FROM operation_state_event WHERE planned_operation_id = ? ORDER BY created_at, rowid",
                (operation_id,),
            )
        )

    def _record_state_event(self, batch_id: str, operation_id: str, state: JournalState) -> None:
        self.database.connection.execute(
            """
            INSERT INTO operation_state_event(id, batch_id, planned_operation_id, state, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (str(uuid.uuid4()), batch_id, operation_id, state.value, datetime.now(timezone.utc).isoformat()),
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
        elif JournalState.INTERRUPTED.value in states:
            status = JournalState.INTERRUPTED.value
            recovery_required = 0
        elif states == {JournalState.SUCCEEDED.value}:
            status = JournalState.SUCCEEDED.value
            recovery_required = 0
        elif states == {JournalState.BLOCKED.value}:
            status = JournalState.BLOCKED.value
            recovery_required = 0
        elif states == {JournalState.FAILED.value}:
            status = JournalState.FAILED.value
            recovery_required = 0
        else:
            status = "PARTIAL_FAILURE"
            recovery_required = 0
        with self.database.connection:
            self.database.connection.execute(
                "UPDATE operation_batch SET status = ?, completed_at = ?, recovery_required = ? WHERE id = ?",
                (status, datetime.now(timezone.utc).isoformat(), recovery_required, batch_id),
            )

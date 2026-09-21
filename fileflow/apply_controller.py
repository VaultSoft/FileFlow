from __future__ import annotations

import json
from dataclasses import dataclass
from enum import Enum
from typing import Callable

from .journal import ExecutionLockState, JournalCoordinator, JournalExecutionBlocked
from .models import (
    ErrorCode,
    JournalState,
    OperationIntent,
    PlannedOperation,
    PlannedOperationStatus,
    PreviewPlan,
    RevalidationStatus,
    Severity,
    StructuredError,
)
from .operations.same_volume_move import SameVolumeMoveExecutor
from .process_identity import ProcessIdentityProbe
from .preview_workflow import PreviewWorkflowService
from .storage import Database, PlanRepository


MAX_APPLY_OPERATIONS = 100


class ApplyState(str, Enum):
    NO_PREVIEW = "NO_PREVIEW"
    PREVIEW_VALID = "PREVIEW_VALID"
    PREVIEW_STALE = "PREVIEW_STALE"
    PREVIEW_BLOCKED = "PREVIEW_BLOCKED"
    CONFIRMING = "CONFIRMING"
    APPLYING = "APPLYING"
    COMPLETE = "COMPLETE"
    RECOVERY_REQUIRED = "RECOVERY_REQUIRED"


@dataclass(frozen=True)
class ApplyReadiness:
    state: ApplyState
    can_apply: bool
    message: str
    ready_count: int = 0
    blocked_count: int = 0
    unsupported_count: int = 0
    total_bytes: int = 0


@dataclass(frozen=True)
class ApplyConfirmationSummary:
    operation_count: int
    total_bytes: int
    source_folder: str
    category_summary: tuple[tuple[str, int], ...]
    blocked_count: int
    unsupported_count: int
    non_actionable_count: int
    message: str


@dataclass(frozen=True)
class ApplyResult:
    state: ApplyState
    batch_id: str | None
    message: str
    attempted: int = 0
    succeeded: int = 0
    failed: int = 0
    blocked: int = 0
    recovery_required: int = 0


class ApplyController:
    def __init__(
        self,
        database: Database,
        *,
        preview_service: PreviewWorkflowService | None = None,
        executor_factory: Callable[[], SameVolumeMoveExecutor] | None = None,
        process_probe: ProcessIdentityProbe | None = None,
    ):
        self.database = database
        self.preview_service = preview_service or PreviewWorkflowService()
        self.executor_factory = executor_factory or SameVolumeMoveExecutor
        self.process_probe = process_probe
        self._active = False
        self._consumed_plan_ids: set[str] = set()

    def readiness(self, plan: PreviewPlan | None, *, already_applying: bool = False) -> ApplyReadiness:
        coordinator = self._coordinator()
        lock_assessment = coordinator.reconcile_execution_lock()
        if lock_assessment.blocks_apply:
            state = ApplyState.APPLYING if lock_assessment.state == ExecutionLockState.ACTIVE else ApplyState.RECOVERY_REQUIRED
            return ApplyReadiness(state, False, lock_assessment.message)
        if plan is None:
            return ApplyReadiness(ApplyState.NO_PREVIEW, False, "Analyse a folder before applying.")
        if already_applying or self._active:
            return ApplyReadiness(ApplyState.APPLYING, False, "FileFlow is already moving files.")
        if plan.id in self._consumed_plan_ids:
            return ApplyReadiness(ApplyState.PREVIEW_STALE, False, "Analyse again before moving more files.")
        unresolved = coordinator.all_operations_requiring_recovery()
        if unresolved:
            return ApplyReadiness(
                ApplyState.RECOVERY_REQUIRED,
                False,
                "A previous FileFlow operation needs recovery review before new files can be moved.",
            )

        operations = actionable_operations(plan)
        blocked_count = sum(1 for operation in plan.operations if operation.safety_status == PlannedOperationStatus.BLOCKED)
        unsupported_count = sum(1 for operation in plan.operations if operation.safety_status == PlannedOperationStatus.UNSUPPORTED)
        total_bytes = sum(operation.source_identity.metadata.size for operation in operations if operation.source_identity is not None)
        if not operations:
            return ApplyReadiness(
                ApplyState.PREVIEW_BLOCKED,
                False,
                "This preview has no ready supported file moves.",
                0,
                blocked_count,
                unsupported_count,
                total_bytes,
            )
        if len(operations) > MAX_APPLY_OPERATIONS:
            return ApplyReadiness(
                ApplyState.PREVIEW_BLOCKED,
                False,
                f"This preview has {len(operations)} ready moves. The current development limit is {MAX_APPLY_OPERATIONS}.",
                len(operations),
                blocked_count,
                unsupported_count,
                total_bytes,
            )
        return ApplyReadiness(
            ApplyState.PREVIEW_VALID,
            True,
            "Ready to move files after validation and confirmation.",
            len(operations),
            blocked_count,
            unsupported_count,
            total_bytes,
        )

    def confirmation_summary(self, plan: PreviewPlan) -> ApplyConfirmationSummary:
        operations = actionable_operations(plan)
        categories: dict[str, int] = {}
        total_bytes = 0
        for operation in operations:
            category = operation.category_snapshot.name if operation.category_snapshot else "Uncategorised"
            categories[category] = categories.get(category, 0) + 1
            if operation.source_identity is not None:
                total_bytes += operation.source_identity.metadata.size
        non_actionable = len(plan.operations) - len(operations)
        blocked_count = sum(1 for operation in plan.operations if operation.safety_status == PlannedOperationStatus.BLOCKED)
        unsupported_count = sum(1 for operation in plan.operations if operation.safety_status == PlannedOperationStatus.UNSUPPORTED)
        return ApplyConfirmationSummary(
            len(operations),
            total_bytes,
            plan.source_root,
            tuple(sorted(categories.items())),
            blocked_count,
            unsupported_count,
            non_actionable,
            "FileFlow will move only the ready files to the exact destinations shown in Preview. Blocked preview rows will not be moved, and existing destinations will not be overwritten.",
        )

    def validate_before_confirmation(self, plan: PreviewPlan) -> ApplyReadiness:
        readiness = self.readiness(plan)
        if not readiness.can_apply:
            return readiness
        executable_plan = _plan_with_operations(plan, actionable_operations(plan))
        result = self.preview_service.revalidate_plan(executable_plan)
        if result.status == RevalidationStatus.STALE:
            return ApplyReadiness(ApplyState.PREVIEW_STALE, False, "The folder changed after this preview was created. Analyse again before moving files.")
        if result.status == RevalidationStatus.BLOCKED:
            message = result.errors[0].message if result.errors else "Preview is blocked by a safety check."
            return ApplyReadiness(ApplyState.PREVIEW_BLOCKED, False, message)
        return readiness

    def apply_confirmed(
        self,
        plan: PreviewPlan,
        *,
        progress_callback: Callable[[str, int, int], None] | None = None,
    ) -> ApplyResult:
        readiness = self.validate_before_confirmation(plan)
        if not readiness.can_apply:
            return ApplyResult(readiness.state, None, readiness.message)
        operations = actionable_operations(plan)
        executable_plan = _plan_with_operations(plan, operations)
        self._active = True
        try:
            plans = PlanRepository(self.database)
            persisted = plans.load_plan(plan.id)
            if persisted is None:
                plans.save_plan(plan)
            elif persisted != plan:
                return ApplyResult(
                    ApplyState.PREVIEW_BLOCKED,
                    None,
                    "Stored preview data does not match the plan selected for Apply.",
                )
            coordinator = self._coordinator()
            batch_id = coordinator.execute_real_move_batch(executable_plan, progress_callback=progress_callback)
            summary = coordinator.batch_summary(batch_id)
            self._consumed_plan_ids.add(plan.id)
            if summary is None:
                return ApplyResult(ApplyState.COMPLETE, batch_id, "Move batch finished, but summary could not be loaded.")
            result = _result_from_summary(summary, excluded_blocked=readiness.blocked_count)
            if result.recovery_required:
                return ApplyResult(
                    ApplyState.RECOVERY_REQUIRED,
                    batch_id,
                    "A previous move could not be fully verified. FileFlow has stopped to protect your files.",
                    result.attempted,
                    result.succeeded,
                    result.failed,
                    result.blocked,
                    result.recovery_required,
                )
            return result
        except JournalExecutionBlocked as exc:
            return ApplyResult(ApplyState.RECOVERY_REQUIRED, None, exc.error.message)
        finally:
            self._active = False

    def confirm_and_apply(
        self,
        plan: PreviewPlan,
        confirm: Callable[[ApplyConfirmationSummary], bool],
        *,
        progress_callback: Callable[[str, int, int], None] | None = None,
    ) -> ApplyResult:
        readiness = self.validate_before_confirmation(plan)
        if not readiness.can_apply:
            return ApplyResult(readiness.state, None, readiness.message)
        if not confirm(self.confirmation_summary(plan)):
            return ApplyResult(ApplyState.PREVIEW_VALID, None, "Move cancelled. No files were changed.")
        return self.apply_confirmed(plan, progress_callback=progress_callback)

    def history_rows(self):
        return self._coordinator().list_batches()

    def history_operations(self, batch_id: str):
        return self._coordinator().list_batch_operations(batch_id)

    def operations_requiring_recovery(self):
        return self._coordinator().operations_requiring_recovery()

    def execution_lock_status(self):
        return self._coordinator().reconcile_execution_lock()

    def _coordinator(self) -> JournalCoordinator:
        return JournalCoordinator(
            self.database,
            self.executor_factory(),
            process_probe=self.process_probe,
        )


def actionable_operations(plan: PreviewPlan) -> tuple[PlannedOperation, ...]:
    return tuple(
        operation
        for operation in plan.operations
        if operation.operation_type == OperationIntent.MOVE
        and operation.safety_status == PlannedOperationStatus.PLANNED
        and operation.destination_path is not None
        and operation.structured_error is None
    )


def _plan_with_operations(plan: PreviewPlan, operations: tuple[PlannedOperation, ...]) -> PreviewPlan:
    from dataclasses import replace

    return replace(plan, operations=operations)


def _result_from_summary(summary, *, excluded_blocked: int = 0) -> ApplyResult:
    attempted = int(summary["attempted_count"] or 0)
    succeeded = int(summary["succeeded_count"] or 0)
    failed = int(summary["failed_count"] or 0)
    blocked = int(summary["blocked_count"] or 0) + excluded_blocked
    recovery = int(summary["recovery_count"] or 0)
    status = summary["status"]
    state = ApplyState.RECOVERY_REQUIRED if status in (JournalState.RECOVERY_REQUIRED.value, JournalState.INTERRUPTED.value) else ApplyState.COMPLETE
    return ApplyResult(
        state,
        summary["id"],
        f"Moved {succeeded} of {attempted} attempted files. Failed safely: {failed}. Blocked: {blocked}.",
        attempted,
        succeeded,
        failed,
        blocked,
        recovery,
    )


def batch_source_folder(row) -> str:
    try:
        payload = json.loads(row["summary_json"] or "{}")
    except json.JSONDecodeError:
        return ""
    return payload.get("source_root", "")

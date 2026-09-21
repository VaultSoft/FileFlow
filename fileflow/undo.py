from __future__ import annotations

import json
import ntpath
import sqlite3
import uuid
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Callable

from .app_metadata import APP_VERSION
from .journal import JournalCoordinator, JournalExecutionBlocked
from .models import (
    ErrorCode,
    ExecutionOutcome,
    FileIdentity,
    IdentitySnapshot,
    JournalState,
    MetadataSnapshot,
    OperationIntent,
    Severity,
    StructuredError,
    dataclass_to_jsonable,
)
from .operations.same_volume_move import (
    DestinationOccupancyInspector,
    OccupancyStatus,
    SameVolumeRenamePrimitive,
)
from .process_identity import ProcessIdentityProbe
from .safety import (
    CloudClassifier,
    ConservativeCloudClassifier,
    FileIdentityProvider,
    IdentityResult,
    PathChainSafety,
    WindowsFileIdentityProvider,
    WindowsPathPolicy,
    WindowsReparseInspector,
)
from .storage import Database, PlanRepository


MAX_UNDO_OPERATIONS = 100
ACTIVE_UNDO_STATES = (
    JournalState.INTENT_RECORDED.value,
    JournalState.IN_PROGRESS.value,
    JournalState.VERIFYING.value,
    JournalState.INTERRUPTED.value,
    JournalState.RECOVERY_REQUIRED.value,
)


class UndoOperationStatus(str, Enum):
    READY = "READY"
    BLOCKED = "BLOCKED"
    STALE = "STALE"


class UndoPlanStatus(str, Enum):
    READY = "READY"
    BLOCKED = "BLOCKED"
    STALE = "STALE"


class UndoControllerState(str, Enum):
    NO_PREVIEW = "NO_PREVIEW"
    PREVIEW_READY = "PREVIEW_READY"
    PREVIEW_BLOCKED = "PREVIEW_BLOCKED"
    PREVIEW_STALE = "PREVIEW_STALE"
    UNDOING = "UNDOING"
    COMPLETE = "COMPLETE"
    RECOVERY_REQUIRED = "RECOVERY_REQUIRED"


@dataclass(frozen=True)
class UndoPlannedOperation:
    id: str
    undo_plan_id: str
    original_plan_id: str
    original_batch_id: str
    original_planned_operation_id: str
    original_execution_id: str
    source_path: str
    restore_path: str
    source_root: str
    restore_root: str
    expected_identity: IdentitySnapshot | None
    source_root_identity: IdentitySnapshot
    restore_root_identity: IdentitySnapshot
    preview_source_identity: IdentitySnapshot | None
    source_parent_identity: IdentitySnapshot | None
    restore_parent_identity: IdentitySnapshot | None
    status: UndoOperationStatus
    reason: StructuredError | None
    metadata_changed: bool
    preview_index: int


@dataclass(frozen=True)
class UndoPlan:
    id: str
    original_batch_id: str
    original_plan_id: str
    status: UndoPlanStatus
    safety_policy_version: int
    operations: tuple[UndoPlannedOperation, ...]
    excluded_apply_count: int
    created_at: str

    @property
    def ready_operations(self) -> tuple[UndoPlannedOperation, ...]:
        return tuple(operation for operation in self.operations if operation.status == UndoOperationStatus.READY)


@dataclass(frozen=True)
class UndoReadiness:
    state: UndoControllerState
    can_undo: bool
    message: str
    ready_count: int = 0
    blocked_count: int = 0
    excluded_apply_count: int = 0


@dataclass(frozen=True)
class UndoConfirmationSummary:
    operation_count: int
    blocked_count: int
    excluded_apply_count: int
    edited_count: int
    message: str


@dataclass(frozen=True)
class UndoResult:
    state: UndoControllerState
    batch_id: str | None
    message: str
    attempted: int = 0
    succeeded: int = 0
    failed: int = 0
    recovery_required: int = 0


@dataclass(frozen=True)
class UndoPreflight:
    plan: UndoPlan
    operation: UndoPlannedOperation
    source_identity: IdentitySnapshot
    source_parent_identity: IdentitySnapshot
    restore_parent_identity: IdentitySnapshot


@dataclass(frozen=True)
class UndoPreflightResult:
    preflight: UndoPreflight | None
    error: StructuredError | None = None

    @property
    def allowed(self) -> bool:
        return self.preflight is not None and self.error is None


@dataclass(frozen=True)
class UndoValidationResult:
    status: UndoPlanStatus
    preflights: tuple[UndoPreflight, ...] = ()
    errors: tuple[StructuredError, ...] = ()

    @property
    def valid(self) -> bool:
        return self.status == UndoPlanStatus.READY and not self.errors


@dataclass(frozen=True)
class UndoVerification:
    outcome: ExecutionOutcome
    identity_after: IdentitySnapshot | None = None
    error: StructuredError | None = None


class UndoPlanRejected(RuntimeError):
    def __init__(self, result: UndoValidationResult):
        self.result = result
        message = result.errors[0].message if result.errors else "Undo plan is no longer valid."
        super().__init__(message)


class UndoRepository:
    def __init__(self, database: Database):
        self.database = database

    def save_plan(self, plan: UndoPlan) -> None:
        summary = {
            "candidate_count": len(plan.operations),
            "ready_count": len(plan.ready_operations),
            "blocked_count": sum(1 for operation in plan.operations if operation.status != UndoOperationStatus.READY),
            "excluded_apply_count": plan.excluded_apply_count,
        }
        behavior = {
            "location_only": True,
            "same_volume_only": True,
            "overwrite": False,
            "auto_rename": False,
            "operation_cap": MAX_UNDO_OPERATIONS,
        }
        snapshot_json = json.dumps(dataclass_to_jsonable(plan), sort_keys=True)
        with self.database.connection:
            self.database.connection.execute(
                """
                INSERT INTO undo_plan(
                    id, original_batch_id, original_plan_id, status,
                    safety_policy_version, behavior_snapshot_json, summary_json,
                    snapshot_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    plan.id,
                    plan.original_batch_id,
                    plan.original_plan_id,
                    plan.status.value,
                    plan.safety_policy_version,
                    json.dumps(behavior, sort_keys=True),
                    json.dumps(summary, sort_keys=True),
                    snapshot_json,
                    plan.created_at,
                ),
            )
            for operation in plan.operations:
                self.database.connection.execute(
                    """
                    INSERT INTO undo_planned_operation(
                        id, undo_plan_id, original_plan_id, original_batch_id,
                        original_planned_operation_id, original_execution_id,
                        source_path, restore_path, expected_identity_json,
                        preview_metadata_json, status, reason_code, reason_detail,
                        snapshot_json, preview_index
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        operation.id,
                        operation.undo_plan_id,
                        operation.original_plan_id,
                        operation.original_batch_id,
                        operation.original_planned_operation_id,
                        operation.original_execution_id,
                        operation.source_path,
                        operation.restore_path,
                        json.dumps(dataclass_to_jsonable(operation.expected_identity), sort_keys=True),
                        json.dumps(dataclass_to_jsonable(operation.preview_source_identity), sort_keys=True),
                        operation.status.value,
                        operation.reason.code.value if operation.reason else None,
                        operation.reason.message if operation.reason else None,
                        json.dumps(dataclass_to_jsonable(operation), sort_keys=True),
                        operation.preview_index,
                    ),
                )

    def load_plan(self, plan_id: str) -> UndoPlan | None:
        row = self.database.connection.execute(
            "SELECT snapshot_json FROM undo_plan WHERE id = ?",
            (plan_id,),
        ).fetchone()
        if row is None:
            return None
        return _undo_plan_from_json(json.loads(row["snapshot_json"]))

    def blocking_undo_state(self, original_execution_id: str) -> str | None:
        row = self.database.connection.execute(
            """
            SELECT state FROM undo_execution
            WHERE original_execution_id = ?
              AND state IN ('SUCCEEDED', 'INTENT_RECORDED', 'IN_PROGRESS', 'VERIFYING', 'INTERRUPTED', 'RECOVERY_REQUIRED')
            ORDER BY started_at DESC LIMIT 1
            """,
            (original_execution_id,),
        ).fetchone()
        return str(row["state"]) if row is not None else None

    def list_batches(self) -> tuple[sqlite3.Row, ...]:
        return tuple(
            self.database.connection.execute(
                """
                SELECT
                    undo_batch.*,
                    COUNT(undo_execution.id) AS attempted_count,
                    SUM(CASE WHEN undo_execution.state = 'SUCCEEDED' THEN 1 ELSE 0 END) AS succeeded_count,
                    SUM(CASE WHEN undo_execution.state = 'FAILED' THEN 1 ELSE 0 END) AS failed_count,
                    SUM(CASE WHEN undo_execution.state IN ('INTERRUPTED', 'RECOVERY_REQUIRED') THEN 1 ELSE 0 END) AS recovery_count
                FROM undo_batch
                LEFT JOIN undo_execution ON undo_execution.undo_batch_id = undo_batch.id
                GROUP BY undo_batch.id
                ORDER BY COALESCE(undo_batch.started_at, undo_batch.approved_at) DESC
                """
            )
        )

    def list_executions(self, undo_batch_id: str) -> tuple[sqlite3.Row, ...]:
        return tuple(
            self.database.connection.execute(
                "SELECT * FROM undo_execution WHERE undo_batch_id = ? ORDER BY started_at, id",
                (undo_batch_id,),
            )
        )


class UndoMoveExecutor:
    def __init__(
        self,
        *,
        path_policy: WindowsPathPolicy | None = None,
        chain_safety: PathChainSafety | None = None,
        identity_provider: FileIdentityProvider | None = None,
        cloud_classifier: CloudClassifier | None = None,
        occupancy: DestinationOccupancyInspector | None = None,
        directory_entries: Callable[[str], tuple[str, ...]] | None = None,
        rename_primitive: Callable[[str, str], None] | None = None,
        before_rename: Callable[[UndoPreflight], None] | None = None,
    ):
        self.path_policy = path_policy or WindowsPathPolicy()
        self.chain_safety = chain_safety or PathChainSafety(self.path_policy, WindowsReparseInspector())
        self.identity_provider = identity_provider or WindowsFileIdentityProvider()
        self.cloud_classifier = cloud_classifier or ConservativeCloudClassifier()
        self.occupancy = occupancy or DestinationOccupancyInspector()
        self.directory_entries = directory_entries or self._filesystem_entries
        self.rename_primitive = rename_primitive or SameVolumeRenamePrimitive()
        self.before_rename = before_rename

    def prepare(self, plan: UndoPlan, operation: UndoPlannedOperation, *, enforce_frozen_parents: bool) -> UndoPreflightResult:
        if operation.expected_identity is None:
            return self._blocked(ErrorCode.UNDO_CONFLICT, "Apply did not record a usable post-move identity.", operation)
        if operation.expected_identity.identity.file_type != "file":
            return self._blocked(ErrorCode.UNSUPPORTED, "Undo supports ordinary files only.", operation)

        source_policy = self.path_policy.classify(operation.source_path, operation.source_root)
        if not source_policy.allowed:
            return UndoPreflightResult(None, source_policy.error)
        restore_policy = self.path_policy.classify(operation.restore_path, operation.restore_root)
        if not restore_policy.allowed:
            return UndoPreflightResult(None, restore_policy.error)

        source_state = self.occupancy.inspect(operation.source_path)
        if source_state.status == OccupancyStatus.FREE:
            return self._blocked(ErrorCode.SOURCE_MISSING, "The file is no longer at FileFlow's destination.", operation)
        if source_state.status == OccupancyStatus.UNKNOWN:
            return UndoPreflightResult(None, source_state.error)

        source_chain = self.chain_safety.classify_chain(operation.source_path, operation.source_root)
        if not source_chain.allowed:
            return UndoPreflightResult(None, source_chain.error)
        restore_parent = ntpath.dirname(operation.restore_path)
        restore_chain = self.chain_safety.classify_chain(restore_parent, operation.restore_root)
        if not restore_chain.allowed:
            return UndoPreflightResult(None, restore_chain.error)

        source_identity = self._snapshot_required(
            operation.source_path,
            ErrorCode.SOURCE_IDENTITY_CHANGED,
            "The file at FileFlow's destination could not be identified.",
        )
        if source_identity.error:
            return UndoPreflightResult(None, source_identity.error)
        if source_identity.snapshot.identity != operation.expected_identity.identity:
            return self._blocked(
                ErrorCode.SOURCE_IDENTITY_CHANGED,
                "The file at FileFlow's destination is not the file FileFlow moved.",
                operation,
            )

        source_root = self._snapshot_required(operation.source_root, ErrorCode.ROOT_IDENTITY_CHANGED, "Undo source root identity could not be verified.")
        if source_root.error:
            return UndoPreflightResult(None, source_root.error)
        if source_root.snapshot.identity != operation.source_root_identity.identity:
            return self._blocked(ErrorCode.ROOT_IDENTITY_CHANGED, "Undo source root identity changed.", operation)

        restore_root = self._snapshot_required(operation.restore_root, ErrorCode.ROOT_IDENTITY_CHANGED, "Restore root identity could not be verified.")
        if restore_root.error:
            return UndoPreflightResult(None, restore_root.error)
        if restore_root.snapshot.identity != operation.restore_root_identity.identity:
            return self._blocked(ErrorCode.ROOT_IDENTITY_CHANGED, "Restore root identity changed.", operation)

        source_parent_path = ntpath.dirname(operation.source_path)
        source_parent = self._snapshot_required(source_parent_path, ErrorCode.DESTINATION_PARENT_CHANGED, "Undo source parent identity could not be verified.")
        if source_parent.error:
            return UndoPreflightResult(None, source_parent.error)
        restore_parent_identity = self._snapshot_required(restore_parent, ErrorCode.DESTINATION_PARENT_CHANGED, "Restore parent identity could not be verified.")
        if restore_parent_identity.error:
            return UndoPreflightResult(None, restore_parent_identity.error)

        if enforce_frozen_parents:
            if operation.source_parent_identity is None or source_parent.snapshot.identity != operation.source_parent_identity.identity:
                return self._blocked(ErrorCode.DESTINATION_PARENT_CHANGED, "Undo source parent changed after preview.", operation)
            if operation.restore_parent_identity is None or restore_parent_identity.snapshot.identity != operation.restore_parent_identity.identity:
                return self._blocked(ErrorCode.DESTINATION_PARENT_CHANGED, "Restore parent changed after preview.", operation)

        restore_state = self.occupancy.inspect(operation.restore_path)
        if not restore_state.free:
            return UndoPreflightResult(
                None,
                restore_state.error
                or StructuredError(
                    ErrorCode.DESTINATION_EXISTS,
                    Severity.OPERATION_BLOCKING,
                    "The original path is occupied or could not be proved free.",
                    {"path": operation.restore_path},
                ),
            )
        collision = self._case_collision_error(operation.restore_path)
        if collision is not None:
            return UndoPreflightResult(None, collision)

        if source_identity.snapshot.identity.volume_id != restore_parent_identity.snapshot.identity.volume_id:
            return self._blocked(ErrorCode.CROSS_VOLUME_FAILURE, "Undo source and restore location are not on the same volume.", operation)

        source_cloud = self.cloud_classifier.classify(operation.source_path)
        if not source_cloud.safe:
            return UndoPreflightResult(None, source_cloud.error)
        restore_cloud = self.cloud_classifier.classify(restore_parent)
        if not restore_cloud.safe:
            return UndoPreflightResult(None, restore_cloud.error)

        return UndoPreflightResult(
            UndoPreflight(
                plan,
                operation,
                source_identity.snapshot,
                source_parent.snapshot,
                restore_parent_identity.snapshot,
            )
        )

    def move(self, preflight: UndoPreflight) -> ExecutionOutcome | tuple[ExecutionOutcome, StructuredError]:
        final = self.prepare(preflight.plan, preflight.operation, enforce_frozen_parents=True)
        if not final.allowed:
            return ExecutionOutcome.FAILED, final.error or _undo_error("Undo final safety check failed.")
        if self.before_rename is not None:
            self.before_rename(preflight)
            final = self.prepare(preflight.plan, preflight.operation, enforce_frozen_parents=True)
            if not final.allowed:
                return ExecutionOutcome.FAILED, final.error or _undo_error("Undo final safety check failed.")
        try:
            self.rename_primitive(preflight.operation.source_path, preflight.operation.restore_path)
        except InterruptedError as exc:
            return ExecutionOutcome.INTERRUPTED, StructuredError(
                ErrorCode.INTERRUPTED,
                Severity.RECOVERY,
                "Undo was interrupted and requires recovery review.",
                {"operation_id": preflight.operation.id, "error": str(exc)},
            )
        except PermissionError as exc:
            return ExecutionOutcome.FAILED, StructuredError(
                ErrorCode.ACCESS_DENIED,
                Severity.RECOVERABLE,
                "Undo was denied by the filesystem before FileFlow could verify a move.",
                {"operation_id": preflight.operation.id, "error": str(exc)},
            )
        except OSError as exc:
            return ExecutionOutcome.FAILED, StructuredError(
                ErrorCode.MOVE_FAILED,
                Severity.RECOVERABLE,
                "The same-volume Undo move failed safely.",
                {"operation_id": preflight.operation.id, "error": str(exc)},
            )
        return ExecutionOutcome.SUCCEEDED

    def verify(self, preflight: UndoPreflight) -> UndoVerification:
        operation = preflight.operation
        source = self.occupancy.inspect(operation.source_path)
        if source.status == OccupancyStatus.UNKNOWN:
            return UndoVerification(ExecutionOutcome.VERIFICATION_FAILED, error=source.error)
        if source.status != OccupancyStatus.FREE:
            return UndoVerification(ExecutionOutcome.VERIFICATION_FAILED, error=_undo_error("Undo source still exists after the move."))
        restored = self.occupancy.inspect(operation.restore_path)
        if restored.status == OccupancyStatus.UNKNOWN:
            return UndoVerification(ExecutionOutcome.VERIFICATION_FAILED, error=restored.error)
        if restored.status != OccupancyStatus.OCCUPIED:
            return UndoVerification(ExecutionOutcome.VERIFICATION_FAILED, error=_undo_error("Restored path does not exist after Undo."))
        restore_chain = self.chain_safety.classify_chain(operation.restore_path, operation.restore_root)
        if not restore_chain.allowed:
            return UndoVerification(ExecutionOutcome.VERIFICATION_FAILED, error=restore_chain.error)
        identity = self._snapshot_required(operation.restore_path, ErrorCode.VERIFICATION_FAILED, "Restored file identity could not be verified.")
        if identity.error:
            return UndoVerification(ExecutionOutcome.VERIFICATION_FAILED, error=identity.error)
        if identity.snapshot.identity != operation.expected_identity.identity:
            return UndoVerification(ExecutionOutcome.VERIFICATION_FAILED, error=_undo_error("Restored file identity does not match the file FileFlow moved."))
        return UndoVerification(ExecutionOutcome.SUCCEEDED, identity.snapshot)

    def _snapshot_required(self, path: str, code: ErrorCode, message: str) -> IdentityResult:
        try:
            result = self.identity_provider.snapshot(path)
        except Exception as exc:
            return IdentityResult(None, StructuredError(code, Severity.OPERATION_BLOCKING, message, {"path": path, "error": str(exc)}))
        if not result.supported or result.snapshot is None:
            return IdentityResult(None, result.error or StructuredError(code, Severity.OPERATION_BLOCKING, message, {"path": path}))
        return result

    def _case_collision_error(self, restore_path: str) -> StructuredError | None:
        try:
            entries = self.directory_entries(ntpath.dirname(restore_path))
        except OSError as exc:
            return StructuredError(
                ErrorCode.UNKNOWN_IO_ERROR,
                Severity.OPERATION_BLOCKING,
                "Could not inspect the original directory for name collisions.",
                {"path": restore_path, "error": str(exc)},
            )
        if self.path_policy.has_case_collision(restore_path, list(entries)):
            return StructuredError(
                ErrorCode.DESTINATION_EXISTS,
                Severity.OPERATION_BLOCKING,
                "A case-equivalent entry occupies the original path.",
                {"path": restore_path},
            )
        return None

    def _filesystem_entries(self, parent: str) -> tuple[str, ...]:
        return tuple(str(child) for child in Path(parent).iterdir())

    def _blocked(self, code: ErrorCode, message: str, operation: UndoPlannedOperation) -> UndoPreflightResult:
        return UndoPreflightResult(
            None,
            StructuredError(code, Severity.OPERATION_BLOCKING, message, {"operation_id": operation.id}),
        )


class UndoPlanner:
    def __init__(self, database: Database, executor: UndoMoveExecutor):
        self.database = database
        self.executor = executor
        self.repository = UndoRepository(database)

    def create_plan(self, original_batch_id: str) -> UndoPlan:
        batch = self.database.connection.execute(
            "SELECT * FROM operation_batch WHERE id = ?",
            (original_batch_id,),
        ).fetchone()
        if batch is None:
            raise ValueError("Apply batch was not found.")
        apply_plan = PlanRepository(self.database).load_plan(batch["plan_id"])
        if apply_plan is None:
            raise ValueError("The immutable Apply plan could not be loaded.")
        executions = tuple(
            self.database.connection.execute(
                "SELECT * FROM executed_operation WHERE batch_id = ? ORDER BY started_at, id",
                (original_batch_id,),
            )
        )
        succeeded = tuple(row for row in executions if row["result"] == JournalState.SUCCEEDED.value)
        excluded_apply_count = len(apply_plan.operations) - len(succeeded)
        plan_id = str(uuid.uuid4())
        planned_by_id = {operation.id: operation for operation in apply_plan.operations}
        operations: list[UndoPlannedOperation] = []
        for index, row in enumerate(succeeded):
            original = planned_by_id.get(row["planned_operation_id"])
            expected = _try_identity_snapshot(row["identity_after_json"])
            operation = UndoPlannedOperation(
                id=str(uuid.uuid4()),
                undo_plan_id=plan_id,
                original_plan_id=apply_plan.id,
                original_batch_id=original_batch_id,
                original_planned_operation_id=row["planned_operation_id"],
                original_execution_id=row["id"],
                source_path=row["destination"] or "",
                restore_path=row["source_before"],
                source_root=apply_plan.destination_root,
                restore_root=apply_plan.source_root_normalized,
                expected_identity=expected,
                source_root_identity=apply_plan.destination_root_identity,
                restore_root_identity=apply_plan.source_root_identity,
                preview_source_identity=None,
                source_parent_identity=None,
                restore_parent_identity=None,
                status=UndoOperationStatus.BLOCKED,
                reason=None,
                metadata_changed=False,
                preview_index=index,
            )
            consistency_error = self._consistency_error(row, original, expected)
            prior_state = self.repository.blocking_undo_state(row["id"])
            if prior_state is not None:
                consistency_error = StructuredError(
                    ErrorCode.UNDO_CONFLICT,
                    Severity.OPERATION_BLOCKING,
                    "This move already has a successful or unresolved Undo.",
                    {"original_execution_id": row["id"], "undo_state": prior_state},
                )
            if consistency_error is not None:
                operations.append(replace(operation, reason=consistency_error))
                continue
            assessment = self.executor.prepare(
                UndoPlan(
                    plan_id,
                    original_batch_id,
                    apply_plan.id,
                    UndoPlanStatus.READY,
                    apply_plan.safety_policy_version,
                    (),
                    excluded_apply_count,
                    datetime.now(timezone.utc).isoformat(),
                ),
                operation,
                enforce_frozen_parents=False,
            )
            if not assessment.allowed:
                operations.append(replace(operation, reason=assessment.error))
                continue
            preflight = assessment.preflight
            metadata_changed = _metadata_changed(expected.metadata, preflight.source_identity.metadata)
            operations.append(
                replace(
                    operation,
                    preview_source_identity=preflight.source_identity,
                    source_parent_identity=preflight.source_parent_identity,
                    restore_parent_identity=preflight.restore_parent_identity,
                    status=UndoOperationStatus.READY,
                    metadata_changed=metadata_changed,
                )
            )

        ready_count = sum(1 for operation in operations if operation.status == UndoOperationStatus.READY)
        status = UndoPlanStatus.READY if ready_count and ready_count <= MAX_UNDO_OPERATIONS else UndoPlanStatus.BLOCKED
        plan = UndoPlan(
            plan_id,
            original_batch_id,
            apply_plan.id,
            status,
            apply_plan.safety_policy_version,
            tuple(operations),
            excluded_apply_count,
            datetime.now(timezone.utc).isoformat(),
        )
        self.repository.save_plan(plan)
        return plan

    def revalidate(self, plan: UndoPlan) -> UndoValidationResult:
        persisted = self.repository.load_plan(plan.id)
        if persisted is None or persisted != plan:
            return UndoValidationResult(
                UndoPlanStatus.BLOCKED,
                errors=(_undo_error("Undo plan does not match its immutable persisted snapshot."),),
            )
        if plan.status != UndoPlanStatus.READY:
            return UndoValidationResult(
                UndoPlanStatus.BLOCKED,
                errors=(_undo_error("Only a ready persisted Undo Preview can execute."),),
            )
        if len(plan.ready_operations) > MAX_UNDO_OPERATIONS:
            return UndoValidationResult(
                UndoPlanStatus.BLOCKED,
                errors=(_undo_error(f"Undo exceeds the {MAX_UNDO_OPERATIONS}-operation safety limit."),),
            )
        errors: list[StructuredError] = []
        preflights: list[UndoPreflight] = []
        for operation in plan.ready_operations:
            prior_state = self.repository.blocking_undo_state(operation.original_execution_id)
            if prior_state is not None:
                errors.append(
                    StructuredError(
                        ErrorCode.UNDO_CONFLICT,
                        Severity.OPERATION_BLOCKING,
                        "This move already has a successful or unresolved Undo.",
                        {"original_execution_id": operation.original_execution_id, "undo_state": prior_state},
                    )
                )
                continue
            result = self.executor.prepare(plan, operation, enforce_frozen_parents=True)
            if not result.allowed:
                errors.append(result.error or _undo_error("Undo safety revalidation failed."))
            else:
                preflights.append(result.preflight)
        if errors:
            stale_codes = {
                ErrorCode.SOURCE_MISSING,
                ErrorCode.SOURCE_IDENTITY_CHANGED,
                ErrorCode.DESTINATION_EXISTS,
                ErrorCode.DESTINATION_PARENT_CHANGED,
                ErrorCode.ROOT_IDENTITY_CHANGED,
                ErrorCode.UNDO_CONFLICT,
            }
            status = UndoPlanStatus.STALE if any(error.code in stale_codes for error in errors) else UndoPlanStatus.BLOCKED
            return UndoValidationResult(status, errors=tuple(errors))
        if not preflights:
            return UndoValidationResult(
                UndoPlanStatus.BLOCKED,
                errors=(_undo_error("Undo Preview contains no ready operations."),),
            )
        return UndoValidationResult(UndoPlanStatus.READY, tuple(preflights))

    def _consistency_error(self, row, original, expected: IdentitySnapshot | None) -> StructuredError | None:
        if original is None:
            return _undo_error("Original planned operation is missing from immutable Apply history.")
        if row["operation_type"] != OperationIntent.MOVE.value:
            return _undo_error("Only FileFlow move executions can be undone.")
        if not row["destination"] or original.destination_path != row["destination"] or original.source_path != row["source_before"]:
            return _undo_error("Apply execution paths do not match the immutable plan.")
        if expected is None:
            return _undo_error("Apply did not persist a verified destination identity.")
        return None


class UndoJournalCoordinator:
    def __init__(
        self,
        database: Database,
        executor: UndoMoveExecutor,
        *,
        process_probe: ProcessIdentityProbe | None = None,
    ):
        self.database = database
        self.executor = executor
        self.repository = UndoRepository(database)
        self.planner = UndoPlanner(database, executor)
        self.lock_coordinator = JournalCoordinator(database, executor, process_probe=process_probe)

    def execute_batch(
        self,
        plan: UndoPlan,
        *,
        progress_callback: Callable[[str, int, int], None] | None = None,
        stop_after: JournalState | None = None,
    ) -> str:
        lock_assessment = self.lock_coordinator.reconcile_execution_lock()
        if lock_assessment.blocks_mutation:
            raise JournalExecutionBlocked(
                StructuredError(ErrorCode.RECOVERY_REQUIRED, Severity.RECOVERY, lock_assessment.message)
            )
        if self.lock_coordinator.all_operations_requiring_recovery():
            raise JournalExecutionBlocked(
                StructuredError(
                    ErrorCode.RECOVERY_REQUIRED,
                    Severity.RECOVERY,
                    "Unresolved Apply or Undo work must be reviewed before another mutation.",
                )
            )
        owner = self.lock_coordinator.acquire_mutation_lock()
        execution_error: BaseException | None = None
        try:
            validation = self.planner.revalidate(plan)
            if not validation.valid:
                raise UndoPlanRejected(validation)
            preflights = validation.preflights
            batch_id = str(uuid.uuid4())
            now = datetime.now(timezone.utc).isoformat()
            with self.database.connection:
                self.database.connection.execute(
                    """
                    INSERT INTO undo_batch(
                        id, undo_plan_id, original_batch_id, status, approved_at,
                        app_version, summary_json, recovery_required
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, 0)
                    """,
                    (
                        batch_id,
                        plan.id,
                        plan.original_batch_id,
                        JournalState.APPROVED.value,
                        now,
                        APP_VERSION,
                        json.dumps({"operation_count": len(preflights)}, sort_keys=True),
                    ),
                )
            with self.database.connection:
                self.database.connection.execute(
                    "UPDATE undo_batch SET status = ?, started_at = ? WHERE id = ?",
                    (JournalState.IN_PROGRESS.value, datetime.now(timezone.utc).isoformat(), batch_id),
                )

            for index, preflight in enumerate(preflights, start=1):
                operation = preflight.operation
                if progress_callback is not None:
                    progress_callback(operation.source_path, index, len(preflights))
                executed_id = self._record_intent(batch_id, preflight)
                self._refresh_batch(batch_id)
                if stop_after == JournalState.INTENT_RECORDED:
                    return batch_id
                self._update_execution(executed_id, JournalState.IN_PROGRESS, None, expected=JournalState.INTENT_RECORDED)
                if stop_after == JournalState.IN_PROGRESS:
                    return batch_id

                move_result = self.executor.move(preflight)
                if isinstance(move_result, tuple):
                    outcome, error = move_result
                else:
                    outcome, error = move_result, None
                if outcome == ExecutionOutcome.INTERRUPTED:
                    self._update_execution(executed_id, JournalState.INTERRUPTED, error, expected=JournalState.IN_PROGRESS)
                    self._refresh_batch(batch_id)
                    return batch_id
                if outcome != ExecutionOutcome.SUCCEEDED:
                    self._update_execution(executed_id, JournalState.FAILED, error, expected=JournalState.IN_PROGRESS)
                    self._refresh_batch(batch_id)
                    continue

                self._update_execution(executed_id, JournalState.VERIFYING, None, expected=JournalState.IN_PROGRESS)
                if stop_after == JournalState.VERIFYING:
                    return batch_id
                verification = self.executor.verify(preflight)
                if verification.outcome == ExecutionOutcome.SUCCEEDED and verification.identity_after is not None:
                    self._update_execution(
                        executed_id,
                        JournalState.SUCCEEDED,
                        None,
                        identity_after=verification.identity_after,
                        expected=JournalState.VERIFYING,
                    )
                else:
                    self._update_execution(
                        executed_id,
                        JournalState.RECOVERY_REQUIRED,
                        verification.error or _undo_error("Undo completed but verification was uncertain."),
                        expected=JournalState.VERIFYING,
                    )
                    self._refresh_batch(batch_id)
                    return batch_id
                self._refresh_batch(batch_id)
            return batch_id
        except BaseException as exc:
            execution_error = exc
            raise
        finally:
            if not self.lock_coordinator.release_mutation_lock(owner):
                release_error = JournalExecutionBlocked(
                    StructuredError(
                        ErrorCode.RECOVERY_REQUIRED,
                        Severity.RECOVERY,
                        "FileFlow could not safely release the global mutation lock because ownership changed.",
                    )
                )
                if execution_error is None:
                    raise release_error
                execution_error.add_note(release_error.error.message)

    def batch_summary(self, batch_id: str):
        return self.database.connection.execute(
            """
            SELECT
                undo_batch.*,
                COUNT(undo_execution.id) AS attempted_count,
                SUM(CASE WHEN undo_execution.state = 'SUCCEEDED' THEN 1 ELSE 0 END) AS succeeded_count,
                SUM(CASE WHEN undo_execution.state = 'FAILED' THEN 1 ELSE 0 END) AS failed_count,
                SUM(CASE WHEN undo_execution.state IN ('INTERRUPTED', 'RECOVERY_REQUIRED') THEN 1 ELSE 0 END) AS recovery_count
            FROM undo_batch
            LEFT JOIN undo_execution ON undo_execution.undo_batch_id = undo_batch.id
            WHERE undo_batch.id = ?
            GROUP BY undo_batch.id
            """,
            (batch_id,),
        ).fetchone()

    def _record_intent(self, batch_id: str, preflight: UndoPreflight) -> str:
        operation = preflight.operation
        executed_id = str(uuid.uuid4())
        started_at = datetime.now(timezone.utc).isoformat()
        try:
            with self.database.connection:
                self.database.connection.execute(
                    """
                    INSERT INTO undo_execution(
                        id, undo_batch_id, undo_planned_operation_id,
                        original_execution_id, source_before, restore_destination,
                        expected_identity_json, identity_before_json,
                        metadata_before_json, state, started_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        executed_id,
                        batch_id,
                        operation.id,
                        operation.original_execution_id,
                        operation.source_path,
                        operation.restore_path,
                        json.dumps(dataclass_to_jsonable(operation.expected_identity), sort_keys=True),
                        json.dumps(dataclass_to_jsonable(preflight.source_identity), sort_keys=True),
                        json.dumps(dataclass_to_jsonable(preflight.source_identity.metadata), sort_keys=True),
                        JournalState.INTENT_RECORDED.value,
                        started_at,
                    ),
                )
                self._record_event(executed_id, batch_id, JournalState.INTENT_RECORDED)
        except sqlite3.IntegrityError as exc:
            raise UndoPlanRejected(
                UndoValidationResult(
                    UndoPlanStatus.BLOCKED,
                    errors=(_undo_error("A successful or unresolved Undo already exists for this move."),),
                )
            ) from exc
        return executed_id

    def _update_execution(
        self,
        executed_id: str,
        state: JournalState,
        error: StructuredError | None,
        *,
        expected: JournalState,
        identity_after: IdentitySnapshot | None = None,
    ) -> None:
        completed_at = None if state in (
            JournalState.INTENT_RECORDED,
            JournalState.IN_PROGRESS,
            JournalState.VERIFYING,
            JournalState.INTERRUPTED,
            JournalState.RECOVERY_REQUIRED,
        ) else datetime.now(timezone.utc).isoformat()
        identity_json = json.dumps(dataclass_to_jsonable(identity_after), sort_keys=True) if identity_after else None
        metadata_json = json.dumps(dataclass_to_jsonable(identity_after.metadata), sort_keys=True) if identity_after else None
        with self.database.connection:
            cursor = self.database.connection.execute(
                """
                UPDATE undo_execution
                SET state = ?, error_code = ?, error_detail = ?,
                    identity_after_json = COALESCE(?, identity_after_json),
                    metadata_after_json = COALESCE(?, metadata_after_json),
                    completed_at = ?
                WHERE id = ? AND state = ?
                """,
                (
                    state.value,
                    error.code.value if error else None,
                    error.message if error else None,
                    identity_json,
                    metadata_json,
                    completed_at,
                    executed_id,
                    expected.value,
                ),
            )
            if cursor.rowcount != 1:
                raise JournalExecutionBlocked(
                    StructuredError(
                        ErrorCode.RECOVERY_REQUIRED,
                        Severity.RECOVERY,
                        "Undo journal state changed unexpectedly during execution.",
                        {"undo_execution_id": executed_id, "requested_state": state.value},
                    )
                )
            row = self.database.connection.execute(
                "SELECT undo_batch_id FROM undo_execution WHERE id = ?",
                (executed_id,),
            ).fetchone()
            self._record_event(executed_id, row["undo_batch_id"], state)

    def _record_event(self, executed_id: str, batch_id: str, state: JournalState) -> None:
        self.database.connection.execute(
            "INSERT INTO undo_state_event(id, undo_execution_id, undo_batch_id, state, created_at) VALUES (?, ?, ?, ?, ?)",
            (str(uuid.uuid4()), executed_id, batch_id, state.value, datetime.now(timezone.utc).isoformat()),
        )

    def _refresh_batch(self, batch_id: str) -> None:
        rows = tuple(self.database.connection.execute("SELECT state FROM undo_execution WHERE undo_batch_id = ?", (batch_id,)))
        if not rows:
            return
        states = {row["state"] for row in rows}
        if JournalState.RECOVERY_REQUIRED.value in states:
            status, recovery, completed = JournalState.RECOVERY_REQUIRED.value, 1, None
        elif JournalState.INTERRUPTED.value in states:
            status, recovery, completed = JournalState.INTERRUPTED.value, 1, None
        elif any(state in states for state in ACTIVE_UNDO_STATES[:3]):
            status, recovery, completed = JournalState.IN_PROGRESS.value, 0, None
        elif states == {JournalState.SUCCEEDED.value}:
            status, recovery, completed = JournalState.SUCCEEDED.value, 0, datetime.now(timezone.utc).isoformat()
        elif states == {JournalState.FAILED.value}:
            status, recovery, completed = JournalState.FAILED.value, 0, datetime.now(timezone.utc).isoformat()
        else:
            status, recovery, completed = "PARTIAL_FAILURE", 0, datetime.now(timezone.utc).isoformat()
        with self.database.connection:
            self.database.connection.execute(
                "UPDATE undo_batch SET status = ?, recovery_required = ?, completed_at = ? WHERE id = ?",
                (status, recovery, completed, batch_id),
            )


class UndoController:
    def __init__(
        self,
        database: Database,
        *,
        executor_factory: Callable[[], UndoMoveExecutor] | None = None,
        process_probe: ProcessIdentityProbe | None = None,
    ):
        self.database = database
        self.executor_factory = executor_factory or UndoMoveExecutor
        self.process_probe = process_probe
        self.repository = UndoRepository(database)
        self._active = False

    def create_plan(self, original_batch_id: str) -> UndoPlan:
        return UndoPlanner(self.database, self.executor_factory()).create_plan(original_batch_id)

    def readiness(self, plan: UndoPlan | None) -> UndoReadiness:
        coordinator = self._coordinator()
        lock = coordinator.lock_coordinator.reconcile_execution_lock()
        if lock.blocks_mutation:
            return UndoReadiness(UndoControllerState.RECOVERY_REQUIRED, False, lock.message)
        if coordinator.lock_coordinator.all_operations_requiring_recovery():
            return UndoReadiness(
                UndoControllerState.RECOVERY_REQUIRED,
                False,
                "Unresolved Apply or Undo work must be reviewed before Undo.",
            )
        if self._active:
            return UndoReadiness(UndoControllerState.UNDOING, False, "FileFlow is already restoring files.")
        if plan is None:
            return UndoReadiness(UndoControllerState.NO_PREVIEW, False, "Select an Apply batch and create an Undo Preview.")
        ready = len(plan.ready_operations)
        blocked = len(plan.operations) - ready
        if plan.status != UndoPlanStatus.READY or ready == 0:
            return UndoReadiness(UndoControllerState.PREVIEW_BLOCKED, False, "This Undo Preview has no restorable files.", ready, blocked, plan.excluded_apply_count)
        if ready > MAX_UNDO_OPERATIONS:
            return UndoReadiness(
                UndoControllerState.PREVIEW_BLOCKED,
                False,
                f"Undo contains {ready} operations; the limit is {MAX_UNDO_OPERATIONS}.",
                ready,
                blocked,
                plan.excluded_apply_count,
            )
        return UndoReadiness(
            UndoControllerState.PREVIEW_READY,
            True,
            "Ready to restore files after validation and confirmation.",
            ready,
            blocked,
            plan.excluded_apply_count,
        )

    def validate_before_confirmation(self, plan: UndoPlan) -> UndoReadiness:
        readiness = self.readiness(plan)
        if not readiness.can_undo:
            return readiness
        validation = UndoPlanner(self.database, self.executor_factory()).revalidate(plan)
        if validation.status == UndoPlanStatus.STALE:
            return UndoReadiness(UndoControllerState.PREVIEW_STALE, False, validation.errors[0].message, readiness.ready_count, readiness.blocked_count, readiness.excluded_apply_count)
        if validation.status == UndoPlanStatus.BLOCKED:
            return UndoReadiness(UndoControllerState.PREVIEW_BLOCKED, False, validation.errors[0].message, readiness.ready_count, readiness.blocked_count, readiness.excluded_apply_count)
        return readiness

    def confirmation_summary(self, plan: UndoPlan) -> UndoConfirmationSummary:
        ready = plan.ready_operations
        return UndoConfirmationSummary(
            len(ready),
            len(plan.operations) - len(ready),
            plan.excluded_apply_count,
            sum(1 for operation in ready if operation.metadata_changed),
            "FileFlow will move these files back to their original locations. It will not restore earlier file contents or replace anything already there.",
        )

    def confirm_and_undo(
        self,
        plan: UndoPlan,
        confirm: Callable[[UndoConfirmationSummary], bool],
        *,
        progress_callback: Callable[[str, int, int], None] | None = None,
    ) -> UndoResult:
        readiness = self.validate_before_confirmation(plan)
        if not readiness.can_undo:
            return UndoResult(readiness.state, None, readiness.message)
        if not confirm(self.confirmation_summary(plan)):
            return UndoResult(UndoControllerState.PREVIEW_READY, None, "Undo cancelled. No files were changed.")
        return self.undo_confirmed(plan, progress_callback=progress_callback)

    def undo_confirmed(
        self,
        plan: UndoPlan,
        *,
        progress_callback: Callable[[str, int, int], None] | None = None,
    ) -> UndoResult:
        readiness = self.readiness(plan)
        if not readiness.can_undo:
            return UndoResult(readiness.state, None, readiness.message)
        self._active = True
        try:
            coordinator = self._coordinator()
            batch_id = coordinator.execute_batch(plan, progress_callback=progress_callback)
            summary = coordinator.batch_summary(batch_id)
            if summary is None:
                return UndoResult(UndoControllerState.COMPLETE, batch_id, "Undo finished, but its summary could not be loaded.")
            attempted = int(summary["attempted_count"] or 0)
            succeeded = int(summary["succeeded_count"] or 0)
            failed = int(summary["failed_count"] or 0)
            recovery = int(summary["recovery_count"] or 0)
            if recovery:
                return UndoResult(
                    UndoControllerState.RECOVERY_REQUIRED,
                    batch_id,
                    "Undo could not be fully verified. FileFlow stopped before restoring another file.",
                    attempted,
                    succeeded,
                    failed,
                    recovery,
                )
            return UndoResult(
                UndoControllerState.COMPLETE,
                batch_id,
                f"Restored {succeeded} of {attempted} attempted files. Failed safely: {failed}.",
                attempted,
                succeeded,
                failed,
                recovery,
            )
        except UndoPlanRejected as exc:
            state = UndoControllerState.PREVIEW_STALE if exc.result.status == UndoPlanStatus.STALE else UndoControllerState.PREVIEW_BLOCKED
            return UndoResult(state, None, str(exc))
        except JournalExecutionBlocked as exc:
            return UndoResult(UndoControllerState.RECOVERY_REQUIRED, None, exc.error.message)
        finally:
            self._active = False

    def list_batches(self):
        return self.repository.list_batches()

    def list_executions(self, undo_batch_id: str):
        return self.repository.list_executions(undo_batch_id)

    def operations_requiring_recovery(self):
        return self._coordinator().lock_coordinator.undo_operations_requiring_recovery()

    def recovery_inspections(self):
        rows = tuple(
            self.database.connection.execute(
                """
                SELECT undo_execution.*, undo_planned_operation.snapshot_json AS operation_snapshot_json
                FROM undo_execution
                JOIN undo_planned_operation ON undo_planned_operation.id = undo_execution.undo_planned_operation_id
                WHERE undo_execution.state IN ('INTENT_RECORDED', 'IN_PROGRESS', 'VERIFYING', 'INTERRUPTED', 'RECOVERY_REQUIRED')
                ORDER BY undo_execution.started_at, undo_execution.id
                """
            )
        )
        inspector = UndoRecoveryInspector()
        results = []
        for row in rows:
            try:
                operation = _undo_operation_from_json(json.loads(row["operation_snapshot_json"]))
                inspection = inspector.inspect(
                    row["source_before"],
                    row["restore_destination"],
                    row["expected_identity_json"],
                    source_root=operation.source_root,
                    restore_root=operation.restore_root,
                )
            except Exception as exc:
                inspection = UndoRecoveryInspection(
                    UndoRecoveryClassification.INSPECTION_BLOCKED,
                    StructuredError(
                        ErrorCode.RECOVERY_REQUIRED,
                        Severity.RECOVERY,
                        "Undo recovery history could not be inspected safely.",
                        {"error": str(exc)},
                    ),
                )
            results.append((row, inspection))
        return tuple(results)

    def _coordinator(self) -> UndoJournalCoordinator:
        return UndoJournalCoordinator(
            self.database,
            self.executor_factory(),
            process_probe=self.process_probe,
        )


class UndoRecoveryClassification(str, Enum):
    LIKELY_NOT_UNDONE = "LIKELY_NOT_UNDONE"
    LIKELY_UNDO_COMPLETED = "LIKELY_UNDO_COMPLETED"
    CONFLICT_RECOVERY_REQUIRED = "CONFLICT_RECOVERY_REQUIRED"
    MISSING_RECOVERY_REQUIRED = "MISSING_RECOVERY_REQUIRED"
    INSPECTION_BLOCKED = "INSPECTION_BLOCKED"


@dataclass(frozen=True)
class UndoRecoveryInspection:
    classification: UndoRecoveryClassification
    error: StructuredError | None = None


class UndoRecoveryInspector:
    def __init__(
        self,
        *,
        identity_provider: FileIdentityProvider | None = None,
        occupancy: DestinationOccupancyInspector | None = None,
        path_policy: WindowsPathPolicy | None = None,
        chain_safety: PathChainSafety | None = None,
        cloud_classifier: CloudClassifier | None = None,
    ):
        self.identity_provider = identity_provider or WindowsFileIdentityProvider()
        self.occupancy = occupancy or DestinationOccupancyInspector()
        self.path_policy = path_policy or WindowsPathPolicy()
        self.chain_safety = chain_safety or PathChainSafety(self.path_policy, WindowsReparseInspector())
        self.cloud_classifier = cloud_classifier or ConservativeCloudClassifier()

    def inspect(
        self,
        source_path: str,
        restore_path: str,
        expected_identity_json: str,
        *,
        source_root: str | None = None,
        restore_root: str | None = None,
    ) -> UndoRecoveryInspection:
        expected = _identity_snapshot_from_json(json.loads(expected_identity_json)).identity
        source = self.occupancy.inspect(source_path)
        restore = self.occupancy.inspect(restore_path)
        if source.status == OccupancyStatus.UNKNOWN:
            return UndoRecoveryInspection(UndoRecoveryClassification.INSPECTION_BLOCKED, source.error)
        if restore.status == OccupancyStatus.UNKNOWN:
            return UndoRecoveryInspection(UndoRecoveryClassification.INSPECTION_BLOCKED, restore.error)
        if source_root is not None:
            source_inspection_path = source_path if source.status == OccupancyStatus.OCCUPIED else ntpath.dirname(source_path)
            decision = self.chain_safety.classify_chain(source_inspection_path, source_root)
            if not decision.allowed:
                return UndoRecoveryInspection(UndoRecoveryClassification.INSPECTION_BLOCKED, decision.error)
        if restore_root is not None:
            restore_inspection_path = restore_path if restore.status == OccupancyStatus.OCCUPIED else ntpath.dirname(restore_path)
            decision = self.chain_safety.classify_chain(restore_inspection_path, restore_root)
            if not decision.allowed:
                return UndoRecoveryInspection(UndoRecoveryClassification.INSPECTION_BLOCKED, decision.error)
        for path, state in ((source_path, source), (restore_path, restore)):
            inspect_path = path if state.status == OccupancyStatus.OCCUPIED else ntpath.dirname(path)
            cloud = self.cloud_classifier.classify(inspect_path)
            if not cloud.safe:
                return UndoRecoveryInspection(UndoRecoveryClassification.INSPECTION_BLOCKED, cloud.error)
        if source.status == OccupancyStatus.OCCUPIED and restore.status == OccupancyStatus.FREE:
            identity = self.identity_provider.snapshot(source_path)
            if identity.supported and identity.snapshot and identity.snapshot.identity == expected:
                return UndoRecoveryInspection(UndoRecoveryClassification.LIKELY_NOT_UNDONE)
            return UndoRecoveryInspection(
                UndoRecoveryClassification.CONFLICT_RECOVERY_REQUIRED,
                identity.error or _undo_error("Undo source identity does not match recovery history."),
            )
        if source.status == OccupancyStatus.FREE and restore.status == OccupancyStatus.OCCUPIED:
            identity = self.identity_provider.snapshot(restore_path)
            if identity.supported and identity.snapshot and identity.snapshot.identity == expected:
                return UndoRecoveryInspection(UndoRecoveryClassification.LIKELY_UNDO_COMPLETED)
            return UndoRecoveryInspection(
                UndoRecoveryClassification.CONFLICT_RECOVERY_REQUIRED,
                identity.error or _undo_error("Restore path contains a different file."),
            )
        if source.status == OccupancyStatus.OCCUPIED and restore.status == OccupancyStatus.OCCUPIED:
            return UndoRecoveryInspection(UndoRecoveryClassification.CONFLICT_RECOVERY_REQUIRED)
        return UndoRecoveryInspection(UndoRecoveryClassification.MISSING_RECOVERY_REQUIRED)


def _undo_error(message: str) -> StructuredError:
    return StructuredError(ErrorCode.UNDO_CONFLICT, Severity.OPERATION_BLOCKING, message)


def _metadata_changed(before: MetadataSnapshot, after: MetadataSnapshot) -> bool:
    return (
        before.size,
        before.mtime_ns,
        before.ctime_ns,
        before.attributes,
    ) != (
        after.size,
        after.mtime_ns,
        after.ctime_ns,
        after.attributes,
    )


def _try_identity_snapshot(value: str | None) -> IdentitySnapshot | None:
    if not value:
        return None
    try:
        return _identity_snapshot_from_json(json.loads(value))
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None


def _identity_snapshot_from_json(payload: dict) -> IdentitySnapshot:
    identity_payload = payload["identity"]
    metadata_payload = payload["metadata"]
    return IdentitySnapshot(
        FileIdentity(
            volume_id=identity_payload["volume_id"],
            file_id=identity_payload["file_id"],
            file_type=identity_payload["file_type"],
            link_count=identity_payload.get("link_count"),
        ),
        MetadataSnapshot(
            logical_path=metadata_payload["logical_path"],
            size=int(metadata_payload["size"]),
            mtime_ns=int(metadata_payload["mtime_ns"]),
            ctime_ns=int(metadata_payload["ctime_ns"]),
            attributes=metadata_payload.get("attributes"),
            reparse_tag=metadata_payload.get("reparse_tag"),
            reparse_kind=metadata_payload.get("reparse_kind"),
        ),
    )


def _structured_error_from_json(payload: dict | None) -> StructuredError | None:
    if payload is None:
        return None
    return StructuredError(
        ErrorCode(payload["code"]),
        Severity(payload["severity"]),
        payload["message"],
        payload.get("details", {}),
    )


def _undo_operation_from_json(payload: dict) -> UndoPlannedOperation:
    return UndoPlannedOperation(
        id=payload["id"],
        undo_plan_id=payload["undo_plan_id"],
        original_plan_id=payload["original_plan_id"],
        original_batch_id=payload["original_batch_id"],
        original_planned_operation_id=payload["original_planned_operation_id"],
        original_execution_id=payload["original_execution_id"],
        source_path=payload["source_path"],
        restore_path=payload["restore_path"],
        source_root=payload["source_root"],
        restore_root=payload["restore_root"],
        expected_identity=_identity_snapshot_from_json(payload["expected_identity"]) if payload.get("expected_identity") else None,
        source_root_identity=_identity_snapshot_from_json(payload["source_root_identity"]),
        restore_root_identity=_identity_snapshot_from_json(payload["restore_root_identity"]),
        preview_source_identity=_identity_snapshot_from_json(payload["preview_source_identity"]) if payload.get("preview_source_identity") else None,
        source_parent_identity=_identity_snapshot_from_json(payload["source_parent_identity"]) if payload.get("source_parent_identity") else None,
        restore_parent_identity=_identity_snapshot_from_json(payload["restore_parent_identity"]) if payload.get("restore_parent_identity") else None,
        status=UndoOperationStatus(payload["status"]),
        reason=_structured_error_from_json(payload.get("reason")),
        metadata_changed=bool(payload["metadata_changed"]),
        preview_index=int(payload["preview_index"]),
    )


def _undo_plan_from_json(payload: dict) -> UndoPlan:
    return UndoPlan(
        id=payload["id"],
        original_batch_id=payload["original_batch_id"],
        original_plan_id=payload["original_plan_id"],
        status=UndoPlanStatus(payload["status"]),
        safety_policy_version=int(payload["safety_policy_version"]),
        operations=tuple(_undo_operation_from_json(operation) for operation in payload.get("operations", ())),
        excluded_apply_count=int(payload["excluded_apply_count"]),
        created_at=payload["created_at"],
    )

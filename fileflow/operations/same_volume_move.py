from __future__ import annotations

import json
import ntpath
import os
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Callable

from ..models import (
    ErrorCode,
    ExecutionOutcome,
    ExecutionResult,
    FileIdentity,
    IdentitySnapshot,
    MetadataSnapshot,
    PlannedOperation,
    PreviewPlan,
    RevalidationStatus,
    Severity,
    StructuredError,
)
from ..planner import PlanRevalidator
from ..rules import default_categories, default_rules
from ..safety import (
    CloudClassifier,
    ConservativeCloudClassifier,
    FileIdentityProvider,
    IdentityResult,
    PathChainSafety,
    WindowsFileIdentityProvider,
    WindowsPathPolicy,
    WindowsReparseInspector,
)


class OccupancyStatus(str, Enum):
    FREE = "FREE"
    OCCUPIED = "OCCUPIED"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class OccupancyResult:
    status: OccupancyStatus
    error: StructuredError | None = None

    @property
    def free(self) -> bool:
        return self.status == OccupancyStatus.FREE and self.error is None


class DestinationOccupancyInspector:
    """No-follow occupancy check for a destination path.

    `Path.lstat()` reports dangling symlinks and reparse entries as occupied.
    Only FileNotFoundError means free; any other inspection error is unknown.
    """

    def inspect(self, path: str) -> OccupancyResult:
        try:
            Path(path).lstat()
        except FileNotFoundError:
            return OccupancyResult(OccupancyStatus.FREE)
        except OSError as exc:
            return OccupancyResult(
                OccupancyStatus.UNKNOWN,
                StructuredError(
                    ErrorCode.UNKNOWN_IO_ERROR,
                    Severity.OPERATION_BLOCKING,
                    "Could not inspect destination occupancy.",
                    {"path": path, "error": str(exc)},
                ),
            )
        return OccupancyResult(
            OccupancyStatus.OCCUPIED,
            StructuredError(
                ErrorCode.DESTINATION_EXISTS,
                Severity.OPERATION_BLOCKING,
                "Destination path is already occupied.",
                {"path": path},
            ),
        )


@dataclass(frozen=True)
class MovePreflight:
    plan: PreviewPlan
    operation: PlannedOperation
    source_identity_before: IdentitySnapshot
    destination_parent_identity: IdentitySnapshot


@dataclass(frozen=True)
class MovePreflightResult:
    preflight: MovePreflight | None
    error: StructuredError | None = None

    @property
    def allowed(self) -> bool:
        return self.preflight is not None and self.error is None


class RecoveryClassification(str, Enum):
    LIKELY_NOT_MOVED = "LIKELY_NOT_MOVED"
    LIKELY_COMPLETED = "LIKELY_COMPLETED"
    CONFLICT_RECOVERY_REQUIRED = "CONFLICT_RECOVERY_REQUIRED"
    MISSING_RECOVERY_REQUIRED = "MISSING_RECOVERY_REQUIRED"
    INSPECTION_BLOCKED = "INSPECTION_BLOCKED"


@dataclass(frozen=True)
class RecoveryInspection:
    classification: RecoveryClassification
    error: StructuredError | None = None


class SameVolumeMoveExecutor:
    """Executes exactly one same-volume file move using `os.rename`.

    `os.rename` is used only after destination absence and same-volume identity
    have been proven. It does not copy across volumes, and this executor never
    asks for replacement semantics.
    """

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
        before_rename: Callable[[MovePreflight], None] | None = None,
    ):
        self.path_policy = path_policy or WindowsPathPolicy()
        self.chain_safety = chain_safety or PathChainSafety(self.path_policy, WindowsReparseInspector())
        self.identity_provider = identity_provider or WindowsFileIdentityProvider()
        self.cloud_classifier = cloud_classifier or ConservativeCloudClassifier()
        self.occupancy = occupancy or DestinationOccupancyInspector()
        self.directory_entries = directory_entries or self._filesystem_entries
        self.rename_primitive = rename_primitive or os.rename
        self.before_rename = before_rename

    def prepare(self, plan: PreviewPlan, operation: PlannedOperation) -> MovePreflightResult:
        from ..models import PlannedOperationStatus

        if operation.safety_status != PlannedOperationStatus.PLANNED:
            return self._blocked(ErrorCode.SAFETY_BLOCK, "Only planned safe operations can be executed.", operation)
        if operation.destination_path is None:
            return self._blocked(ErrorCode.DESTINATION_UNSAFE, "Operation has no frozen destination.", operation)

        single_operation_plan = self._single_operation_plan(plan, operation)
        revalidation = PlanRevalidator(
            self.path_policy,
            self.chain_safety,
            self.identity_provider,
            self.cloud_classifier,
            entry_exists=self._entry_exists_for_revalidator,
        ).revalidate(single_operation_plan, default_rules(default_categories()), default_categories(), self._parent_entries(operation.destination_path))
        if revalidation.status in (RevalidationStatus.STALE, RevalidationStatus.BLOCKED):
            return MovePreflightResult(
                None,
                revalidation.errors[0]
                if revalidation.errors
                else StructuredError(
                    ErrorCode.STALE_PLAN,
                    Severity.OPERATION_BLOCKING,
                    "Operation is no longer valid and must be previewed again.",
                    {"operation_id": operation.id, "status": revalidation.status.value},
                ),
            )

        source_state = self.occupancy.inspect(operation.source_path)
        if source_state.status == OccupancyStatus.FREE:
            return self._blocked(ErrorCode.SOURCE_MISSING, "Source path is no longer available.", operation)
        if source_state.status == OccupancyStatus.UNKNOWN:
            return MovePreflightResult(None, source_state.error)

        source_identity = self._snapshot_required(operation.source_path, ErrorCode.SOURCE_IDENTITY_CHANGED, "Source identity could not be verified.")
        if source_identity.error:
            return MovePreflightResult(None, source_identity.error)
        if operation.source_identity is None or source_identity.snapshot != operation.source_identity:
            return self._blocked(ErrorCode.SOURCE_IDENTITY_CHANGED, "Source identity changed after preview.", operation)

        destination_parent = ntpath.dirname(operation.destination_path)
        destination_parent_identity = self._snapshot_required(
            destination_parent,
            ErrorCode.DESTINATION_PARENT_CHANGED,
            "Destination parent identity could not be verified.",
        )
        if destination_parent_identity.error:
            return MovePreflightResult(None, destination_parent_identity.error)
        collision_error = self._case_collision_error(operation.destination_path)
        if collision_error is not None:
            return MovePreflightResult(None, collision_error)

        if source_identity.snapshot.identity.file_type != "file":
            return self._blocked(ErrorCode.UNSUPPORTED, "Only ordinary files can be moved.", operation)
        if source_identity.snapshot.identity.volume_id != destination_parent_identity.snapshot.identity.volume_id:
            return self._blocked(
                ErrorCode.CROSS_VOLUME_FAILURE,
                "Source and destination are not on the same filesystem volume.",
                operation,
                {"source_volume": source_identity.snapshot.identity.volume_id, "destination_volume": destination_parent_identity.snapshot.identity.volume_id},
            )

        cloud = self.cloud_classifier.classify(operation.source_path)
        if not cloud.safe:
            return MovePreflightResult(None, cloud.error or StructuredError(ErrorCode.CLOUD_PLACEHOLDER, Severity.OPERATION_BLOCKING, "Cloud state is unsafe.", {"path": operation.source_path}))

        return MovePreflightResult(MovePreflight(single_operation_plan, operation, source_identity.snapshot, destination_parent_identity.snapshot))

    def move(self, preflight: MovePreflight) -> ExecutionResult:
        final_check = self._final_pre_move_check(preflight)
        if final_check is not None:
            return ExecutionResult(preflight.operation.id, ExecutionOutcome.FAILED, final_check)
        if self.before_rename is not None:
            self.before_rename(preflight)
            final_check = self._final_pre_move_check(preflight)
            if final_check is not None:
                return ExecutionResult(preflight.operation.id, ExecutionOutcome.FAILED, final_check)
        try:
            self.rename_primitive(preflight.operation.source_path, preflight.operation.destination_path)
        except InterruptedError as exc:
            return ExecutionResult(
                preflight.operation.id,
                ExecutionOutcome.INTERRUPTED,
                StructuredError(ErrorCode.INTERRUPTED, Severity.RECOVERY, "Move was interrupted.", {"operation_id": preflight.operation.id, "error": str(exc)}),
            )
        except PermissionError as exc:
            return ExecutionResult(
                preflight.operation.id,
                ExecutionOutcome.FAILED,
                StructuredError(ErrorCode.ACCESS_DENIED, Severity.RECOVERABLE, "Move was denied by the filesystem.", {"operation_id": preflight.operation.id, "error": str(exc)}),
            )
        except OSError as exc:
            return ExecutionResult(
                preflight.operation.id,
                ExecutionOutcome.FAILED,
                StructuredError(ErrorCode.MOVE_FAILED, Severity.RECOVERABLE, "Same-volume move failed.", {"operation_id": preflight.operation.id, "error": str(exc)}),
            )
        return ExecutionResult(preflight.operation.id, ExecutionOutcome.SUCCEEDED)

    def verify(self, preflight: MovePreflight) -> ExecutionResult:
        operation = preflight.operation
        source_occupancy = self.occupancy.inspect(operation.source_path)
        if source_occupancy.status == OccupancyStatus.UNKNOWN:
            return ExecutionResult(operation.id, ExecutionOutcome.VERIFICATION_FAILED, source_occupancy.error)
        if source_occupancy.status != OccupancyStatus.FREE:
            return self._verification_failed(operation, "Source path still exists after move.", {"source": operation.source_path})
        destination_occupancy = self.occupancy.inspect(operation.destination_path)
        if destination_occupancy.status == OccupancyStatus.UNKNOWN:
            return ExecutionResult(operation.id, ExecutionOutcome.VERIFICATION_FAILED, destination_occupancy.error)
        if destination_occupancy.status != OccupancyStatus.OCCUPIED:
            return self._verification_failed(operation, "Destination path does not exist after move.", {"destination": operation.destination_path})
        destination_chain = self.chain_safety.classify_chain(operation.destination_path, preflight.plan.destination_root)
        if not destination_chain.allowed:
            return ExecutionResult(operation.id, ExecutionOutcome.VERIFICATION_FAILED, destination_chain.error)
        destination_identity = self._snapshot_required(operation.destination_path, ErrorCode.VERIFICATION_FAILED, "Destination identity could not be verified.")
        if destination_identity.error:
            return ExecutionResult(operation.id, ExecutionOutcome.VERIFICATION_FAILED, destination_identity.error)
        if destination_identity.snapshot.identity != preflight.source_identity_before.identity:
            return self._verification_failed(
                operation,
                "Destination identity does not match the pre-move source identity.",
                {
                    "source_identity": preflight.source_identity_before.identity.file_id,
                    "destination_identity": destination_identity.snapshot.identity.file_id,
                },
            )
        return ExecutionResult(operation.id, ExecutionOutcome.SUCCEEDED)

    def _final_pre_move_check(self, preflight: MovePreflight) -> StructuredError | None:
        operation = preflight.operation
        source_chain = self.chain_safety.classify_chain(operation.source_path, preflight.plan.source_root_normalized)
        if not source_chain.allowed:
            return source_chain.error
        destination_parent = ntpath.dirname(operation.destination_path)
        parent_chain = self.chain_safety.classify_chain(destination_parent, preflight.plan.destination_root)
        if not parent_chain.allowed:
            return parent_chain.error
        destination_policy = self.path_policy.classify(operation.destination_path, preflight.plan.destination_root)
        if not destination_policy.allowed:
            return destination_policy.error
        source_identity = self._snapshot_required(operation.source_path, ErrorCode.SOURCE_IDENTITY_CHANGED, "Source identity could not be verified immediately before move.")
        if source_identity.error:
            return source_identity.error
        if source_identity.snapshot != preflight.source_identity_before:
            return StructuredError(ErrorCode.SOURCE_IDENTITY_CHANGED, Severity.OPERATION_BLOCKING, "Source identity changed immediately before move.", {"operation_id": operation.id})
        destination_occupancy = self.occupancy.inspect(operation.destination_path)
        if not destination_occupancy.free:
            return destination_occupancy.error or StructuredError(ErrorCode.DESTINATION_EXISTS, Severity.OPERATION_BLOCKING, "Destination occupancy is not free.", {"destination": operation.destination_path})
        collision_error = self._case_collision_error(operation.destination_path)
        if collision_error is not None:
            return collision_error
        parent_identity = self._snapshot_required(destination_parent, ErrorCode.DESTINATION_PARENT_CHANGED, "Destination parent identity could not be verified immediately before move.")
        if parent_identity.error:
            return parent_identity.error
        if parent_identity.snapshot.identity != preflight.destination_parent_identity.identity:
            return StructuredError(ErrorCode.DESTINATION_PARENT_CHANGED, Severity.OPERATION_BLOCKING, "Destination parent changed immediately before move.", {"operation_id": operation.id})
        if parent_identity.snapshot.identity.volume_id != preflight.source_identity_before.identity.volume_id:
            return StructuredError(ErrorCode.CROSS_VOLUME_FAILURE, Severity.OPERATION_BLOCKING, "Source and destination are no longer on the same volume.", {"operation_id": operation.id})
        cloud = self.cloud_classifier.classify(operation.source_path)
        if not cloud.safe:
            return cloud.error
        return None

    def _entry_exists_for_revalidator(self, path: str) -> bool:
        occupancy = self.occupancy.inspect(path)
        if occupancy.status == OccupancyStatus.UNKNOWN:
            raise OSError(occupancy.error.message if occupancy.error else "unknown occupancy")
        return occupancy.status == OccupancyStatus.OCCUPIED

    def _parent_entries(self, destination_path: str) -> tuple[str, ...]:
        try:
            return self.directory_entries(ntpath.dirname(destination_path))
        except OSError:
            return ()

    def _case_collision_error(self, destination_path: str) -> StructuredError | None:
        try:
            entries = self.directory_entries(ntpath.dirname(destination_path))
        except OSError as exc:
            return StructuredError(
                ErrorCode.UNKNOWN_IO_ERROR,
                Severity.OPERATION_BLOCKING,
                "Could not inspect destination directory for collisions.",
                {"destination": destination_path, "error": str(exc)},
            )
        if self.path_policy.has_case_collision(destination_path, list(entries)):
            return StructuredError(
                ErrorCode.DESTINATION_COLLISION_CHANGED,
                Severity.OPERATION_BLOCKING,
                "A case-equivalent destination collision is present.",
                {"destination": destination_path},
            )
        return None

    def _filesystem_entries(self, parent: str) -> tuple[str, ...]:
        return tuple(str(child) for child in Path(parent).iterdir())

    def _snapshot_required(self, path: str, code: ErrorCode, message: str) -> IdentityResult:
        try:
            result = self.identity_provider.snapshot(path)
        except Exception as exc:
            return IdentityResult(None, StructuredError(code, Severity.OPERATION_BLOCKING, message, {"path": path, "error": str(exc)}))
        if not result.supported or result.snapshot is None:
            return IdentityResult(None, result.error or StructuredError(code, Severity.OPERATION_BLOCKING, message, {"path": path}))
        return result

    def _blocked(self, code: ErrorCode, message: str, operation: PlannedOperation, details: dict | None = None) -> MovePreflightResult:
        payload = {"operation_id": operation.id}
        payload.update(details or {})
        return MovePreflightResult(None, StructuredError(code, Severity.OPERATION_BLOCKING, message, payload))

    def _verification_failed(self, operation: PlannedOperation, message: str, details: dict) -> ExecutionResult:
        payload = {"operation_id": operation.id}
        payload.update(details)
        return ExecutionResult(operation.id, ExecutionOutcome.VERIFICATION_FAILED, StructuredError(ErrorCode.VERIFICATION_FAILED, Severity.RECOVERABLE, message, payload))

    def _single_operation_plan(self, plan: PreviewPlan, operation: PlannedOperation) -> PreviewPlan:
        from dataclasses import replace

        return replace(plan, operations=(operation,))


class SameVolumeMoveRecoveryInspector:
    def __init__(
        self,
        *,
        identity_provider: FileIdentityProvider | None = None,
        occupancy: DestinationOccupancyInspector | None = None,
    ):
        self.identity_provider = identity_provider or WindowsFileIdentityProvider()
        self.occupancy = occupancy or DestinationOccupancyInspector()

    def inspect(self, source_before: str, destination: str, identity_before_json: str) -> RecoveryInspection:
        expected_identity = _identity_snapshot_from_json(json.loads(identity_before_json)).identity
        source = self.occupancy.inspect(source_before)
        destination_state = self.occupancy.inspect(destination)
        if source.status == OccupancyStatus.UNKNOWN:
            return RecoveryInspection(RecoveryClassification.INSPECTION_BLOCKED, source.error)
        if destination_state.status == OccupancyStatus.UNKNOWN:
            return RecoveryInspection(RecoveryClassification.INSPECTION_BLOCKED, destination_state.error)
        if source.status == OccupancyStatus.OCCUPIED and destination_state.status == OccupancyStatus.FREE:
            return RecoveryInspection(RecoveryClassification.LIKELY_NOT_MOVED)
        if source.status == OccupancyStatus.FREE and destination_state.status == OccupancyStatus.OCCUPIED:
            destination_identity = self.identity_provider.snapshot(destination)
            if destination_identity.supported and destination_identity.snapshot and destination_identity.snapshot.identity == expected_identity:
                return RecoveryInspection(RecoveryClassification.LIKELY_COMPLETED)
            return RecoveryInspection(
                RecoveryClassification.CONFLICT_RECOVERY_REQUIRED,
                destination_identity.error
                or StructuredError(ErrorCode.RECOVERY_REQUIRED, Severity.RECOVERY, "Destination identity does not match interrupted source.", {"destination": destination}),
            )
        if source.status == OccupancyStatus.OCCUPIED and destination_state.status == OccupancyStatus.OCCUPIED:
            return RecoveryInspection(RecoveryClassification.CONFLICT_RECOVERY_REQUIRED)
        return RecoveryInspection(RecoveryClassification.MISSING_RECOVERY_REQUIRED)


def _identity_snapshot_from_json(payload: dict) -> IdentitySnapshot:
    identity = FileIdentity(
        volume_id=payload["identity"]["volume_id"],
        file_id=payload["identity"]["file_id"],
        file_type=payload["identity"]["file_type"],
        link_count=payload["identity"].get("link_count"),
    )
    metadata_payload = payload["metadata"]
    metadata = MetadataSnapshot(
        logical_path=metadata_payload["logical_path"],
        size=int(metadata_payload["size"]),
        mtime_ns=int(metadata_payload["mtime_ns"]),
        ctime_ns=int(metadata_payload["ctime_ns"]),
        attributes=metadata_payload.get("attributes"),
        reparse_tag=metadata_payload.get("reparse_tag"),
        reparse_kind=metadata_payload.get("reparse_kind"),
    )
    return IdentitySnapshot(identity, metadata)

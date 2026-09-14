from __future__ import annotations

from dataclasses import dataclass
from pathlib import PureWindowsPath

from ..models import (
    ConflictStatus,
    ErrorCode,
    PlannedOperation,
    PlannedOperationStatus,
    PreviewPlan,
    RevalidationReason,
    RevalidationResult,
    RevalidationStatus,
    SafetyDecision,
    SafetyReason,
    ScannedItem,
    ScannedItemKind,
    StructuredError,
)
from ..preview_workflow import PreviewAnalysis


READY = "READY"
BLOCKED = "BLOCKED"
UNSUPPORTED = "UNSUPPORTED"
COLLISION = "COLLISION"


@dataclass(frozen=True)
class PreviewRow:
    operation_id: str
    filename: str
    source: str
    destination: str
    category: str
    rule: str
    reason: str
    status: str
    safety_code: str
    collision_status: str
    detail: str


@dataclass(frozen=True)
class PreviewSummary:
    total_files: int
    ready: int
    blocked: int
    unsupported: int
    collisions: int
    skipped_subdirectories: int
    total_bytes: int


@dataclass(frozen=True)
class PreviewPresentation:
    rows: tuple[PreviewRow, ...]
    summary: PreviewSummary
    validation_message: str
    can_apply: bool


@dataclass(frozen=True)
class StalePresentation:
    status: str
    title: str
    message: str
    reasons: tuple[str, ...]


SAFETY_REASON_TEXT = {
    SafetyReason.OK: "This path passed FileFlow's current safety checks.",
    SafetyReason.EMPTY_PATH: "Select a folder before analysing.",
    SafetyReason.RELATIVE_PATH: "FileFlow needs a normal absolute Windows folder path.",
    SafetyReason.DRIVE_RELATIVE: "Drive-relative paths are not supported.",
    SafetyReason.FILESYSTEM_ROOT: "Filesystem roots cannot be analysed.",
    SafetyReason.UNC_PATH: "Network share paths are not supported in this milestone.",
    SafetyReason.EXTENDED_PATH: "Extended-length Windows paths are not supported yet.",
    SafetyReason.ADS_SYNTAX: "Alternate data stream syntax is not supported.",
    SafetyReason.TRAILING_DOT: "A path component ends with a period, which is unsafe on Windows.",
    SafetyReason.TRAILING_SPACE: "A path component ends with a space, which is unsafe on Windows.",
    SafetyReason.RESERVED_DEVICE_NAME: "A path component uses a reserved Windows device name.",
    SafetyReason.INVALID_NAME: "A path component contains characters Windows cannot safely handle.",
    SafetyReason.AMBIGUOUS_NORMALIZATION: "The path uses ambiguous Unicode or dot-segment normalization.",
    SafetyReason.UNSUPPORTED_LONG_PATH: "The path is longer than FileFlow supports in this milestone.",
    SafetyReason.PATH_ESCAPE: "The path would escape the selected folder.",
    SafetyReason.CASE_EQUIVALENT_COLLISION: "This destination collides under Windows case-insensitive matching.",
    SafetyReason.PROTECTED_ROOT: "This is a protected Windows or profile location.",
    SafetyReason.REPARSE_POINT: "This path contains a junction, symbolic link, or other redirected filesystem entry.",
    SafetyReason.REPARSE_INSPECTION_FAILED: "FileFlow could not prove this path is free of filesystem redirects.",
    SafetyReason.CLOUD_PLACEHOLDER: "This file is stored as a cloud placeholder and cannot be safely analysed yet.",
    SafetyReason.CLOUD_UNKNOWN: "FileFlow cannot prove the cloud or redirect state is safe.",
    SafetyReason.IDENTITY_UNAVAILABLE: "FileFlow could not establish a stable Windows identity for this item.",
    SafetyReason.SOURCE_MISSING: "The source file is no longer available.",
    SafetyReason.DESTINATION_EXISTS: "The planned destination already exists.",
    SafetyReason.DESTINATION_PARENT_MISSING: "The destination folder does not exist yet.",
    SafetyReason.DIRECTORY_SKIPPED: "Subdirectories are shown but not analysed in this milestone.",
    SafetyReason.NO_MATCHING_RULE: "No enabled rule matched this file.",
    SafetyReason.SCAN_FAILED: "FileFlow could not safely inspect this item.",
}

ERROR_TEXT = {
    ErrorCode.DESTINATION_APPEARED: "The destination changed after this preview was created.",
    ErrorCode.SOURCE_IDENTITY_CHANGED: "The source file changed after this preview was created.",
    ErrorCode.ROOT_IDENTITY_CHANGED: "The selected folder changed after this preview was created.",
    ErrorCode.REPARSE_POINT: "A redirected filesystem entry was detected.",
    ErrorCode.CLOUD_PLACEHOLDER: "A cloud placeholder cannot be safely analysed yet.",
    ErrorCode.DESTINATION_EXISTS: "The destination already exists.",
}

REVALIDATION_TEXT = {
    RevalidationReason.SOURCE_MISSING: "A source file is no longer available.",
    RevalidationReason.SOURCE_IDENTITY_CHANGED: "A source file changed after preview.",
    RevalidationReason.ROOT_IDENTITY_CHANGED: "The selected folder changed after preview.",
    RevalidationReason.DESTINATION_ROOT_IDENTITY_CHANGED: "The destination folder changed after preview.",
    RevalidationReason.RULE_SNAPSHOT_CHANGED: "Rules changed after preview.",
    RevalidationReason.CATEGORY_SNAPSHOT_CHANGED: "Categories changed after preview.",
    RevalidationReason.DESTINATION_APPEARED: "A planned destination appeared after preview.",
    RevalidationReason.DESTINATION_COLLISION_CHANGED: "A case-equivalent destination collision appeared after preview.",
    RevalidationReason.DESTINATION_REPARSE_CHANGED: "A destination became a redirected filesystem entry.",
    RevalidationReason.DESTINATION_OUTSIDE_ROOT: "A destination no longer stays inside the selected folder.",
    RevalidationReason.DESTINATION_PATH_POLICY_CHANGED: "A destination no longer passes path safety checks.",
    RevalidationReason.DESTINATION_PARENT_CHANGED: "A destination parent changed or became unsafe.",
    RevalidationReason.SAFETY_CLASSIFICATION_CHANGED: "Safety classification changed after preview.",
    RevalidationReason.CLOUD_CLASSIFICATION_CHANGED: "Cloud classification changed after preview.",
    RevalidationReason.REPARSE_STATE_CHANGED: "A filesystem redirect changed after preview.",
    RevalidationReason.SAFETY_POLICY_CHANGED: "FileFlow's safety policy changed after preview.",
}


def present_analysis(analysis: PreviewAnalysis) -> PreviewPresentation:
    if analysis.plan is None:
        message = safety_decision_text(analysis.validation.decision)
        return PreviewPresentation((), PreviewSummary(0, 0, 0, 0, 0, 0, 0), message, False)

    rows = tuple(present_operation(operation) for operation in analysis.plan.operations)
    summary = summarize(analysis.scanned_items, rows)
    message = safety_decision_text(analysis.validation.decision)
    return PreviewPresentation(rows, summary, message, False)


def present_operation(operation: PlannedOperation) -> PreviewRow:
    status = row_status(operation)
    error = operation.structured_error
    safety_code = error.code.value if error else operation.safety_status.value
    return PreviewRow(
        operation_id=operation.id,
        filename=PureWindowsPath(operation.source_path).name,
        source=operation.source_path,
        destination=operation.destination_path or "",
        category=operation.category_snapshot.name if operation.category_snapshot else "",
        rule=operation.rule_snapshot.name if operation.rule_snapshot else "",
        reason=operation.reason,
        status=status,
        safety_code=safety_code,
        collision_status=operation.conflict_status.value,
        detail=operation_detail(operation),
    )


def row_status(operation: PlannedOperation) -> str:
    if operation.conflict_status != ConflictStatus.NONE:
        return COLLISION
    if operation.safety_status == PlannedOperationStatus.PLANNED:
        return READY
    if operation.safety_status == PlannedOperationStatus.UNSUPPORTED:
        return UNSUPPORTED
    return BLOCKED


def operation_detail(operation: PlannedOperation) -> str:
    if operation.structured_error:
        return structured_error_text(operation.structured_error)
    if operation.safety_status == PlannedOperationStatus.PLANNED:
        return "Ready for preview only. FileFlow will not apply changes in this milestone."
    return operation.reason


def summarize(scanned_items: tuple[ScannedItem, ...], rows: tuple[PreviewRow, ...]) -> PreviewSummary:
    file_items = tuple(item for item in scanned_items if item.kind == ScannedItemKind.FILE)
    skipped_dirs = sum(
        1
        for item in scanned_items
        if item.kind == ScannedItemKind.DIRECTORY and item.safety.reason == SafetyReason.DIRECTORY_SKIPPED
    )
    total_bytes = sum(item.identity.metadata.size for item in file_items if item.identity is not None)
    return PreviewSummary(
        total_files=len(file_items),
        ready=sum(1 for row in rows if row.status == READY),
        blocked=sum(1 for row in rows if row.status == BLOCKED),
        unsupported=sum(1 for row in rows if row.status == UNSUPPORTED),
        collisions=sum(1 for row in rows if row.status == COLLISION),
        skipped_subdirectories=skipped_dirs,
        total_bytes=total_bytes,
    )


def present_revalidation(result: RevalidationResult) -> StalePresentation:
    if result.status == RevalidationStatus.VALID:
        return StalePresentation(
            result.status.value,
            "Preview is current",
            "This preview still matches the selected folder. Nothing has been applied.",
            (),
        )
    title = "Preview is out of date" if result.status == RevalidationStatus.STALE else "Preview is blocked"
    reasons = tuple(REVALIDATION_TEXT.get(reason, reason.value) for reason in result.reasons)
    return StalePresentation(
        result.status.value,
        title,
        "Analyse again to create a new preview. FileFlow will not silently regenerate it.",
        reasons,
    )


def safety_decision_text(decision: SafetyDecision) -> str:
    if decision.error:
        return structured_error_text(decision.error, fallback_reason=decision.reason)
    return SAFETY_REASON_TEXT.get(decision.reason, decision.reason.value)


def structured_error_text(error: StructuredError, fallback_reason: SafetyReason | None = None) -> str:
    if error.code in ERROR_TEXT:
        return ERROR_TEXT[error.code]
    if fallback_reason is not None and fallback_reason in SAFETY_REASON_TEXT:
        return SAFETY_REASON_TEXT[fallback_reason]
    return error.message


def format_bytes(value: int) -> str:
    units = ("B", "KB", "MB", "GB")
    size = float(value)
    for unit in units:
        if size < 1024 or unit == units[-1]:
            if unit == "B":
                return f"{int(size)} {unit}"
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{value} B"

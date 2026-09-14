from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any


class ErrorCode(str, Enum):
    SAFETY_BLOCK = "SAFETY_BLOCK"
    SOURCE_MISSING = "SOURCE_MISSING"
    DESTINATION_EXISTS = "DESTINATION_EXISTS"
    ACCESS_DENIED = "ACCESS_DENIED"
    LOCKED = "LOCKED"
    STALE_PLAN = "STALE_PLAN"
    REPARSE_POINT = "REPARSE_POINT"
    DISK_FULL = "DISK_FULL"
    CROSS_VOLUME_FAILURE = "CROSS_VOLUME_FAILURE"
    UNDO_CONFLICT = "UNDO_CONFLICT"
    PATH_TOO_LONG = "PATH_TOO_LONG"
    INVALID_NAME = "INVALID_NAME"
    RESERVED_NAME = "RESERVED_NAME"
    CASE_COLLISION = "CASE_COLLISION"
    DESTINATION_UNSAFE = "DESTINATION_UNSAFE"
    ROOT_UNSAFE = "ROOT_UNSAFE"
    RULE_CHANGED = "RULE_CHANGED"
    CATEGORY_CHANGED = "CATEGORY_CHANGED"
    METADATA_CHANGED = "METADATA_CHANGED"
    SOURCE_IDENTITY_CHANGED = "SOURCE_IDENTITY_CHANGED"
    ROOT_IDENTITY_CHANGED = "ROOT_IDENTITY_CHANGED"
    DESTINATION_APPEARED = "DESTINATION_APPEARED"
    DESTINATION_COLLISION_CHANGED = "DESTINATION_COLLISION_CHANGED"
    DESTINATION_REPARSE_CHANGED = "DESTINATION_REPARSE_CHANGED"
    DESTINATION_PARENT_CHANGED = "DESTINATION_PARENT_CHANGED"
    DESTINATION_PATH_POLICY_CHANGED = "DESTINATION_PATH_POLICY_CHANGED"
    CLOUD_PLACEHOLDER = "CLOUD_PLACEHOLDER"
    UNSUPPORTED = "UNSUPPORTED"
    INTERRUPTED = "INTERRUPTED"
    RECOVERY_REQUIRED = "RECOVERY_REQUIRED"
    UNKNOWN_IO_ERROR = "UNKNOWN_IO_ERROR"


class Severity(str, Enum):
    BLOCKING = "BLOCKING"
    OPERATION_BLOCKING = "OPERATION_BLOCKING"
    RECOVERABLE = "RECOVERABLE"
    WARNING = "WARNING"
    RECOVERY = "RECOVERY"


@dataclass(frozen=True)
class StructuredError:
    code: ErrorCode
    severity: Severity
    message: str
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["code"] = self.code.value
        payload["severity"] = self.severity.value
        return payload


class SafetyStatus(str, Enum):
    SAFE = "SAFE"
    SAFETY_BLOCK = "SAFETY_BLOCK"
    UNSUPPORTED = "UNSUPPORTED"


class SafetyReason(str, Enum):
    OK = "OK"
    EMPTY_PATH = "EMPTY_PATH"
    RELATIVE_PATH = "RELATIVE_PATH"
    DRIVE_RELATIVE = "DRIVE_RELATIVE"
    FILESYSTEM_ROOT = "FILESYSTEM_ROOT"
    UNC_PATH = "UNC_PATH"
    EXTENDED_PATH = "EXTENDED_PATH"
    ADS_SYNTAX = "ADS_SYNTAX"
    TRAILING_DOT = "TRAILING_DOT"
    TRAILING_SPACE = "TRAILING_SPACE"
    RESERVED_DEVICE_NAME = "RESERVED_DEVICE_NAME"
    INVALID_NAME = "INVALID_NAME"
    AMBIGUOUS_NORMALIZATION = "AMBIGUOUS_NORMALIZATION"
    UNSUPPORTED_LONG_PATH = "UNSUPPORTED_LONG_PATH"
    PATH_ESCAPE = "PATH_ESCAPE"
    CASE_EQUIVALENT_COLLISION = "CASE_EQUIVALENT_COLLISION"
    PROTECTED_ROOT = "PROTECTED_ROOT"
    REPARSE_POINT = "REPARSE_POINT"
    REPARSE_INSPECTION_FAILED = "REPARSE_INSPECTION_FAILED"
    CLOUD_PLACEHOLDER = "CLOUD_PLACEHOLDER"
    CLOUD_UNKNOWN = "CLOUD_UNKNOWN"
    IDENTITY_UNAVAILABLE = "IDENTITY_UNAVAILABLE"
    SOURCE_MISSING = "SOURCE_MISSING"
    DESTINATION_EXISTS = "DESTINATION_EXISTS"
    DIRECTORY_SKIPPED = "DIRECTORY_SKIPPED"
    NO_MATCHING_RULE = "NO_MATCHING_RULE"
    SCAN_FAILED = "SCAN_FAILED"


@dataclass(frozen=True)
class SafetyDecision:
    status: SafetyStatus
    reason: SafetyReason
    normalized_path: str | None = None
    error: StructuredError | None = None

    @property
    def allowed(self) -> bool:
        return self.status == SafetyStatus.SAFE

    @classmethod
    def safe(cls, normalized_path: str) -> "SafetyDecision":
        return cls(SafetyStatus.SAFE, SafetyReason.OK, normalized_path)

    @classmethod
    def block(
        cls,
        reason: SafetyReason,
        message: str,
        *,
        normalized_path: str | None = None,
        code: ErrorCode = ErrorCode.SAFETY_BLOCK,
        severity: Severity = Severity.OPERATION_BLOCKING,
        details: dict[str, Any] | None = None,
    ) -> "SafetyDecision":
        return cls(
            SafetyStatus.SAFETY_BLOCK,
            reason,
            normalized_path,
            StructuredError(code, severity, message, details or {}),
        )

    @classmethod
    def unsupported(
        cls,
        reason: SafetyReason,
        message: str,
        *,
        normalized_path: str | None = None,
        code: ErrorCode = ErrorCode.UNSUPPORTED,
        details: dict[str, Any] | None = None,
    ) -> "SafetyDecision":
        return cls(
            SafetyStatus.UNSUPPORTED,
            reason,
            normalized_path,
            StructuredError(code, Severity.OPERATION_BLOCKING, message, details or {}),
        )


@dataclass(frozen=True)
class LogicalPath:
    raw_path: str
    normalized_path: str


@dataclass(frozen=True)
class FileIdentity:
    volume_id: str
    file_id: str
    file_type: str
    link_count: int | None


@dataclass(frozen=True)
class MetadataSnapshot:
    logical_path: str
    size: int
    mtime_ns: int
    ctime_ns: int
    attributes: int | None = None
    reparse_tag: int | None = None
    reparse_kind: str | None = None


@dataclass(frozen=True)
class IdentitySnapshot:
    identity: FileIdentity
    metadata: MetadataSnapshot


@dataclass(frozen=True)
class Category:
    id: str
    name: str
    destination_folder: str
    extensions: tuple[str, ...]
    enabled: bool
    sort_order: int
    is_builtin: bool
    version: int


@dataclass(frozen=True)
class CategorySnapshot:
    id: str
    name: str
    destination_folder: str
    extensions: tuple[str, ...]
    enabled: bool
    sort_order: int
    is_builtin: bool
    version: int

    @classmethod
    def from_category(cls, category: Category) -> "CategorySnapshot":
        return cls(
            category.id,
            category.name,
            category.destination_folder,
            category.extensions,
            category.enabled,
            category.sort_order,
            category.is_builtin,
            category.version,
        )


@dataclass(frozen=True)
class Rule:
    id: str
    name: str
    category_id: str
    destination_folder: str
    enabled: bool
    priority: int
    sort_order: int
    version: int
    extensions: tuple[str, ...] = ()
    filename_contains: str | None = None
    filename_startswith: str | None = None
    filename_endswith: str | None = None
    min_size: int | None = None
    max_size: int | None = None
    source_subfolder: str | None = None


@dataclass(frozen=True)
class RuleSnapshot:
    id: str
    name: str
    category_id: str
    destination_folder: str
    enabled: bool
    priority: int
    sort_order: int
    version: int
    extensions: tuple[str, ...] = ()
    filename_contains: str | None = None
    filename_startswith: str | None = None
    filename_endswith: str | None = None
    min_size: int | None = None
    max_size: int | None = None
    source_subfolder: str | None = None

    @classmethod
    def from_rule(cls, rule: Rule) -> "RuleSnapshot":
        return cls(
            rule.id,
            rule.name,
            rule.category_id,
            rule.destination_folder,
            rule.enabled,
            rule.priority,
            rule.sort_order,
            rule.version,
            rule.extensions,
            rule.filename_contains,
            rule.filename_startswith,
            rule.filename_endswith,
            rule.min_size,
            rule.max_size,
            rule.source_subfolder,
        )


@dataclass(frozen=True)
class RuleMatch:
    rule: Rule
    reason: str


class ScannedItemKind(str, Enum):
    FILE = "FILE"
    DIRECTORY = "DIRECTORY"


@dataclass(frozen=True)
class ScannedItem:
    path: str
    relative_path: str
    kind: ScannedItemKind
    safety: SafetyDecision
    identity: IdentitySnapshot | None = None


class OperationIntent(str, Enum):
    MOVE = "MOVE"


class PlannedOperationStatus(str, Enum):
    PLANNED = "PLANNED"
    BLOCKED = "BLOCKED"
    UNSUPPORTED = "UNSUPPORTED"


class ConflictStatus(str, Enum):
    NONE = "NONE"
    DESTINATION_EXISTS = "DESTINATION_EXISTS"
    CASE_EQUIVALENT_COLLISION = "CASE_EQUIVALENT_COLLISION"


@dataclass(frozen=True)
class PlannedOperation:
    id: str
    operation_type: OperationIntent
    source_path: str
    destination_path: str | None
    source_root: str
    rule_snapshot: RuleSnapshot | None
    category_snapshot: CategorySnapshot | None
    reason: str
    safety_status: PlannedOperationStatus
    conflict_status: ConflictStatus
    reversible: bool
    source_identity: IdentitySnapshot | None
    preview_index: int
    structured_error: StructuredError | None = None


class PlanStatus(str, Enum):
    PLANNED = "PLANNED"
    APPROVED = "APPROVED"
    STALE = "STALE"
    BLOCKED = "BLOCKED"


@dataclass(frozen=True)
class PreviewPlan:
    id: str
    profile_id: str
    source_root: str
    source_root_normalized: str
    source_root_identity: IdentitySnapshot
    destination_root: str
    destination_root_identity: IdentitySnapshot
    status: PlanStatus
    rule_set_version: int
    category_version: int
    safety_policy_version: int
    operations: tuple[PlannedOperation, ...]
    rule_snapshots: tuple[RuleSnapshot, ...]
    category_snapshots: tuple[CategorySnapshot, ...]
    created_at: str


class JournalState(str, Enum):
    PLANNED = "PLANNED"
    APPROVED = "APPROVED"
    INTENT_RECORDED = "INTENT_RECORDED"
    IN_PROGRESS = "IN_PROGRESS"
    VERIFYING = "VERIFYING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    BLOCKED = "BLOCKED"
    INTERRUPTED = "INTERRUPTED"
    RECOVERY_REQUIRED = "RECOVERY_REQUIRED"


class ExecutionOutcome(str, Enum):
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    INTERRUPTED = "INTERRUPTED"
    VERIFICATION_FAILED = "VERIFICATION_FAILED"


@dataclass(frozen=True)
class ExecutionResult:
    operation_id: str
    outcome: ExecutionOutcome
    error: StructuredError | None = None


@dataclass(frozen=True)
class RevalidationResult:
    status: "RevalidationStatus"
    reasons: tuple["RevalidationReason", ...] = ()
    errors: tuple[StructuredError, ...] = ()

    @property
    def valid(self) -> bool:
        return self.status == RevalidationStatus.VALID


class RevalidationStatus(str, Enum):
    VALID = "VALID"
    STALE = "STALE"
    BLOCKED = "BLOCKED"


class RevalidationReason(str, Enum):
    SOURCE_MISSING = "SOURCE_MISSING"
    SOURCE_IDENTITY_CHANGED = "SOURCE_IDENTITY_CHANGED"
    ROOT_IDENTITY_CHANGED = "ROOT_IDENTITY_CHANGED"
    DESTINATION_ROOT_IDENTITY_CHANGED = "DESTINATION_ROOT_IDENTITY_CHANGED"
    RULE_SNAPSHOT_CHANGED = "RULE_SNAPSHOT_CHANGED"
    CATEGORY_SNAPSHOT_CHANGED = "CATEGORY_SNAPSHOT_CHANGED"
    DESTINATION_APPEARED = "DESTINATION_APPEARED"
    DESTINATION_COLLISION_CHANGED = "DESTINATION_COLLISION_CHANGED"
    DESTINATION_REPARSE_CHANGED = "DESTINATION_REPARSE_CHANGED"
    DESTINATION_OUTSIDE_ROOT = "DESTINATION_OUTSIDE_ROOT"
    DESTINATION_PATH_POLICY_CHANGED = "DESTINATION_PATH_POLICY_CHANGED"
    DESTINATION_PARENT_CHANGED = "DESTINATION_PARENT_CHANGED"
    SAFETY_CLASSIFICATION_CHANGED = "SAFETY_CLASSIFICATION_CHANGED"
    CLOUD_CLASSIFICATION_CHANGED = "CLOUD_CLASSIFICATION_CHANGED"
    REPARSE_STATE_CHANGED = "REPARSE_STATE_CHANGED"
    SAFETY_POLICY_CHANGED = "SAFETY_POLICY_CHANGED"


def dataclass_to_jsonable(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if hasattr(value, "__dataclass_fields__"):
        return {k: dataclass_to_jsonable(v) for k, v in asdict(value).items()}
    if isinstance(value, tuple):
        return [dataclass_to_jsonable(v) for v in value]
    if isinstance(value, list):
        return [dataclass_to_jsonable(v) for v in value]
    if isinstance(value, dict):
        return {k: dataclass_to_jsonable(v) for k, v in value.items()}
    return value

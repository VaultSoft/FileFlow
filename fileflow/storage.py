from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from .models import (
    CategorySnapshot,
    ConflictStatus,
    ErrorCode,
    FileIdentity,
    IdentitySnapshot,
    JournalState,
    MetadataSnapshot,
    OperationIntent,
    PlanStatus,
    PlannedOperation,
    PlannedOperationStatus,
    PreviewPlan,
    RuleSnapshot,
    Severity,
    StructuredError,
    dataclass_to_jsonable,
)


MIGRATIONS: tuple[tuple[int, str, str], ...] = (
    (
        1,
        "initial_non_destructive_core",
        """
        CREATE TABLE IF NOT EXISTS app_metadata (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS preview_plan (
            id TEXT PRIMARY KEY,
            profile_id TEXT NOT NULL,
            source_root TEXT NOT NULL,
            source_root_normalized TEXT NOT NULL,
            source_root_identity_json TEXT NOT NULL,
            destination_root TEXT NOT NULL,
            status TEXT NOT NULL,
            rule_set_version INTEGER NOT NULL,
            category_version INTEGER NOT NULL,
            safety_policy_version INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            approved_at TEXT,
            summary_json TEXT NOT NULL,
            snapshot_json TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS planned_operation (
            id TEXT PRIMARY KEY,
            plan_id TEXT NOT NULL,
            operation_type TEXT NOT NULL,
            source_path TEXT NOT NULL,
            destination_path TEXT,
            source_root TEXT NOT NULL,
            rule_id TEXT,
            category_id TEXT,
            reason TEXT NOT NULL,
            safety_status TEXT NOT NULL,
            conflict_status TEXT NOT NULL,
            reversible INTEGER NOT NULL,
            metadata_json TEXT NOT NULL,
            identity_json TEXT NOT NULL,
            preview_index INTEGER NOT NULL,
            operation_json TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS operation_batch (
            id TEXT PRIMARY KEY,
            plan_id TEXT NOT NULL,
            profile_id TEXT NOT NULL,
            status TEXT NOT NULL,
            approved_at TEXT NOT NULL,
            started_at TEXT,
            completed_at TEXT,
            app_version TEXT NOT NULL,
            summary_json TEXT NOT NULL,
            recovery_required INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS executed_operation (
            id TEXT PRIMARY KEY,
            batch_id TEXT NOT NULL,
            planned_operation_id TEXT NOT NULL,
            operation_type TEXT NOT NULL,
            source_before TEXT NOT NULL,
            destination TEXT,
            result TEXT NOT NULL,
            error_code TEXT,
            error_detail TEXT,
            metadata_before_json TEXT NOT NULL,
            metadata_after_json TEXT,
            identity_before_json TEXT,
            identity_after_json TEXT,
            undo_eligible INTEGER NOT NULL,
            undo_status TEXT NOT NULL,
            started_at TEXT NOT NULL,
            completed_at TEXT
        );
        CREATE TABLE IF NOT EXISTS operation_state_event (
            id TEXT PRIMARY KEY,
            executed_operation_id TEXT NOT NULL,
            batch_id TEXT NOT NULL,
            planned_operation_id TEXT NOT NULL,
            state TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS error_event (
            id TEXT PRIMARY KEY,
            scope TEXT NOT NULL,
            scope_id TEXT NOT NULL,
            code TEXT NOT NULL,
            severity TEXT NOT NULL,
            message TEXT NOT NULL,
            details_json TEXT,
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_preview_plan_status_created ON preview_plan(status, created_at);
        CREATE INDEX IF NOT EXISTS idx_planned_operation_plan_index ON planned_operation(plan_id, preview_index);
        CREATE INDEX IF NOT EXISTS idx_planned_operation_status ON planned_operation(plan_id, safety_status);
        CREATE INDEX IF NOT EXISTS idx_executed_operation_batch_result ON executed_operation(batch_id, result);
        CREATE INDEX IF NOT EXISTS idx_operation_state_event_execution ON operation_state_event(executed_operation_id, created_at);
        CREATE INDEX IF NOT EXISTS idx_operation_state_event_operation ON operation_state_event(planned_operation_id, created_at);
        CREATE INDEX IF NOT EXISTS idx_error_event_scope ON error_event(scope, scope_id);
        """,
    ),
    (
        2,
        "execution_lock",
        """
        CREATE TABLE IF NOT EXISTS execution_lock (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            owner TEXT NOT NULL,
            acquired_at TEXT NOT NULL
        );
        """,
    ),
    (
        3,
        "execution_lock_process_identity",
        """
        ALTER TABLE execution_lock ADD COLUMN process_id INTEGER;
        ALTER TABLE execution_lock ADD COLUMN process_started_at TEXT;
        """,
    ),
    (
        4,
        "safe_undo_journal",
        """
        CREATE TABLE IF NOT EXISTS undo_plan (
            id TEXT PRIMARY KEY,
            original_batch_id TEXT NOT NULL,
            original_plan_id TEXT NOT NULL,
            status TEXT NOT NULL,
            safety_policy_version INTEGER NOT NULL,
            behavior_snapshot_json TEXT NOT NULL,
            summary_json TEXT NOT NULL,
            snapshot_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            first_revalidated_at TEXT,
            second_revalidated_at TEXT
        );
        CREATE TABLE IF NOT EXISTS undo_planned_operation (
            id TEXT PRIMARY KEY,
            undo_plan_id TEXT NOT NULL,
            original_plan_id TEXT NOT NULL,
            original_batch_id TEXT NOT NULL,
            original_planned_operation_id TEXT NOT NULL,
            original_execution_id TEXT NOT NULL,
            source_path TEXT NOT NULL,
            restore_path TEXT NOT NULL,
            expected_identity_json TEXT NOT NULL,
            preview_metadata_json TEXT NOT NULL,
            status TEXT NOT NULL,
            reason_code TEXT,
            reason_detail TEXT,
            snapshot_json TEXT NOT NULL,
            preview_index INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS undo_batch (
            id TEXT PRIMARY KEY,
            undo_plan_id TEXT NOT NULL,
            original_batch_id TEXT NOT NULL,
            status TEXT NOT NULL,
            approved_at TEXT NOT NULL,
            started_at TEXT,
            completed_at TEXT,
            app_version TEXT NOT NULL,
            summary_json TEXT NOT NULL,
            recovery_required INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS undo_execution (
            id TEXT PRIMARY KEY,
            undo_batch_id TEXT NOT NULL,
            undo_planned_operation_id TEXT NOT NULL,
            original_execution_id TEXT NOT NULL,
            source_before TEXT NOT NULL,
            restore_destination TEXT NOT NULL,
            expected_identity_json TEXT NOT NULL,
            identity_before_json TEXT NOT NULL,
            identity_after_json TEXT,
            metadata_before_json TEXT NOT NULL,
            metadata_after_json TEXT,
            state TEXT NOT NULL,
            error_code TEXT,
            error_detail TEXT,
            started_at TEXT NOT NULL,
            completed_at TEXT
        );
        CREATE TABLE IF NOT EXISTS undo_state_event (
            id TEXT PRIMARY KEY,
            undo_execution_id TEXT NOT NULL,
            undo_batch_id TEXT NOT NULL,
            state TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_undo_plan_original_batch ON undo_plan(original_batch_id, created_at);
        CREATE INDEX IF NOT EXISTS idx_undo_plan_status ON undo_plan(status, created_at);
        CREATE INDEX IF NOT EXISTS idx_undo_planned_operation_plan ON undo_planned_operation(undo_plan_id, preview_index);
        CREATE INDEX IF NOT EXISTS idx_undo_planned_operation_original ON undo_planned_operation(original_execution_id);
        CREATE INDEX IF NOT EXISTS idx_undo_planned_operation_status ON undo_planned_operation(undo_plan_id, status);
        CREATE INDEX IF NOT EXISTS idx_undo_batch_original ON undo_batch(original_batch_id, approved_at);
        CREATE INDEX IF NOT EXISTS idx_undo_batch_status ON undo_batch(status, started_at);
        CREATE INDEX IF NOT EXISTS idx_undo_execution_batch ON undo_execution(undo_batch_id, state);
        CREATE INDEX IF NOT EXISTS idx_undo_execution_original ON undo_execution(original_execution_id, started_at);
        CREATE INDEX IF NOT EXISTS idx_undo_execution_state ON undo_execution(state, started_at);
        CREATE INDEX IF NOT EXISTS idx_undo_state_event_execution ON undo_state_event(undo_execution_id, created_at);
        CREATE UNIQUE INDEX IF NOT EXISTS idx_undo_execution_one_success
            ON undo_execution(original_execution_id) WHERE state = 'SUCCEEDED';
        CREATE UNIQUE INDEX IF NOT EXISTS idx_undo_execution_one_active
            ON undo_execution(original_execution_id)
            WHERE state IN ('INTENT_RECORDED', 'IN_PROGRESS', 'VERIFYING', 'INTERRUPTED', 'RECOVERY_REQUIRED');
        """,
    ),
)


class Database:
    def __init__(self, path: str | Path):
        self.path = str(path)
        self.connection = sqlite3.connect(self.path)
        self.connection.row_factory = sqlite3.Row

    def close(self) -> None:
        self.connection.close()

    def migrate(self) -> None:
        self.connection.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY, name TEXT NOT NULL, applied_at TEXT NOT NULL)"
        )
        applied = {row["version"] for row in self.connection.execute("SELECT version FROM schema_migrations")}
        for version, name, sql in MIGRATIONS:
            if version in applied:
                continue
            with self.connection:
                self.connection.executescript(sql)
                self.connection.execute(
                    "INSERT INTO schema_migrations(version, name, applied_at) VALUES (?, ?, ?)",
                    (version, name, datetime.now(timezone.utc).isoformat()),
                )


class PlanRepository:
    def __init__(self, database: Database):
        self.database = database

    def save_plan(self, plan: PreviewPlan) -> None:
        summary = {
            "operation_count": len(plan.operations),
            "blocked_count": sum(1 for operation in plan.operations if operation.safety_status.value != "PLANNED"),
        }
        with self.database.connection:
            self.database.connection.execute(
                """
                INSERT INTO preview_plan(
                    id, profile_id, source_root, source_root_normalized, source_root_identity_json,
                    destination_root, status, rule_set_version, category_version, safety_policy_version,
                    created_at, approved_at, summary_json, snapshot_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    plan.id,
                    plan.profile_id,
                    plan.source_root,
                    plan.source_root_normalized,
                    json.dumps(dataclass_to_jsonable(plan.source_root_identity), sort_keys=True),
                    plan.destination_root,
                    plan.status.value,
                    plan.rule_set_version,
                    plan.category_version,
                    plan.safety_policy_version,
                    plan.created_at,
                    None,
                    json.dumps(summary, sort_keys=True),
                    json.dumps(dataclass_to_jsonable(plan), sort_keys=True),
                ),
            )
            for operation in plan.operations:
                self.save_operation(plan.id, operation)

    def save_operation(self, plan_id: str, operation: PlannedOperation) -> None:
        identity_json = json.dumps(dataclass_to_jsonable(operation.source_identity), sort_keys=True)
        metadata_json = "{}"
        if operation.source_identity is not None:
            metadata_json = json.dumps(dataclass_to_jsonable(operation.source_identity.metadata), sort_keys=True)
        self.database.connection.execute(
            """
            INSERT INTO planned_operation(
                id, plan_id, operation_type, source_path, destination_path, source_root,
                rule_id, category_id, reason, safety_status, conflict_status, reversible,
                metadata_json, identity_json, preview_index, operation_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                operation.id,
                plan_id,
                operation.operation_type.value,
                operation.source_path,
                operation.destination_path,
                operation.source_root,
                operation.rule_snapshot.id if operation.rule_snapshot else None,
                operation.category_snapshot.id if operation.category_snapshot else None,
                operation.reason,
                operation.safety_status.value,
                operation.conflict_status.value,
                1 if operation.reversible else 0,
                metadata_json,
                identity_json,
                operation.preview_index,
                json.dumps(dataclass_to_jsonable(operation), sort_keys=True),
            ),
        )

    def load_plan(self, plan_id: str) -> PreviewPlan | None:
        row = self.database.connection.execute(
            "SELECT snapshot_json FROM preview_plan WHERE id = ?",
            (plan_id,),
        ).fetchone()
        if row is None:
            return None
        return _preview_plan_from_json(json.loads(row["snapshot_json"]))

    def plan_count(self) -> int:
        row = self.database.connection.execute("SELECT COUNT(*) AS count FROM preview_plan").fetchone()
        return int(row["count"])

    def operation_count(self) -> int:
        row = self.database.connection.execute("SELECT COUNT(*) AS count FROM planned_operation").fetchone()
        return int(row["count"])


class ErrorRepository:
    def __init__(self, database: Database):
        self.database = database

    def record(self, *, event_id: str, scope: str, scope_id: str, error: StructuredError) -> None:
        with self.database.connection:
            self.database.connection.execute(
                """
                INSERT INTO error_event(id, scope, scope_id, code, severity, message, details_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event_id,
                    scope,
                    scope_id,
                    error.code.value,
                    error.severity.value,
                    error.message,
                    json.dumps(error.details, sort_keys=True),
                    datetime.now(timezone.utc).isoformat(),
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


def _file_identity_from_json(payload: dict) -> FileIdentity:
    return FileIdentity(
        volume_id=payload["volume_id"],
        file_id=payload["file_id"],
        file_type=payload["file_type"],
        link_count=payload.get("link_count"),
    )


def _metadata_snapshot_from_json(payload: dict) -> MetadataSnapshot:
    return MetadataSnapshot(
        logical_path=payload["logical_path"],
        size=int(payload["size"]),
        mtime_ns=int(payload["mtime_ns"]),
        ctime_ns=int(payload["ctime_ns"]),
        attributes=payload.get("attributes"),
        reparse_tag=payload.get("reparse_tag"),
        reparse_kind=payload.get("reparse_kind"),
    )


def _identity_snapshot_from_json(payload: dict | None) -> IdentitySnapshot | None:
    if payload is None:
        return None
    return IdentitySnapshot(
        identity=_file_identity_from_json(payload["identity"]),
        metadata=_metadata_snapshot_from_json(payload["metadata"]),
    )


def _rule_snapshot_from_json(payload: dict | None) -> RuleSnapshot | None:
    if payload is None:
        return None
    return RuleSnapshot(
        id=payload["id"],
        name=payload["name"],
        category_id=payload["category_id"],
        destination_folder=payload["destination_folder"],
        enabled=bool(payload["enabled"]),
        priority=int(payload["priority"]),
        sort_order=int(payload["sort_order"]),
        version=int(payload["version"]),
        extensions=tuple(payload.get("extensions", ())),
        filename_contains=payload.get("filename_contains"),
        filename_startswith=payload.get("filename_startswith"),
        filename_endswith=payload.get("filename_endswith"),
        min_size=payload.get("min_size"),
        max_size=payload.get("max_size"),
        source_subfolder=payload.get("source_subfolder"),
    )


def _category_snapshot_from_json(payload: dict | None) -> CategorySnapshot | None:
    if payload is None:
        return None
    return CategorySnapshot(
        id=payload["id"],
        name=payload["name"],
        destination_folder=payload["destination_folder"],
        extensions=tuple(payload.get("extensions", ())),
        enabled=bool(payload["enabled"]),
        sort_order=int(payload["sort_order"]),
        is_builtin=bool(payload["is_builtin"]),
        version=int(payload["version"]),
    )


def _planned_operation_from_json(payload: dict) -> PlannedOperation:
    return PlannedOperation(
        id=payload["id"],
        operation_type=OperationIntent(payload["operation_type"]),
        source_path=payload["source_path"],
        destination_path=payload.get("destination_path"),
        source_root=payload["source_root"],
        rule_snapshot=_rule_snapshot_from_json(payload.get("rule_snapshot")),
        category_snapshot=_category_snapshot_from_json(payload.get("category_snapshot")),
        reason=payload["reason"],
        safety_status=PlannedOperationStatus(payload["safety_status"]),
        conflict_status=ConflictStatus(payload["conflict_status"]),
        reversible=bool(payload["reversible"]),
        source_identity=_identity_snapshot_from_json(payload.get("source_identity")),
        preview_index=int(payload["preview_index"]),
        structured_error=_structured_error_from_json(payload.get("structured_error")),
    )


def _preview_plan_from_json(payload: dict) -> PreviewPlan:
    return PreviewPlan(
        id=payload["id"],
        profile_id=payload["profile_id"],
        source_root=payload["source_root"],
        source_root_normalized=payload["source_root_normalized"],
        source_root_identity=_identity_snapshot_from_json(payload["source_root_identity"]),
        destination_root=payload["destination_root"],
        destination_root_identity=_identity_snapshot_from_json(payload["destination_root_identity"]),
        status=PlanStatus(payload["status"]),
        rule_set_version=int(payload["rule_set_version"]),
        category_version=int(payload["category_version"]),
        safety_policy_version=int(payload["safety_policy_version"]),
        operations=tuple(_planned_operation_from_json(operation) for operation in payload.get("operations", ())),
        rule_snapshots=tuple(_rule_snapshot_from_json(rule) for rule in payload.get("rule_snapshots", ())),
        category_snapshots=tuple(_category_snapshot_from_json(category) for category in payload.get("category_snapshots", ())),
        created_at=payload["created_at"],
    )

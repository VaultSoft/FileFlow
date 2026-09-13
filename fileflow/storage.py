from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from .models import PlannedOperation, PreviewPlan, StructuredError, dataclass_to_jsonable


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
            summary_json TEXT NOT NULL
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
            preview_index INTEGER NOT NULL
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
        CREATE INDEX IF NOT EXISTS idx_error_event_scope ON error_event(scope, scope_id);
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
                    created_at, approved_at, summary_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                metadata_json, identity_json, preview_index
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
            ),
        )

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

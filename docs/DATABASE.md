# Database Design

FileFlow should use SQLite from day one for previews, history, operation results, rules, and undo state.

The database lives under the per-user VaultSoft data root, for example `%LOCALAPPDATA%\VaultSoft\FileFlow\fileflow.db`.

## Migration Approach

Use a `schema_migrations` table:

- `version INTEGER PRIMARY KEY`
- `name TEXT NOT NULL`
- `applied_at TEXT NOT NULL`

Application startup checks the current schema version and applies forward-only migrations. Failed migrations abort startup with a clear error.

## Crash-Aware State Model

Use coherent operation states across plan, batch, execution, and undo tables:

- `PLANNED`
- `APPROVED`
- `INTENT_RECORDED`
- `IN_PROGRESS`
- `VERIFYING`
- `SUCCEEDED`
- `FAILED`
- `BLOCKED`
- `INTERRUPTED`
- `RECOVERY_REQUIRED`

Undo uses corresponding `UNDO_*` states.

SQLite and filesystem operations cannot be atomic together. Future execution must use this journal ordering:

1. validate plan
2. record operation intent
3. commit journal state
4. perform filesystem operation
5. verify result
6. record final result
7. commit result

Startup recovery: any operation or batch left in `INTENT_RECORDED`, `IN_PROGRESS`, or `VERIFYING` becomes `RECOVERY_REQUIRED`. FileFlow must inspect filesystem identity/state before deciding whether it succeeded, failed, or needs user action.

## Proposed Tables

### app_metadata

- `key TEXT PRIMARY KEY`
- `value TEXT NOT NULL`

Stores safety policy version, current rule set version, and app metadata.

### profile

- `id TEXT PRIMARY KEY`
- `name TEXT NOT NULL`
- `created_at TEXT NOT NULL`
- `updated_at TEXT NOT NULL`
- `is_default INTEGER NOT NULL`

Profiles are future-friendly but can start with one default profile.

### category

- `id TEXT PRIMARY KEY`
- `profile_id TEXT NOT NULL`
- `name TEXT NOT NULL`
- `destination_folder TEXT NOT NULL`
- `extensions_json TEXT NOT NULL`
- `enabled INTEGER NOT NULL`
- `sort_order INTEGER NOT NULL`
- `is_builtin INTEGER NOT NULL`
- `version INTEGER NOT NULL`

Index: `(profile_id, enabled, sort_order)`.

### rule

- `id TEXT PRIMARY KEY`
- `profile_id TEXT NOT NULL`
- `name TEXT NOT NULL`
- `rule_type TEXT NOT NULL`
- `conditions_json TEXT NOT NULL`
- `destination_template TEXT NOT NULL`
- `category_id TEXT`
- `enabled INTEGER NOT NULL`
- `priority INTEGER NOT NULL`
- `version INTEGER NOT NULL`
- `created_at TEXT NOT NULL`
- `updated_at TEXT NOT NULL`

Index: `(profile_id, enabled, priority)`.

### preview_plan

- `id TEXT PRIMARY KEY`
- `profile_id TEXT NOT NULL`
- `source_root TEXT NOT NULL`
- `source_root_normalized TEXT NOT NULL`
- `source_root_identity_json TEXT NOT NULL`
- `destination_root TEXT`
- `status TEXT NOT NULL`
- `rule_set_version INTEGER NOT NULL`
- `category_version INTEGER NOT NULL`
- `safety_policy_version INTEGER NOT NULL`
- `created_at TEXT NOT NULL`
- `approved_at TEXT`
- `summary_json TEXT NOT NULL`

Indexes: `(status, created_at)`, `(profile_id, created_at)`.

### planned_operation

- `id TEXT PRIMARY KEY`
- `plan_id TEXT NOT NULL`
- `operation_type TEXT NOT NULL`
- `source_path TEXT NOT NULL`
- `destination_path TEXT NOT NULL`
- `source_root TEXT NOT NULL`
- `rule_id TEXT`
- `category_id TEXT`
- `reason TEXT NOT NULL`
- `safety_status TEXT NOT NULL`
- `conflict_status TEXT NOT NULL`
- `reversible INTEGER NOT NULL`
- `metadata_json TEXT NOT NULL`
- `identity_json TEXT NOT NULL`
- `preview_index INTEGER NOT NULL`

Indexes: `(plan_id, preview_index)`, `(plan_id, safety_status)`, `(plan_id, conflict_status)`.

### operation_batch

- `id TEXT PRIMARY KEY`
- `plan_id TEXT NOT NULL`
- `profile_id TEXT NOT NULL`
- `status TEXT NOT NULL`
- `approved_at TEXT NOT NULL`
- `started_at TEXT`
- `completed_at TEXT`
- `app_version TEXT NOT NULL`
- `summary_json TEXT NOT NULL`
- `recovery_required INTEGER NOT NULL DEFAULT 0`

Indexes: `(status, started_at)`, `(profile_id, started_at)`.

### executed_operation

- `id TEXT PRIMARY KEY`
- `batch_id TEXT NOT NULL`
- `planned_operation_id TEXT NOT NULL`
- `operation_type TEXT NOT NULL`
- `source_before TEXT NOT NULL`
- `destination TEXT NOT NULL`
- `result TEXT NOT NULL`
- `error_code TEXT`
- `error_detail TEXT`
- `metadata_before_json TEXT NOT NULL`
- `metadata_after_json TEXT`
- `identity_before_json TEXT`
- `identity_after_json TEXT`
- `undo_eligible INTEGER NOT NULL`
- `undo_status TEXT NOT NULL`
- `started_at TEXT NOT NULL`
- `completed_at TEXT`

Indexes: `(batch_id, result)`, `(undo_status, completed_at)`.

### undo_attempt

- `id TEXT PRIMARY KEY`
- `batch_id TEXT NOT NULL`
- `status TEXT NOT NULL`
- `started_at TEXT NOT NULL`
- `completed_at TEXT`
- `summary_json TEXT NOT NULL`

### undo_operation

- `id TEXT PRIMARY KEY`
- `undo_attempt_id TEXT NOT NULL`
- `executed_operation_id TEXT NOT NULL`
- `source_path TEXT NOT NULL`
- `restore_path TEXT NOT NULL`
- `result TEXT NOT NULL`
- `error_code TEXT`
- `error_detail TEXT`

Index: `(undo_attempt_id, result)`.

### error_event

- `id TEXT PRIMARY KEY`
- `scope TEXT NOT NULL`
- `scope_id TEXT NOT NULL`
- `code TEXT NOT NULL`
- `severity TEXT NOT NULL`
- `message TEXT NOT NULL`
- `details_json TEXT`
- `created_at TEXT NOT NULL`

Index: `(scope, scope_id)`.

### duplicate_candidate

Future/pro-capable table. Not used by initial implementation.

- `id TEXT PRIMARY KEY`
- `profile_id TEXT NOT NULL`
- `path TEXT NOT NULL`
- `size INTEGER NOT NULL`
- `partial_hash TEXT`
- `full_hash TEXT`
- `status TEXT NOT NULL`
- `created_at TEXT NOT NULL`

Index: `(profile_id, size)`, `(profile_id, full_hash)`.

## Notes

Use JSON columns for metadata snapshots where SQLite's relational shape would add complexity without improving safety. Keep high-level status fields indexed and queryable.

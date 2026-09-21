# Undo Journal

Undo history is append-only and separate from Apply history. Original
`preview_plan`, `planned_operation`, `operation_batch`, `executed_operation`,
and state-event rows are immutable evidence. Undo must never rewrite them.

The existing `executed_operation.undo_eligible` and `undo_status` columns are
legacy placeholders from the initial schema. Undo code does not update or trust
them as authoritative state. Eligibility and current Undo status are
derived from the original execution plus linked Undo records.

## Lifecycle

The Undo journal uses these states across its plan, batch, and execution records:

- `PLANNED`
- `APPROVED`
- `INTENT_RECORDED`
- `IN_PROGRESS`
- `VERIFYING`
- `SUCCEEDED`
- `FAILED`
- `INTERRUPTED`
- `RECOVERY_REQUIRED`

`VALID`, `STALE`, and `BLOCKED` belong to Undo planning and revalidation, not to
the mutation lifecycle. No execution row is created for an operation blocked at
preview or either pre-execution revalidation.

`PLANNED` describes the persisted journal intent before approval and `APPROVED`
describes the confirmed batch. A persisted `undo_execution` begins at
`INTENT_RECORDED`, carrying the exact data approved by the user.

Allowed execution transitions are:

```text
PLANNED -> APPROVED
APPROVED -> INTENT_RECORDED
INTENT_RECORDED -> IN_PROGRESS | RECOVERY_REQUIRED
IN_PROGRESS -> VERIFYING | FAILED | INTERRUPTED | RECOVERY_REQUIRED
VERIFYING -> SUCCEEDED | FAILED | RECOVERY_REQUIRED
INTERRUPTED -> RECOVERY_REQUIRED
```

Terminal `SUCCEEDED` and `FAILED` states do not transition. An ambiguous error
can never be reduced to `FAILED`; it is `RECOVERY_REQUIRED`.

Every update uses a guarded expected-state predicate. If exactly one expected
row is not updated, the coordinator stops, marks the scope for recovery where
possible, and performs no further mutation.

## Durable Ordering

The future controller must use this order:

1. Persist the immutable UndoPlan.
2. Perform first revalidation.
3. Show safe-default confirmation.
4. On explicit confirmation, pass the global mutation-safety gate and acquire
   the singleton execution lock.
5. Perform second revalidation while holding that lock.
6. Persist and commit an `undo_batch` in `APPROVED` state.
7. For each operation, insert its exact paths and expected identity as an
   `undo_execution` in `INTENT_RECORDED`; append an event; commit.
8. Guard-transition to `IN_PROGRESS`; append an event; commit.
9. Perform a final no-follow identity, path-chain, occupancy, cloud, and volume
   check.
10. Invoke the one reviewed same-volume rename primitive.
11. Guard-transition to `VERIFYING`; append an event; commit.
12. Verify the old location is absent and the restore destination contains the
    expected identity.
13. Guard-transition to `SUCCEEDED`, `FAILED`, or `RECOVERY_REQUIRED`; append an
    event; commit.
14. Refresh the batch summary and release the owned global lock.

The intent commit must happen before the rename. A crash between rename and the
`VERIFYING` commit leaves `IN_PROGRESS`, which is deliberately recoverable.

## Append-Only Migration Proposal

Migrations 1 through 3 remain unchanged. Milestone 3B implements the Undo
schema as append-only migration 4.

### undo_plan

- `id TEXT PRIMARY KEY`
- `original_batch_id TEXT NOT NULL`
- `original_plan_id TEXT NOT NULL`
- `status TEXT NOT NULL`
- `safety_policy_version INTEGER NOT NULL`
- `behavior_snapshot_json TEXT NOT NULL`
- `summary_json TEXT NOT NULL`
- `snapshot_json TEXT NOT NULL`
- `created_at TEXT NOT NULL`
- `first_revalidated_at TEXT`
- `second_revalidated_at TEXT`

Indexes: `(original_batch_id, created_at)` and `(status, created_at)`.

### undo_planned_operation

- `id TEXT PRIMARY KEY`
- `undo_plan_id TEXT NOT NULL`
- `original_plan_id TEXT NOT NULL`
- `original_batch_id TEXT NOT NULL`
- `original_planned_operation_id TEXT NOT NULL`
- `original_execution_id TEXT NOT NULL`
- `source_path TEXT NOT NULL`
- `restore_path TEXT NOT NULL`
- `expected_identity_json TEXT NOT NULL`
- `preview_metadata_json TEXT NOT NULL`
- `status TEXT NOT NULL`
- `reason_code TEXT`
- `reason_detail TEXT`
- `snapshot_json TEXT NOT NULL`
- `preview_index INTEGER NOT NULL`

Indexes: `(undo_plan_id, preview_index)`, `(original_execution_id)`, and
`(undo_plan_id, status)`.

### undo_batch

- `id TEXT PRIMARY KEY`
- `undo_plan_id TEXT NOT NULL`
- `original_batch_id TEXT NOT NULL`
- `status TEXT NOT NULL`
- `approved_at TEXT NOT NULL`
- `started_at TEXT`
- `completed_at TEXT`
- `app_version TEXT NOT NULL`
- `summary_json TEXT NOT NULL`
- `recovery_required INTEGER NOT NULL DEFAULT 0`

Indexes: `(original_batch_id, approved_at)` and `(status, started_at)`.

### undo_execution

- `id TEXT PRIMARY KEY`
- `undo_batch_id TEXT NOT NULL`
- `undo_planned_operation_id TEXT NOT NULL`
- `original_execution_id TEXT NOT NULL`
- `source_before TEXT NOT NULL`
- `restore_destination TEXT NOT NULL`
- `expected_identity_json TEXT NOT NULL`
- `identity_before_json TEXT NOT NULL`
- `identity_after_json TEXT`
- `metadata_before_json TEXT NOT NULL`
- `metadata_after_json TEXT`
- `state TEXT NOT NULL`
- `error_code TEXT`
- `error_detail TEXT`
- `started_at TEXT NOT NULL`
- `completed_at TEXT`

Indexes: `(undo_batch_id, state)`, `(original_execution_id, started_at)`, and
`(state, started_at)`.

### undo_state_event

- `id TEXT PRIMARY KEY`
- `undo_execution_id TEXT NOT NULL`
- `undo_batch_id TEXT NOT NULL`
- `state TEXT NOT NULL`
- `created_at TEXT NOT NULL`

Index: `(undo_execution_id, created_at)`.

Foreign-key relationships should be declared where compatible with FileFlow's
database configuration, but runtime lookups must still fail closed if linked
rows are absent or inconsistent.

## Duplicate Prevention

The database and coordinator both enforce repeat protection:

- a partial unique index permits at most one `SUCCEEDED` Undo execution for an
  `original_execution_id`
- a partial unique index permits at most one active or unresolved Undo execution
  for an original execution
- a transaction rechecks both constraints immediately before batch approval
- a failed uniqueness check aborts the batch before intent is recorded

Terminal, proven no-mutation failures may remain as history and do not prevent a
fresh plan. `INTERRUPTED` and `RECOVERY_REQUIRED` always prevent another attempt.

## Batch Accounting

Undo batch summaries record candidate, valid, blocked, attempted, succeeded,
failed, interrupted, and recovery-required counts separately. Byte totals count
only verified successful operations and use the metadata observed for those
executions. Blocked preview rows are not reported as attempted.

History joins Undo records to their original Apply plan, batch, and execution.
It never infers success from a missing source or from a batch-level status alone.

## Global Mutation Gate

Apply and Undo use the same singleton execution lock and the same preflight gate.
The gate blocks any real mutation when either journal contains
`INTENT_RECORDED`, `IN_PROGRESS`, `VERIFYING`, `INTERRUPTED`, or
`RECOVERY_REQUIRED`, or when lock ownership is active or cannot be proven stale.

A stale lock may be cleared only when its owner is proved dead and neither Apply
nor Undo has unresolved work. Preview, Undo Preview, History, and read-only
recovery inspection remain available while mutation is blocked.

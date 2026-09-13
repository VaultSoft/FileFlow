# Undo and Journal Model

Undo is part of the design before file execution exists. FileFlow should use SQLite for durable history, operation results, and undo eligibility.

## Journal Goals

- Every approved batch has a durable record.
- Every attempted operation has a durable result.
- Undo eligibility is explicit, not assumed.
- Partial failures are represented accurately.
- Undo never overwrites user data.
- FileFlow can explain why an operation cannot be undone.

## Crash-Aware States

Operation lifecycle:

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

Undo lifecycle:

- `UNDO_PLANNED`
- `UNDO_APPROVED`
- `UNDO_INTENT_RECORDED`
- `UNDO_IN_PROGRESS`
- `UNDO_VERIFYING`
- `UNDO_SUCCEEDED`
- `UNDO_FAILED`
- `UNDO_BLOCKED`
- `UNDO_INTERRUPTED`
- `UNDO_RECOVERY_REQUIRED`

Filesystem operations and SQLite cannot be made atomic together. Future real apply and undo must record intent and commit it before touching the filesystem, then verify and commit the final result afterward.

Startup recovery rule: any batch or operation left in an in-progress state is not automatically marked succeeded or failed. It becomes `RECOVERY_REQUIRED` and must be inspected against filesystem identity and state before any conclusion.

## Batch Record

Each applied batch should store:

- batch ID
- plan ID
- profile ID
- source root
- destination root
- created timestamp
- approved timestamp
- started timestamp
- completed timestamp
- app version
- safety policy version
- rule set version
- category mapping version
- status: `APPROVED`, `APPLYING`, `COMPLETED`, `COMPLETED_WITH_FAILURES`, `ABORTED`, `INTERRUPTED`, `RECOVERY_REQUIRED`
- operation counts by status

## Executed Operation Record

Each executed operation should store:

- operation ID
- batch ID
- planned operation ID
- operation type
- source path before operation
- destination path
- source metadata before operation
- destination metadata after operation
- file identity before operation
- file identity after operation
- size before and after
- timestamps before and after
- result: `SUCCEEDED`, `FAILED`, `SKIPPED`, `STALE`, `BLOCKED`
- structured error code if any
- error detail text for display
- undo eligibility
- undo status
- undo attempted timestamp
- undo result error code if any

Identity fields use `FileIdentity`, not path metadata alone. Metadata snapshots are stored separately from identity.

## Undo Eligibility

An operation is undo-eligible when:

- it completed successfully
- it was a move or rename
- destination still exists
- destination identity matches the post-apply identity
- original source path does not exist
- original source parent exists and is safe
- restoring would not cross unsafe reparse points
- restoring would not overwrite any file or directory
- destination has not materially changed since apply

An operation is not undo-eligible when:

- original location is occupied
- destination file was modified, replaced, or moved
- source or destination volume changed unexpectedly
- destination no longer exists
- source/destination path chain now includes unsafe redirects
- original parent is missing and cannot be safely recreated
- restoring would exceed path/name constraints
- operation was not completed
- user manually changed the file after apply

## Undo Apply Contract

Undo is itself a planned operation batch:

1. User opens history and selects a batch.
2. FileFlow evaluates undo eligibility for each successful operation.
3. FileFlow previews exact reverse operations.
4. User approves undo.
5. FileFlow revalidates immediately before undo.
6. FileFlow records undo results per operation.

Undo must never silently choose a different restoration path. If the original path is occupied, the operation is blocked or requires a new user decision in a future advanced flow.

## Cross-Volume Considerations

Cross-volume real moves are not supported in the first real-operation milestone. They should be represented as `UNSUPPORTED` until the future design proves copy, verification, source removal, interruption handling, and recovery.

Future cross-volume undo is only safe when the journal proves:

- the destination copy was verified
- the source removal completed
- destination identity still matches
- original location is still safe and empty

Interrupted cross-volume moves should be recorded as partial failures with enough detail to avoid pretending undo is available.

## Retention

The journal should not store file contents. It stores metadata, paths, outcomes, and fingerprints. If a future backup/staging feature is added, it must have explicit retention settings, size limits, and user-visible storage location.

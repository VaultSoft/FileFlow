# Undo and Journal Model

Undo is part of the design before file execution exists. FileFlow should use SQLite for durable history, operation results, and undo eligibility.

## Journal Goals

- Every approved batch has a durable record.
- Every attempted operation has a durable result.
- Undo eligibility is explicit, not assumed.
- Partial failures are represented accurately.
- Undo never overwrites user data.
- FileFlow can explain why an operation cannot be undone.

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
- status: `APPROVED`, `APPLYING`, `COMPLETED`, `COMPLETED_WITH_FAILURES`, `ABORTED`
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
- file identity before operation where available
- file identity after operation where available
- size before and after
- timestamps before and after
- result: `SUCCEEDED`, `FAILED`, `SKIPPED`, `STALE`, `BLOCKED`
- structured error code if any
- error detail text for display
- undo eligibility
- undo status
- undo attempted timestamp
- undo result error code if any

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

Cross-volume moves may be implemented as copy, verify, then remove source. Undo for such operations is only safe when the journal proves:

- the destination copy was verified
- the source removal completed
- destination identity still matches
- original location is still safe and empty

Interrupted cross-volume moves should be recorded as partial failures with enough detail to avoid pretending undo is available.

## Retention

The journal should not store file contents. It stores metadata, paths, outcomes, and fingerprints. If a future backup/staging feature is added, it must have explicit retention settings, size limits, and user-visible storage location.

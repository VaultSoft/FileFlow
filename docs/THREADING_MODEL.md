# Threading Model

PyQt UI work stays on the UI thread. File and database work that can block uses workers.

## Implemented Ownership

- `PreviewWorker` runs folder validation, scanning, and planning in a real `QThread`. It owns no database connection and performs no mutation.
- `ApplyWorker` receives only a database path and frozen Preview plan. Inside its worker thread it creates, migrates, uses, and closes a worker-local `Database` and `ApplyController`.
- `UndoWorker` follows the same database-path and worker-local ownership model.
- `MainWindow` never passes its live SQLite connection or a controller containing that connection into Apply or Undo workers.
- worker results are emitted only after worker-local database cleanup completes

SQLite uses its default thread check. `check_same_thread=False` is not used.

## Worker Candidates

Run outside the UI thread:

- directory scanning
- preview generation for large folders
- metadata collection
- hashing
- file operations
- undo operations
- duplicate analysis later
- space analysis
- database migrations if slow

## Worker Pattern

Use Hub's QThread signal style as a reference:

- worker receives immutable request data
- worker calls a service
- worker emits progress events
- worker emits structured success result
- worker emits structured failure result
- UI updates only in response to signals

## Closing and Cancellation

The release candidate does not expose mid-operation cancellation. Closing the window is refused while Preview, Apply, or Undo is active so a live `QThread` is never destroyed. Apply and Undo finish their current journaled batch and report the verified result.

Any future cancellation design must stop only between operations, journal the result, and preserve the same recovery guarantees. Cross-volume stages are outside the current product boundary.

## Progress

Progress should distinguish phases:

- scanning
- safety checks
- rule evaluation
- conflict resolution
- revalidation
- applying
- verifying
- journaling

File counts and byte counts should be reported separately because byte totals can be unknown or expensive.

## Error Propagation

Workers emit structured errors, not raw exception strings as app state. Exception text can be retained as detail, but UI decisions should use error codes.

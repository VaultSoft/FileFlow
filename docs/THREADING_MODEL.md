# Threading Model

PyQt UI work stays on the UI thread. File and database work that can block uses workers.

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

## Cancellation

Scanning and preview generation should be cancellable.

Apply cancellation is more constrained:

- cancellation requests stop before the next operation
- an operation already in progress is allowed to complete or fail
- future cross-volume staged operations must complete their current safe stage before stopping
- cancellation is journaled as batch status, not hidden

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

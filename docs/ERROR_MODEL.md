# Error Model

FileFlow should use structured errors as primary application state.

## Error Codes

- `SAFETY_BLOCK`
- `SOURCE_MISSING`
- `DESTINATION_EXISTS`
- `ACCESS_DENIED`
- `LOCKED`
- `STALE_PLAN`
- `REPARSE_POINT`
- `DISK_FULL`
- `CROSS_VOLUME_FAILURE`
- `UNDO_CONFLICT`
- `PATH_TOO_LONG`
- `INVALID_NAME`
- `RESERVED_NAME`
- `CASE_COLLISION`
- `DESTINATION_UNSAFE`
- `ROOT_UNSAFE`
- `RULE_CHANGED`
- `METADATA_CHANGED`
- `CLOUD_PLACEHOLDER`
- `UNKNOWN_IO_ERROR`

## Severity

- `BLOCKING`: prevents the whole batch.
- `OPERATION_BLOCKING`: prevents one operation.
- `RECOVERABLE`: operation failed but unrelated operations may continue.
- `WARNING`: user should see context, but operation may be safe.

## Classification

Blocking:

- root unsafe
- rule set changed
- plan stale at batch level
- destination boundary escape
- unsafe reparse point in selected root

Operation-blocking:

- destination exists
- source missing
- invalid name
- locked file
- access denied
- path too long
- cloud placeholder unavailable

Recoverable:

- individual copy failure
- disk full for one operation
- destination volume unavailable

Safety blocks should be grouped separately from ordinary errors in the UI.

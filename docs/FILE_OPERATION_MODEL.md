# File Operation Model

FileFlow's operation engine should be a narrow service that executes approved plans. Rules and UI never perform filesystem operations directly.

Initial supported operation types:

- `MOVE`
- `RENAME`
- `CREATE_DIRECTORY`

Deletion is not part of the initial organization engine.

## MOVE

Same-volume moves can use an atomic filesystem rename where available after all safety checks pass.

Cross-volume moves should be treated as:

1. copy to a temporary destination in the target directory
2. verify copied size and metadata
3. optionally verify content hash when policy requires it
4. atomically rename temporary destination to final planned destination
5. remove the original only after verification succeeds
6. journal every stage

If any stage fails, FileFlow records the partial state and does not claim success.

## RENAME

Rename is a move within the same directory.

Case-only renames need special handling on case-insensitive filesystems. FileFlow should preview them as `CASE_ONLY_RENAME` and block until the implementation has a tested two-step strategy that cannot collide with existing paths.

## CREATE_DIRECTORY

Directory creation is allowed only for planned destination folders under approved destination boundaries. It must not follow a destination parent that became a reparse point between preview and apply.

Directory creation records:

- path
- reason
- whether it already existed at preview
- whether FileFlow created it at apply

## Failure Behavior

Expected structured failures:

- destination exists
- locked file
- access denied
- disk full
- source disappeared
- destination became unavailable
- target parent unsafe
- cross-volume verification failure
- interrupted operation

One ordinary failure should not abort unrelated safe work. A safety boundary failure should stop the affected operation or batch depending on scope.

## Verification Model

For every successful move or rename, FileFlow should verify:

- planned source no longer exists, except hardlink semantics where applicable
- planned destination exists
- destination identity or metadata matches expected result
- journal entry was written

For cross-volume moves, verification happens before source removal and after final destination placement.

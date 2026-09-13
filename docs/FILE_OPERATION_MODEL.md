# File Operation Model

FileFlow's operation engine should be a narrow service that executes approved plans. Rules and UI never perform filesystem operations directly.

Initial supported operation types:

- `MOVE`
- `RENAME`
- `CREATE_DIRECTORY`

Deletion is not part of the initial organization engine.

Milestone 1 has no real file operations. It defines models, planning, stale revalidation, journaling states, and a mocked operation interface only. It must have no capability to alter user files.

## Milestone 1 Unsupported Path/Operation Conditions

The operation model receives only paths already accepted by the safety layer. In Milestone 1, unsupported conditions include:

- drive-relative paths
- filesystem roots
- UNC paths/shares
- `\\?\` extended-length logical paths
- ADS syntax
- trailing dot/space names
- reserved device names
- ambiguous Unicode normalization
- unsupported long paths
- case-equivalent collisions
- any participating reparse point
- cloud placeholders/unclassified cloud state

These are `SAFETY_BLOCK` or `UNSUPPORTED`, not execution errors.

## MOVE

Same-volume moves can use an atomic filesystem rename where available after all safety checks pass.

Cross-volume real moves are not supported in the first real-operation milestone. They should be represented as `UNSUPPORTED`.

Future cross-volume support must address:

- copy to a temporary destination
- content verification
- source identity unchanged during copy
- destination identity/content verification
- disk-full and interruption behavior
- ADS policy
- metadata policy
- ACL/security descriptor policy
- sparse-file policy
- crash recovery
- source removal only after verification succeeds

If any stage fails, FileFlow records the partial state and does not claim success.

## RENAME

Rename is a move within the same directory.

Case-only renames need special handling on case-insensitive filesystems. FileFlow should preview them as `CASE_ONLY_RENAME` and block until the implementation has a tested two-step strategy that cannot collide with existing paths.

Decision: case-only rename is `UNSUPPORTED` initially and remains unsupported for the first real move milestone.

## CREATE_DIRECTORY

Directory creation is allowed only for planned destination folders under approved destination boundaries. It must not follow a destination parent that became a reparse point between preview and apply.

Directory creation is not an applied operation in Milestone 1.

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

For cross-volume moves, verification requirements are future design only; they are unsupported until explicitly implemented and tested.

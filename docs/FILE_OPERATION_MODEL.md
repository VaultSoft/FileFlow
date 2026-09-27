# File Operation Model

FileFlow's operation engine is a narrow service that executes approved plans. Rules and UI never perform filesystem operations directly.

Current supported real operation type:

- `MOVE`

Apply and Undo perform same-volume moves through the shared rename primitive after revalidation and durable intent recording. FileFlow does not expose general rename, directory creation, copy, replacement, or deletion operations.

Historical note: Milestone 1 had no real file operations. It defined models, planning, stale revalidation, journaling states, and a mocked operation interface. The current implementation preserves those contracts around its narrow real move boundary.

## Unsupported Path and Operation Conditions

The operation model receives only paths already accepted by the safety layer. Unsupported conditions include:

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

Cross-volume real moves are not supported. They are represented as `UNSUPPORTED`.

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

Decision: case-only rename remains `UNSUPPORTED`.

## CREATE_DIRECTORY

Directory creation is not supported. Destination category folders must already exist under the selected root. A missing folder produces a Blocked row and clear guidance to create it manually before choosing **Analyse Again**.

No Apply or Undo path calls `mkdir`, `makedirs`, or any equivalent user-folder creation primitive.

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

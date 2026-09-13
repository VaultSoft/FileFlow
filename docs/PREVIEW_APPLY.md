# Preview and Apply Contract

Preview and Apply are FileFlow's central contract.

Preview creates an explicit operation plan. Apply attempts only that approved plan after revalidation. Apply does not rediscover files, rerun rules, recalculate destination names, or silently adjust the plan.

Milestone 1 is non-destructive. It models preview, stale revalidation, and a mocked operation interface only. It must have no capability to alter user files.

## Plan Lifecycle

- `DRAFT`: generated but not approved.
- `APPROVED`: user explicitly accepted the displayed plan.
- `APPLYING`: execution has begun.
- `APPLIED`: execution finished with per-operation results.
- `STALE`: plan no longer matches the filesystem or rule state.
- `BLOCKED`: safety policy prevents one or more required operations.
- `ABANDONED`: user discarded the preview.

## What Preview Freezes

Preview freezes:

- selected root logical path and root identity
- relevant existing path-component identity/reparse state
- source logical path
- source `FileIdentity`
- source `MetadataSnapshot`
- intended destination logical path
- destination parent state
- rule ID and rule snapshot
- category mapping snapshot
- collision and safety classification
- cloud/reparse classification
- path policy version
- safety policy version

Apply must attempt exactly this frozen plan or refuse with `STALE_PLAN` / `SAFETY_BLOCK`.

## Planned Operation Fields

Each planned operation should contain:

- stable operation ID
- plan ID and batch ID
- operation type: `MOVE`, `RENAME`, `CREATE_DIRECTORY`
- source path
- intended destination path
- source root ID
- destination root ID
- rule ID and rule version
- category ID and category version when applicable
- human explanation
- conflict status
- safety status
- reversible flag
- undo caveat if any
- source size
- source modified time
- source created time where available
- source file attributes
- source `FileIdentity`
- source link count from identity provider when available
- source content fingerprint state, such as none, metadata-only, partial hash, or full hash
- destination parent status
- preview timestamp

## FileIdentity and MetadataSnapshot

`FileIdentity` is not path + size + mtime. For Windows actionable files, the plan must use handle-based filesystem identity supplied by a `FileIdentityProvider`.

Minimum logical `FileIdentity`:

- volume serial number or equivalent volume identity
- Windows file ID / file index
- file type
- link count where available

Supporting `MetadataSnapshot`:

- logical pathname
- size
- mtime
- ctime
- attributes
- reparse status/tag

If identity cannot be obtained for a file that would eventually be actionable, the operation is `UNSUPPORTED` or `SAFETY_BLOCK`, not safe. Stale revalidation must detect replacement of a file at the same pathname.

## Safety Status

Suggested statuses:

- `SAFE`: operation can be displayed as eligible for apply.
- `NEEDS_DECISION`: user must choose among safe options.
- `BLOCKED`: FileFlow must not apply this operation.
- `STALE`: previously safe but no longer valid.
- `UNSUPPORTED`: not currently supported, such as unsafe cross-volume undo.

## Conflict Status

Suggested statuses:

- `NONE`
- `DESTINATION_EXISTS`
- `CASE_INSENSITIVE_COLLISION`
- `CASE_ONLY_RENAME`
- `NAME_INVALID`
- `PATH_TOO_LONG`
- `DESTINATION_PARENT_MISSING`
- `DESTINATION_PARENT_UNSAFE`

## Preview Generation

Preview generation should:

1. Validate the selected root.
2. Enumerate files according to selected traversal options.
3. Exclude or block unsafe path components.
4. Evaluate rules without performing filesystem operations.
5. Resolve collisions deterministically during preview.
6. Store the exact proposed destination for every operation.
7. Record metadata needed for stale detection.
8. Persist the plan before user approval.

## Apply Revalidation

Immediately before apply, FileFlow must revalidate:

- selected root still exists
- selected root has not become a redirect
- source still exists
- source is the same file or the same safe identity
- source size and timestamps have not materially changed
- source path still remains under the approved root
- source path chain still has no unsafe reparse point
- destination path is still exactly the planned path
- destination does not exist unless operation explicitly allows an existing safe directory
- destination parent exists or can be created according to the plan
- destination parent is not a redirect
- destination remains under approved destination boundary
- rule set version equals the previewed version
- category mapping version equals the previewed version
- plan has not already been applied

Any failed revalidation makes the operation `STALE` or `BLOCKED`. A stale plan requires re-preview.

## Exact Stale-Plan Conditions

A plan or operation becomes stale when:

- source identity changed
- source disappeared
- source was replaced at the same pathname
- selected-root identity changed
- relevant path component identity changed
- relevant path component reparse state changed
- destination now exists when it did not at preview
- destination identity/state changed
- rule snapshot differs
- category mapping snapshot differs
- approved root/path policy differs
- safety classification changed
- cloud classification changed
- reparse classification changed
- path containment no longer holds under logical Windows comparison

`STALE_PLAN` means the user must preview again. Apply must not silently regenerate, repair, re-run rules, select a different destination, or continue as if the new filesystem state had been approved.

## Valid, Stale, and Blocked

`VALID` means every batch-level and operation-level revalidation check passed.

`STALE` means FileFlow cannot prove the approved operation still matches the current filesystem or rule state. Stale is not an error to work around; it means the user must preview again.

`BLOCKED` means safety policy forbids the operation even if it still matches the preview.

## Apply Rules

- Apply never silently adds operations.
- Apply never silently removes operations without recording that they were skipped or stale.
- Apply never changes destination names.
- Apply can continue past independent recoverable failures when the batch policy allows partial success.
- Apply records attempted, succeeded, skipped, stale, blocked, and failed counts separately.
- Apply result should be understandable without opening logs.

## Batch Policies

Initial safe defaults:

- Block the whole batch if the selected root is stale or unsafe.
- Block the whole batch if rules/category versions changed.
- Skip individual stale operations only if the UI explicitly presents this policy before apply.
- Continue unrelated safe operations after ordinary file I/O failures.
- Never continue after detecting destination boundary escape.

## Journal Ordering for Future Apply

Filesystem operations and SQLite commits cannot be one atomic transaction. Future real apply must use this ordering:

1. validate plan
2. record operation intent
3. commit journal state
4. perform filesystem operation
5. verify result
6. record final result
7. commit result

Milestone 1 should model and test this state machine using a mocked operation interface.

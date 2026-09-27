# Test Strategy

## Release Candidate Gate

The 0.1.0 release candidate runs the complete standard-library `unittest` suite on Windows, compiles `fileflow` and `tests`, builds the PyInstaller package, launches the packaged executable, and audits the runtime mutation boundary.

Real `QThread` integration coverage uses temporary directories and a file-backed SQLite database. It proves Preview is non-mutating, Apply and Undo each own and close a worker-local connection, stale post-confirmation state blocks without mutation, unexpected failures clean up before emitting, and a complete Apply-to-Undo round trip persists both batches for a separate main-thread connection.

UI coverage verifies safe initial states, confirmation default/Escape behavior, built-in Rules presentation, non-mutating filename testing, and visible fixed safety boundaries.

Tests must be designed before implementation. Destructive-operation tests use only controlled temporary directories.

Never test FileFlow against real Desktop, Downloads, Documents, or system folders.

## Unit Tests

Safety:

- empty paths
- relative paths
- drive roots
- UNC roots
- Windows directory
- Program Files
- ProgramData
- user profile root
- Desktop/Documents/Downloads selected explicitly
- traversal attempts
- symlinks
- junction/reparse injectable decisions
- destination escape
- alternate data stream syntax
- reserved filenames
- invalid characters
- unsupported long paths are blocked

Rules:

- extension/category matches
- unknown extension behavior
- rule priority
- disabled rules
- first-match behavior
- explainability text
- destination templates

Collisions:

- existing destination blocks
- auto-rename is not available
- apply detects destination created after preview
- case-insensitive collision
- case-only rename blocked

Preview/apply:

- plan contains exact source/destination/reason
- missing destination category folder is visibly blocked with manual recovery guidance
- a mixed Preview validates and applies only its Ready subset while retaining blocked rows
- stale source missing
- stale metadata changed
- stale rule version changed
- stale root reparse changed
- no silent destination recalculation

Undo:

- successful undo eligibility
- original path occupied
- destination changed
- destination missing
- unsafe restore parent
- partial batch undo

The authoritative Undo design is in `UNDO_MODEL.md`, `UNDO_SAFETY.md`,
`UNDO_JOURNAL.md`, `UNDO_RECOVERY.md`, and `UNDO_UX.md`. The detailed
acceptance matrix appears below.

Database:

- migrations apply in order
- plan persistence
- batch persistence
- executed operation persistence
- history queries

## Integration Tests

Use temp directories to simulate:

- normal same-volume move through planner and operation abstraction
- partial failure with one locked or mocked-denied file
- cross-volume operation is represented as unsupported
- disappearing file after preview
- destination appears after preview

Use injectable filesystem adapters for hard-to-create Windows states:

- junctions
- mount points
- OneDrive placeholders
- disk full
- access denied
- sharing violation

## CI

CI and local release verification run:

- unittest discovery
- syntax/import checks
- `python build.py`
- portable EXE and ZIP output verification

CI must not run tests against real user folders.

## Historical Milestone 1 Acceptance Matrix

This completed matrix is retained as the safety baseline for all later Apply and Undo work.

### Windows Path Safety

- drive-relative path is blocked
- filesystem root is blocked
- UNC path/share is blocked
- `\\?\` extended logical path is blocked
- ADS syntax is blocked
- reserved device name is blocked
- trailing dot name is blocked
- trailing space name is blocked
- path escape outside selected root is blocked
- case-equivalent collision is blocked
- ambiguous Unicode normalization case is blocked or marked unsupported

### Reparse

- selected root junction is blocked
- parent junction is blocked
- nested child junction is blocked
- symlink is blocked
- mount/reparse indicator is blocked
- unknown reparse classification is blocked
- redirect introduced after preview makes plan stale/blocked
- reparse inspection failure fails closed

### Identity

- same file unchanged revalidates
- file replaced at same pathname is stale
- identity mismatch is stale
- source removed is stale
- root identity changed is stale
- hardlink identity behavior is documented and tested
- identity unavailable for actionable file becomes unsupported/safety block

### Stale Plan

- destination collision introduced after preview is stale
- rule changed after preview is stale
- category map changed after preview is stale
- safety state changed after preview is stale
- cloud/reparse classification changed after preview is stale

### Rules

- deterministic priority ordering
- priority tie behavior by `(priority, sort_order, id)`
- case-insensitive filename match
- case-insensitive extension match
- normalized relative-subfolder match
- stable explanation output
- rules produce planning intent only

### Journal

- intent recorded before mocked execution
- interrupted state represented
- recovery-required state represented
- partial batch represented
- no operation silently treated as successful after interruption
- startup recovery does not auto-mark in-progress work as succeeded or failed

### Collision

- no overwrite
- no auto-renaming
- exact destination remains fixed
- collision after preview makes operation stale/blocked

### Cloud

- known placeholder blocked
- offline/unhydrated cloud file blocked
- unknown cloud reparse state blocked
- placeholder is not hashed or hydrated

Use injectable/testable filesystem abstractions for Windows-specific conditions that cannot be reliably created everywhere.

## Undo Acceptance Matrix

All real-filesystem cases use dedicated temporary directories only. Windows
reparse, cloud, identity, occupancy, lock, and rename behavior must also be
injectable so CI can prove fail-closed decisions without touching user data.

### Eligibility and Frozen Paths

- only a verified `SUCCEEDED` same-volume FileFlow move is a candidate
- failed, blocked, interrupted, recovery-required, and unsupported Apply rows
  are never Undo candidates
- exact source is the persisted Apply destination
- exact restore destination is the persisted Apply source
- current rules and categories are never consulted to reconstruct paths
- missing or inconsistent original plan, batch, or execution links block
- a consumed execution with successful Undo cannot produce another UndoPlan
- a terminal proven no-mutation failure requires a fresh plan before retry
- a manually renamed or moved file blocks; no identity discovery scan occurs

### Identity and Edited Files

- matching post-Apply `FileIdentity` is eligible
- replacement at the same filename with a different identity blocks
- unavailable or partially supported identity blocks
- changed file type blocks
- unsupported or changed hard-link state blocks
- content, size, and timestamp edits with unchanged identity remain eligible
- edited-file preview warns that only location is restored
- no test or UI claims that Undo restores earlier contents

### Occupancy, Paths, and Reparse State

- free original pathname is eligible
- ordinary file at original path blocks
- directory at original path blocks
- dangling symlink at original path blocks
- junction, mount point, or other reparse entry at original path blocks
- unknown occupancy state blocks
- case-equivalent sibling collision blocks
- missing restore parent blocks and no directory is created
- original source root redirect blocks
- Apply destination root redirect blocks
- nested redirect in either chain blocks
- destination file becoming a reparse entry blocks
- reparse inspection failure blocks
- unsafe or changed root identity blocks
- cloud placeholder, unsupported cloud state, and cloud inspection failure block
- current source and restore parent volume mismatch blocks
- inability to prove same volume blocks

### Revalidation and Confirmation

- first revalidation runs before confirmation
- stale first revalidation prevents confirmation
- Cancel is the default action
- close and Escape cancel
- Enter does not implicitly confirm
- only explicit affirmative input reaches second revalidation
- second revalidation runs after confirmation while the global lock is held
- source identity change between revalidations blocks without mutation
- original path occupancy between revalidations blocks without mutation
- stale or blocked operation invalidates the frozen set; no silent omission
- repeated or double activation creates no second Undo batch

### Execution and Verification

- intent is durably committed before reverse rename
- crash after reverse rename but before `VERIFYING` is classified through
  recovery and is never replayed automatically
- state updates use guarded expected-state transitions
- exact frozen paths are passed to the shared rename primitive
- no overwrite, replace, auto-rename, copy, delete, or mkdir occurs
- verification requires old location absent and restore identity matching
- failed verification becomes recovery required, not success
- a proven no-mutation ordinary failure can allow an unrelated operation to run
- operation 1 interruption or recovery required prevents operation 2
- successful-byte totals include only verified successes
- blocked, failed, interrupted, and recovery counts remain distinct

### Partial Batches and History

- 10 successful, 2 failed, and 1 blocked Apply result shows all outcomes
- if only 7 successful moves remain eligible, preview shows 7 valid and 3
  blocked successful moves with reasons
- failed and blocked Apply operations are visible but never marked undoable
- successful partial Undo persists each operation result
- original Apply records remain byte-for-byte unchanged
- Undo history persists across database close and reopen
- History links each Undo execution to its original Apply execution
- successful Undo prevents duplicate Undo after restart
- Apply A-to-B, Undo B-to-A, and new Apply A-to-B remain distinct chains
- no executable Redo or Undo-of-Undo path exists

### Global Mutation Gate

- active Apply blocks Undo
- active Undo blocks Apply
- unresolved Apply blocks Undo
- unresolved Undo blocks Apply
- unknown lock owner blocks both
- dead lock owner is cleared only when neither journal has unresolved work
- preview, History, and read-only recovery remain available during lockout

### Crash Recovery

- A: expected identity at B and A absent classifies likely not undone
- A with wrong identity at B classifies conflict
- B: B absent and expected identity at A classifies likely completed
- C: both paths occupied classifies conflict
- D: both paths absent classifies missing/recovery required
- E: A occupied by wrong identity classifies conflict
- occupancy or identity inspection error remains unresolved
- recovery inspection never calls the mutation primitive
- recovery inspection never automatically marks success or replays Undo

### Database Migration

- existing migrations retain exact definitions
- new Undo migration applies after all existing migrations
- migration is idempotently skipped after it is recorded
- old databases upgrade without rewriting Apply rows
- new installs receive the same schema through ordered migrations
- uniqueness constraints reject duplicate successful or active Undo executions

### Packaged GUI

- Undo starts disabled and enables only for a freshly validated eligible Apply batch
- persisted History opens in the packaged application
- Undo Preview shows exact current and restore paths
- blocked reasons and edited-file warnings render without truncation
- confirmation safely defaults to Cancel
- packaged close and Escape paths cancel
- no automatic move occurs when opening History or Undo Preview
- a stale second revalidation returns to preview without mutation

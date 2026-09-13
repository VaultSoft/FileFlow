# Test Strategy

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
- auto-rename is not available in Milestone 1
- apply detects destination created after preview
- case-insensitive collision
- case-only rename blocked

Preview/apply:

- plan contains exact source/destination/reason
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

Initial CI should run:

- unittest discovery
- syntax/import checks
- later, `python build.py`
- portable ZIP output verification once packaging exists

CI must not run tests against real user folders.

## Milestone 1 Acceptance Matrix

Milestone 1 cannot be considered complete without these tests.

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

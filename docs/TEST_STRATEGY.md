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
- long paths where supported

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
- deterministic auto-rename preview when enabled
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
- interrupted cross-volume flow through mocked filesystem adapter
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

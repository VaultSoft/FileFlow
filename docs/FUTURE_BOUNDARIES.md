# Future Boundaries

This document marks future-capable areas without implementing them.

## Free Boundary

Initial FileFlow Free should support:

- analyse selected folders
- preview file organization plans
- apply reviewed plans
- history
- undo where safely possible
- basic extension/category rules
- basic space analysis

Free architecture must still use the same plan, journal, safety, and storage foundations as future Pro work.

## Pro-Capable Future Areas

Future FileFlow Pro may add:

- advanced rules
- folder monitoring
- scheduled organization
- duplicate detection and management
- profiles/presets
- more automation

Do not add licensing, paywalls, or Pro UI in the initial implementation.

## Duplicate Strategy

Do not implement duplicate detection initially.

Future duplicate stages:

1. group by size
2. optional partial hash
3. full cryptographic hash
4. present candidate groups for review

Never classify duplicates solely by filename.

Future duplicate actions:

- review
- keep
- move/quarantine
- delete only with explicit destructive flow

Deletion should never be automatic.

## Monitoring and Scheduling

Do not implement folder watching or scheduling initially.

Future monitoring may generate proposed plans, but it must not grant destructive authority by itself. Safe automation might eventually apply only previously approved low-risk rules in constrained folders, but the default should remain review-first.

Monitoring should:

- debounce changes
- create preview plans
- mark plans stale when folder changes again
- never run file operations directly from a filesystem event callback

Scheduling should:

- generate previews
- notify the user
- require approval for destructive moves until an explicit and well-tested automation policy exists

## Risks Before Implementation

Settle before coding:

- where SQLite database lives
- what metadata change tolerance makes a plan stale
- exact Win32/ctypes implementation details for `WindowsFileIdentityProvider`
- exact Win32/ctypes implementation details for `ReparseInspector`
- future policy for ACL/security descriptor preservation
- future policy for ADS preservation in cross-volume operations
- future UX for user-driven collision decisions

## Recommended First Implementation Milestone

Build a non-destructive core slice with tests:

1. category defaults
2. deterministic basic rule engine
3. Windows logical path model
4. explicit path support/block classifier
5. reparse inspector abstraction
6. Windows `FileIdentity` abstraction
7. immediate-child scanner/planner
8. explicit `PreviewPlan` and `PlannedOperation` models
9. category/rule snapshots
10. SQLite schema and migrations
11. crash-aware journal state machine
12. stale-plan revalidation
13. mocked operation interface
14. structured error/safety classifications
15. comprehensive tests

Not included:

- real move
- real rename
- directory creation as an applied operation
- delete
- undo execution
- GUI Apply
- recursion
- auto-rename
- case-only rename
- cross-volume operation
- cloud hydration
- duplicate detection
- monitoring
- scheduling

Milestone 1 must have no capability to alter user files.

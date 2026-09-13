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

- exact Windows reparse-point detection strategy
- file identity metadata available from Python 3.11 on Windows
- long path policy
- OneDrive placeholder handling
- case-only rename strategy
- whether recursive scanning is included in milestone 1
- whether auto-rename is included in milestone 1 or deferred
- where SQLite database lives
- what metadata change tolerance makes a plan stale

## Recommended First Implementation Milestone

Build a non-GUI core slice with tests:

1. category defaults
2. rule engine
3. safety path classifier
4. preview planner for immediate-child files only
5. SQLite schema and migrations
6. stale-plan revalidation
7. no apply engine yet except a mocked operation interface

This gives FileFlow a safe foundation before any real file-moving code exists.

# Project Architecture

FileFlow should follow VaultSoft Hub's package-first structure rather than the older single-file app pattern.

Stack:

- Python 3.11
- PyQt6
- SQLite
- PyInstaller
- unittest for initial tests

## Proposed Layout

```text
FileFlow/
  README.md
  VERSION
  requirements.txt
  requirements-build.txt
  build.py
  fileflow/
    __init__.py
    __main__.py
    app.py
    models/
      __init__.py
      paths.py
      rules.py
      plans.py
      history.py
      errors.py
    services/
      scanner.py
      planner.py
      rules_engine.py
      operation_engine.py
      undo_service.py
      duplicate_service.py
    safety/
      paths.py
      identity.py
      reparse.py
      names.py
      roots.py
    storage/
      database.py
      migrations.py
      repositories.py
    workers/
      scan_worker.py
      preview_worker.py
      apply_worker.py
      undo_worker.py
    ui/
      main_window.py
      preview_view.py
      history_view.py
      rules_view.py
      settings_view.py
      styles.py
  tests/
    test_safety_paths.py
    test_rules_engine.py
    test_preview_apply.py
    test_collision_policy.py
    test_undo_journal.py
    test_database_migrations.py
```

This is a design target, not implementation created by this task.

## Responsibility Boundaries

### UI

Displays state and gathers user choices. It never moves files, scans directly, or writes operation results.

### Models

Plain dataclasses or typed structures for paths, rules, plans, history, and errors. Models contain no PyQt dependencies.

### Safety

Centralized path and filesystem policy. Every scanner, planner, apply, and undo path goes through safety checks.

Milestone 1 safety abstractions:

- `FileIdentityProvider`: supplies handle-based logical identity for Windows files, roots, and relevant path components.
- `WindowsFileIdentityProvider`: future Windows implementation boundary. Exact ctypes/Win32 details are not chosen in the design docs.
- `ReparseInspector`: classifies reparse state per path component without trusting `Path.resolve()`.
- `WindowsPathPolicy`: accepts only normal absolute local drive paths under an approved root and blocks unsupported Windows path forms.

### Services

Business logic:

- scanner enumerates candidate files
- rules engine explains intended destinations
- planner creates operation plans
- operation engine executes approved operations
- undo service creates and applies reverse plans

### Storage

SQLite connection, migrations, and repository methods. Services depend on storage interfaces rather than raw UI state.

### Workers

PyQt thread wrappers around services. Workers emit progress, results, and structured errors.

## Release Standard Alignment

FileFlow should start compliant with the VaultSoft release standard:

- one authoritative version source
- separate `requirements.txt` and `requirements-build.txt`
- `python build.py`
- clean reproducible PyInstaller build
- non-release `Build Verification` workflow
- tests and syntax/import checks in CI
- portable ZIP artifact
- release workflow separate from verification

Do not add release automation until implementation needs packaging.

## Revised Milestone 1: Non-Destructive Core

Implement later:

- category defaults
- deterministic basic rule engine
- Windows logical path model
- explicit path support/block classifier
- reparse inspector abstraction
- Windows `FileIdentity` abstraction
- immediate-child scanner/planner
- explicit `PreviewPlan` and `PlannedOperation` models
- category/rule snapshots
- SQLite schema and migrations
- crash-aware journal state machine
- stale-plan revalidation
- mocked operation interface
- structured error/safety classifications
- comprehensive tests

Explicitly not part of Milestone 1:

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

# FileFlow 0.1.0 Release Candidate

## Product Contract

This release candidate supports previewed, journaled, same-volume file moves and exact-path Undo on Windows. It is intentionally narrower than a general file manager.

The release is ready only when all of the following remain true:

- Apply starts disabled and requires a current supported Preview.
- Apply revalidates before and after a safe-default confirmation.
- Undo is available only from persisted successful Apply operations.
- Undo revalidates before and after a safe-default confirmation.
- Apply and Undo workers create and close their own SQLite connections in their own `QThread`.
- the UI thread retains a separate connection for readiness, History, and recovery inspection
- unresolved Apply or Undo journal state blocks every mutation path
- the 100-operation limit blocks rather than truncates
- exact frozen destinations and exact original restore paths are used
- collisions never overwrite and never auto-rename
- recovery inspection remains read-only
- only `os.rename` in `fileflow/operations/same_volume_move.py` can mutate user files

## Rules Experience

The Rules page is an inspectable view of the active built-in category mappings. Its filename tester evaluates filename and extension conditions in memory only. It does not read metadata, scan a folder, create a Preview, or grant permission to move anything.

Rules remain built-in and read-only in this release candidate. Unknown extensions stay in place. User-defined rule editing needs its own persistence, migration, stale-plan, and safety review before a later release.

## History and Recovery

History is backed by the append-only operation journal. Apply batches remain immutable after Undo. Undo creates separate plan, batch, execution, and event records linked to the original Apply batch.

When FileFlow cannot prove whether a move completed, it records unresolved state, stops later operations, disables Apply and Undo globally, and displays read-only recovery evidence. It never retries, deletes, overwrites, searches for a file, or repairs journal state automatically.

## Release Verification

Run from a clean working tree:

```powershell
python -B -m unittest discover -s tests -v
python -B -m compileall -q fileflow tests
python -B build.py
git diff --check
git status --short
```

Then launch `dist\FileFlow\FileFlow.exe` and verify:

- title is `FileFlow`
- Apply and Undo controls start disabled
- Preview, History, Rules, and Settings pages render correctly
- the filename tester is non-mutating
- the app closes normally when no worker is active
- the portable ZIP contains the complete packaged directory

No tag, release, or version change is implied by passing this checklist.

# Undo UX

Milestone 3A defines this flow only. It does not add, enable, or wire an Undo
control.

## History Entry Point

History remains readable during mutation lockout. Each Apply batch row shows
its persisted status and counts. A details view lists every original execution
and any linked Undo attempts.

A future `Preview Undo` button appears only for a completed Apply batch that has
at least one successful same-volume move candidate. The button opens preview;
it never mutates immediately. If global unresolved work exists, History remains
available but the control is disabled with the recovery reason.

The UI must not label an operation undoable from stored filenames alone. A
fresh eligibility evaluation determines the preview result.

## Undo Preview

Undo Preview shows one row for every successful Apply execution considered:

- filename
- current location (Apply destination)
- restore location (original Apply source)
- expected identity status
- current metadata-change warning, if any
- `VALID`, `STALE`, or `BLOCKED`
- structured reason and conflict detail

The summary shows valid, blocked, stale, and never-moved Apply counts. Failed or
blocked Apply operations remain visible in batch context but are never presented
as Undo candidates. Filters may separate Restorable, Blocked, and Not Moved,
but default presentation must not hide blocked rows.

The primary command uses the exact valid count, for example `Preview Undo (7)`
or `Undo 7 Files` at confirmation. No alternate destinations or auto-rename
choices are offered.

## Edited-File Message

When metadata changed but identity still matches, show a non-blocking warning:

```text
This file changed after FileFlow moved it. Undo restores its location only.
Its current contents and metadata will move with it.
```

Do not claim that Undo restores an earlier file version.

## Confirmation

First revalidation runs before the dialog. The confirmation states:

```text
FileFlow will move these files back to their original locations.
It will not restore earlier file contents or replace anything already there.
```

The dialog shows the exact operation count and blocked count. `Cancel` is the
default focused action. Closing the window and pressing Escape cancel. The
affirmative button is not the keyboard default; only explicit activation starts
the post-confirmation flow.

After confirmation, FileFlow acquires the global mutation lock and performs the
second revalidation. A stale or blocked result returns to Undo Preview with no
mutation and requires a fresh preview.

## Progress and Result

Progress identifies the operation as Undo and displays the exact current and
restore paths. Apply and Undo controls remain disabled while one real batch owns
the global lock. Repeated clicks cannot create a second batch.

Result accounting separates:

- restored and verified
- failed with no mutation
- blocked before execution
- not attempted
- interrupted
- recovery required

The result links to the original Apply batch and the new Undo batch. It does not
rewrite the original History row. A partial result explicitly lists every file.

## Repeat Undo

After a verified successful Undo, the original execution reads `Restored` and
cannot be selected for Undo again. A later new Apply from the restored path is a
new history chain. There is no Redo control in the first implementation.

## Recovery View

Recovery is read-only. It shows both journal paths, observed occupancy, identity
match status, and one of the recovery classifications. It offers no retry,
delete, overwrite, find-file, or automatic repair action.

## Packaged GUI Test Expectations

Future packaged GUI coverage should prove:

- Undo controls are absent or disabled until implementation is intentionally
  enabled
- History opens without mutation
- Undo Preview shows exact persisted paths and blocked reasons
- confirmation defaults to Cancel
- close and Escape cancel
- only explicit affirmative input reaches second revalidation
- double activation creates at most one batch
- unresolved Apply or Undo disables both real mutation paths
- stale second revalidation returns to preview without execution

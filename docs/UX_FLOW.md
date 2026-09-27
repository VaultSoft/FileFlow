# UX Flow

FileFlow is a dense, work-focused Windows utility. Safety state is always visible and real file changes require a Preview plus explicit confirmation.

## Preview

The opening page contains:

- selected-folder field and folder picker
- Analyse command, disabled until the selected root passes safety checks
- visible status and progress state
- totals for files, Ready, Blocked, Unsupported, Collisions, skipped folders, and bytes
- status filter and filename/category search
- exact source, destination, category, rule reason, and detailed safety state
- Validate Preview, Analyse Again, and a count-aware Move command

Apply starts disabled. It enables only when the controller reports a current supported plan with at least one actionable operation and no global lockout. Apply uses a safe-default confirmation and second worker-thread revalidation.

## Apply Result

The Preview page reports moved-and-verified, safely failed, blocked/excluded, and recovery-required counts. A consumed plan cannot be applied again. History refreshes from persisted journal state.

## History and Recovery

History lists newest Apply batches with source folder, counts, recovery state, and status. Selection shows read-only operation details and linked Undo attempts.

The recovery banner distinguishes clear state, active lock state, unresolved Apply, and unresolved Undo. Unresolved work disables both mutation paths. Recovery evidence is read-only and offers no retry, overwrite, delete, or automatic repair.

## Undo

An eligible successful Apply batch exposes Preview Undo. The Preview checks current identity and the exact original path and shows every Ready or blocked restore operation.

Undo uses a safe-default confirmation and a second worker-thread revalidation. It restores location only; edited contents and metadata move with the same identified file. Successful Undo is persisted separately and never rewrites Apply history.

## Rules

Rules lists the ordered built-in categories, active state, exact destination folder, and extensions. The filename tester is an in-memory explanation tool only. It does not inspect disk or make a file eligible for Apply.

## Settings and Safety

The Settings page presents fixed behavior rather than misleading controls: immediate-file scope, same-volume moves, collision blocking, identity-checked Undo, read-only recovery, the 100-operation cap, app version, and local history database path.

## Window Lifecycle

FileFlow closes normally when idle. While any Preview, Apply, or Undo worker is active, close is refused with a clear message so a running worker thread is never abandoned.

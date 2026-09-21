# Undo Model

Milestone 3A defines Undo but does not implement or expose it. Undo is a new
filesystem mutation workflow. It is not an instruction to swap the source and
destination strings and call rename.

The first implementation is limited to restoring the location of successfully
verified same-volume file moves performed by FileFlow. It does not restore file
contents, metadata, or earlier versions.

## Terms

- Apply source: the original source path recorded by the immutable Apply plan.
- Apply destination: the exact destination path used by the successful Apply.
- Undo source: the Apply destination at which the moved file is expected now.
- Restore destination: the Apply source to which Undo proposes to move it.
- Original execution: the successful `executed_operation` produced by Apply.
- Expected identity: the post-Apply `FileIdentity` stored by the original
  execution after successful verification.

The Undo direction is always:

```text
Apply destination -> Apply source
```

Both paths come directly from immutable persisted Apply records. Rules,
categories, current folder names, and filename heuristics must never be used to
reconstruct either path.

## Eligibility

An original execution is a candidate for Undo only when all of these are true:

1. Its operation type is the supported FileFlow same-volume `MOVE`.
2. Its final Apply journal state is `SUCCEEDED`.
3. Apply stored a supported post-move `FileIdentity` after verification.
4. Its Apply batch and plan records are present and internally consistent.
5. It has no successful Undo and no active or unresolved Undo attempt.
6. The exact Apply destination exists and is an ordinary file.
7. The file at that path has the exact expected `FileIdentity`.
8. The exact original source path is unoccupied under a no-follow check.
9. The restore parent exists, is a directory, and is safe. Undo does not create
   missing directories.
10. Both roots and both complete path chains pass the current Windows path,
    reparse, cloud, containment, and protected-location policies.
11. Current source and restore parent identities prove a same-volume rename.
12. The current safety policy can establish every required fact without an
    inspection error or ambiguity.

Any failed or unprovable condition makes that operation `BLOCKED`. A previous
`FAILED`, `BLOCKED`, `INTERRUPTED`, or `RECOVERY_REQUIRED` Apply execution is
never an Undo candidate. Unsupported and cross-volume operations are also never
candidates.

## Identity Policy

Undo compares the current Undo source with the post-Apply `FileIdentity`, not
with a pathname, filename, size, timestamp, or content hash. The comparison is
against the complete supported identity recorded by FileFlow, including the
volume identity, file ID, file type, and link-count policy represented by the
current `FileIdentity` model. If a required identity field was unavailable or
is no longer equal, Undo blocks.

Replacing a file at the same name normally changes its file ID and therefore
blocks Undo. A manually renamed or externally moved file makes the expected
Apply destination absent and also blocks Undo. The initial implementation must
not search the filesystem for a matching file ID.

## Edited Files

Undo restores location only. If the file at the Apply destination still has the
same `FileIdentity`, changes to its contents, size, timestamps, attributes, or
other ordinary metadata do not by themselves block Undo.

The preview records and compares metadata so it can show that the file changed
after Apply. This is a warning, not an assertion that FileFlow can recover old
contents. Confirmation text must state that current file contents move with the
file and are not rolled back.

Identity-relevant or safety-relevant changes still block. Examples include a
different file ID, a changed file type, unsupported hard-link state, a reparse
state change, an unsafe cloud state, or inability to inspect the file.

## Original Path Occupancy

The restore destination must be proved free with a no-follow occupancy check.
Only a definite not-found result is free. All of these are occupied or unknown
and therefore block:

- an ordinary file
- a directory
- a case-equivalent name in the restore parent
- a symlink, including a dangling symlink
- a junction, mount point, or other reparse entry
- an access-denied or otherwise uninspectable entry

Undo never overwrites, replaces, deletes, auto-renames, or selects an alternate
path. It does not restore as `filename (1)`.

## Frozen UndoPlan

`UndoPlan` is an immutable persisted preview. It has a stable ID, original
Apply batch and plan IDs, creation time, current safety-policy version, behavior
snapshot, summary, and one of these states:

- `VALID`: every selected operation passed preview validation.
- `STALE`: a previously valid frozen fact no longer matches current state.
- `BLOCKED`: current policy forbids execution or cannot prove safety.

Each `UndoPlannedOperation` stores:

- stable Undo operation ID and preview index
- original Apply plan, batch, planned-operation, and execution IDs
- exact Undo source path, taken from the Apply destination
- exact restore destination, taken from the Apply source
- expected post-Apply `FileIdentity`
- metadata observed at Undo preview
- original source and destination root paths and identities
- Undo source parent and restore parent identities
- relevant path-chain and reparse snapshots
- cloud and same-volume classification
- safety-policy and behavior versions
- `VALID`, `STALE`, or `BLOCKED` status
- structured block or stale reason

The plan does not rerun organization rules. A valid plan freezes the exact set
and order of operations the user sees. If either revalidation changes that set,
the whole plan stops and the user must create a fresh Undo Preview.

## Partial Apply Batches

Only original executions with final state `SUCCEEDED` enter eligibility
evaluation. For a batch with 10 successes, 2 failures, and 1 blocked operation,
the preview explains all 13 outcomes but creates Undo candidate rows only for
the 10 successes.

If 7 of those 10 remain valid, the preview shows:

- 7 valid operations in the proposed Undo set
- 3 blocked successful operations with individual reasons
- 2 failed and 1 blocked Apply operations as never moved by FileFlow

Nothing is silently omitted. Confirmation names the exact valid count. The
initial batch may execute those 7 after both revalidations. A newly stale item
before execution invalidates the frozen plan rather than being silently dropped.

Once execution starts, an ordinary, proven no-mutation failure may be recorded
and unrelated operations may continue. Any ambiguous result, interruption, or
`RECOVERY_REQUIRED` stops the batch before the next rename.

## Repeat Undo and Future Redo

A successful Undo permanently makes that original execution ineligible for a
second Undo. Eligibility is derived from linked Undo records, not by rewriting
the Apply record. An active or unresolved Undo also blocks another attempt.

A terminal failure that is proven not to have mutated the file may permit a new
preview and later attempt. It never permits replay of the old plan.

These chains are valid historical sequences:

```text
Apply A->B -> Undo B->A -> new Apply A->B
```

The last Apply is a new Apply plan, batch, and execution with its own future
Undo eligibility. This is not Redo.

Executable Redo and Undo-of-Undo are outside the first implementation. Keeping
Apply and Undo records immutable, linked, and complete allows a future migration
to add Redo without rewriting history.

## Runtime Boundary

The preferred implementation is separate Apply and Undo policy layers that
produce a typed, fully validated same-volume move request for one reviewed move
primitive. `SameVolumeMoveExecutor` currently embeds Apply-specific plan and
rule revalidation, so passing an UndoPlan into it directly would weaken type and
policy boundaries.

Before implementing Undo, extract only the final reviewed same-volume rename
mechanism behind a narrow typed interface. Keep Apply preflight and Undo
preflight separate, but route both through that single primitive. There must
still be exactly one raw `os.rename` call in runtime code. Milestone 3A performs
no such refactor.

## Open Implementation Questions

These do not change the safety contract, but should be resolved during the
implementation review:

- the narrow shared primitive's final type and module name
- the strongest practical Windows handle-sharing strategy for reducing the
  final check-to-rename race without following reparse points
- the exact presentation threshold for metadata-change warnings
- the separately reviewed user workflow for reconciling a read-only recovery
  assessment into a terminal journal resolution
- whether the existing Apply batch-size cap is reused unchanged for Undo or a
  lower conservative cap is introduced

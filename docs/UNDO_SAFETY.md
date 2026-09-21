# Undo Safety

Undo is a real filesystem mutation and receives the same conservative treatment
as Apply. A false negative is acceptable. An uncertain operation must not move a
file.

## Safety Invariants

Every Undo operation must preserve these invariants:

1. The operation is linked to one verified successful FileFlow Apply execution.
2. Both paths are exact immutable journal paths.
3. The file at the Undo source is the exact post-Apply file FileFlow recorded.
4. The restore destination is definitely unoccupied.
5. Both path chains are normal supported local Windows paths with no reparse
   participation.
6. Source and restore parent are proved to be on the same volume.
7. No copy, delete, replace, overwrite, auto-rename, directory creation, cloud
   hydration, identity discovery, or cross-volume fallback occurs.
8. One global mutation gate prevents concurrent Apply or Undo batches.

## Windows Path Validation

Undo validates logical Windows paths without relying on `Path.resolve()`.
Validation is case-insensitive and rejects unsupported syntax, including
relative and drive-relative paths, filesystem roots, UNC and extended paths,
alternate data streams, reserved names, ambiguous trailing dots or spaces, and
paths outside their recorded approved roots.

The Undo source must remain beneath the original Apply destination root. The
restore destination must remain beneath the original Apply source root. Both
root directory identities must match the immutable plan snapshots. Textual
prefix containment is never sufficient.

## Reparse Safety

The current root, every existing parent component, the source entry, and any
entry occupying the restore path are inspected without following redirects.
Symlinks, junctions, mount points, name-surrogate tags, and unknown reparse tags
block. Failure to inspect any component blocks.

The check is repeated during Undo Preview, first revalidation, second
revalidation, and the final executor preflight. The final check must occur as
close to the rename as the reviewed primitive permits.

The initial design does not allow a safe reparse exception. Even a redirect
that currently resolves inside a permitted root blocks because its target can
change independently and because textual containment cannot prove the actual
mutation boundary.

## Source Identity

The source check requires:

- exact Undo source pathname occupied
- ordinary file type
- supported handle-based identity
- exact equality with the persisted post-Apply `FileIdentity`
- supported hard-link/link-count state
- safe cloud state without implicit hydration

The expected path being absent blocks. A different identity at that path
blocks. FileFlow does not search sibling folders, the selected roots, or the
volume for a matching identity.

Ordinary metadata and content differences are displayed but do not block when
identity and all safety facts remain valid. Undo promises location restoration,
not content restoration.

## Restore Destination and Parent

The restore destination uses a no-follow occupancy inspector. Only definite
absence is accepted. Any existing or uninspectable entry blocks, including a
dangling link or a case-equivalent sibling.

The restore parent must already exist as an ordinary directory. Its identity is
frozen in the UndoPlan and checked again. Missing parents block; Undo must not
call `mkdir`. Parent and current source volume identities must match.

The restore path is reclassified under current name, length, protected-root,
cloud, containment, and collision policies at every revalidation.

## Double Revalidation

The controller sequence is fixed:

```text
Undo Preview
-> first revalidation
-> safe-default confirmation
-> acquire global lock
-> second revalidation
-> record intent
-> final executor preflight
-> rename
-> verify
```

The first revalidation prevents confirmation of an already stale preview. The
second occurs after explicit user confirmation and while holding the global
mutation lock. Neither check authorizes a changed set: a stale or blocked item
invalidates the frozen plan and requires a new preview.

Closing the confirmation, pressing Escape, or using the default focused action
must cancel. Enter must not implicitly activate the affirmative action. Only an
explicit affirmative control can proceed.

## Race Limits

SQLite locking cannot prevent external programs from changing files between a
check and rename. The reviewed primitive therefore repeats identity, parent,
occupancy, reparse, cloud, and volume checks immediately before mutation and
never uses replacement semantics.

If Windows APIs cannot confidently preserve or verify the required no-follow
facts, the operation blocks. A future implementation may strengthen this with
appropriately shared handles, but it must not weaken the fail-closed contract to
gain availability.

## Execution Failure Policy

A rename error that proves no mutation occurred may be recorded `FAILED`, and
the batch may continue with unrelated valid operations. An interruption,
verification failure, ownership change, journal transition failure, or any
uncertain filesystem result becomes `RECOVERY_REQUIRED` and stops the batch.

Verification requires:

- Undo source definitely absent
- restore destination definitely occupied
- restore destination identity equal to the expected identity
- restore path chain still safe

Failure to prove all four is not success.

## Mutation Boundary

Undo must not add a second raw rename implementation. Apply and Undo should have
separate typed preflight policies feeding one narrow reviewed same-volume move
primitive. Runtime continues to contain one `os.rename` call and no copy,
delete, replace, overwrite, or cross-volume fallback.

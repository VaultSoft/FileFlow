# Undo Recovery

Undo recovery is inspection only. It never automatically replays a rename,
deletes a conflicting entry, selects a different path, or decides that a file
with the right name is the expected file.

## Recovery Inputs

Recovery reads immutable journal data:

- exact Undo source `B` (the Apply destination)
- exact restore destination `A` (the Apply source)
- expected post-Apply `FileIdentity`
- source and restore roots
- recorded Undo execution state and state events

It performs fresh no-follow occupancy, identity, path-chain, reparse, cloud, and
root checks. An inspection error is itself an unresolved recovery result.

## Core Classifications

Before Undo, the expected arrangement is `B` occupied by the expected identity
and `A` absent.

### A: B exists, A absent

If `B` has the expected identity and both paths are safely inspectable, classify
`LIKELY_NOT_UNDONE`. The rename likely did not happen. Do not retry it
automatically and do not mark the execution successful.

If `B` has a different or unprovable identity, classify
`CONFLICT_RECOVERY_REQUIRED`.

### B: B absent, A exists with matching identity

Classify `LIKELY_UNDO_COMPLETED`. The rename likely happened. Inspection does
not silently convert the journal to success; a separately reviewed recovery
resolution flow may later reconcile it.

### C: B and A both exist

Classify `CONFLICT_RECOVERY_REQUIRED`, regardless of which path contains the
expected identity. Undo never removes either entry.

### D: B and A both absent

Classify `MISSING_RECOVERY_REQUIRED`. FileFlow cannot locate the expected entry
at either journal path and does not search elsewhere.

### E: A exists with the wrong identity

Classify `CONFLICT_RECOVERY_REQUIRED`. The restore name is occupied by another
entry and cannot be treated as a completed Undo.

## Additional Fail-Closed Cases

Recovery remains unresolved when:

- either occupancy check is unknown
- identity cannot be obtained
- either path chain includes a reparse point
- a recorded root identity changed
- cloud state cannot be proved supported
- the journal link to the original execution is missing or inconsistent
- journal state events are invalid or incomplete
- a case-equivalent entry makes occupancy ambiguous

## State and Lockout

Startup treats Undo executions left in `INTENT_RECORDED`, `IN_PROGRESS`,
`VERIFYING`, or `INTERRUPTED` as unresolved and exposes a recovery assessment.
They must not be flattened to `FAILED` merely because the process ended.

Any unresolved Apply or Undo globally blocks all new real mutation. The singleton
execution lock is not cleared when unresolved journal work exists. Read-only
History, preview, and recovery inspection continue to work.

An assessment is evidence, not an execution state transition. Milestone 3A does
not design an automatic resolver. A future reviewed recovery workflow may let a
user acknowledge a proven result and append a resolution record, but it must
not mutate or erase the original state-event history.

## Batch Recovery

Recovery is per execution and summarized at batch level. If operation 1 enters
`RECOVERY_REQUIRED`, operation 2 must not run. The batch remains
`RECOVERY_REQUIRED` even if earlier operations succeeded.

The UI reports each earlier success, each untouched later operation, and the
uncertain operation separately. It must not present an aggregate success count
as proof that the uncertain file is safe.

## Recovery Test Matrix

Future tests inject occupancy and identity providers to cover:

- A with expected identity at B
- A with wrong identity at B
- B with expected identity at A
- C with expected identity at B only
- C with expected identity at A only
- C with wrong identities at both paths
- D
- E
- inspection errors at each path
- unsafe source or restore chain
- unresolved Undo blocking Apply
- unresolved Apply blocking Undo
- no automatic rename or journal success transition during inspection

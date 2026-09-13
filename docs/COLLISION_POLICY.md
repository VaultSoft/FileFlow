# Collision Policy

FileFlow must never silently overwrite an existing file.

Collision policy is resolved during preview. Apply only revalidates the exact previewed destination.

## Outcomes

Supported design outcomes:

- `BLOCK`: operation cannot proceed.
- `SKIP`: operation is omitted from apply but recorded in the plan.
- `AUTO_RENAME`: FileFlow chooses a deterministic available name during preview.
- `USER_DECISION`: user selects a safe outcome in preview.

Initial safe defaults:

- Existing destination: `BLOCK`.
- Case-insensitive collision: `BLOCK`.
- Invalid name: `BLOCK`.
- Auto-rename: optional setting, off by default for the first implementation.

## Auto-Rename

If enabled later, auto-renaming must be deterministic:

- `photo.jpg`
- `photo (1).jpg`
- `photo (2).jpg`

Rules:

- final exact name is chosen during preview
- chosen destination is persisted in the plan
- apply must not pick a new suffix
- if the previewed name is no longer available at apply time, operation is `STALE`

## Case-Insensitive Collisions

On Windows, FileFlow should compare destination paths using case-insensitive semantics. It should detect:

- `Photo.jpg` versus `photo.jpg`
- case-only rename attempts
- names that differ only by normalization or trailing characters Windows ignores

Initial behavior is to block case-only renames unless a tested safe rename strategy exists.

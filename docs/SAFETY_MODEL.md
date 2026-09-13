# Safety Model

FileFlow treats moving and renaming files as destructive operations. A false negative is acceptable. A destructive false positive is not.

The core rule is:

> Preview authorizes only the exact planned operations that were shown. Apply must revalidate those operations and block or require re-preview when reality no longer matches the plan.

## Safety Principles

1. User selects an explicit analysis root.
2. FileFlow never operates on filesystem roots.
3. FileFlow never treats system or profile roots as normal cleanup roots.
4. Reparse points, symlinks, junctions, mount points, and cloud placeholders are hostile until proven safe.
5. No operation silently overwrites an existing file.
6. No destination is silently recalculated during Apply.
7. No operation escapes the approved source root or destination boundary.
8. Stale plans require regeneration.
9. Safety blocks are not ordinary errors and should be displayed separately.
10. Undo must fail safely and must never overwrite user-created data.

## Protected Roots

The safety service should block selecting or writing directly to:

- Drive roots such as `C:\`, `D:\`, UNC share roots, and volume mount roots.
- Windows directory, normally `C:\Windows`.
- `System32` and descendants.
- `Program Files`.
- `Program Files (x86)`.
- `ProgramData`.
- User profile root, for example `C:\Users\Josh`.
- Other user profile roots under `C:\Users`.
- Recycle Bin and system volume metadata folders.

Desktop, Documents, Downloads, Pictures, Videos, and Music are not categorically forbidden because they are likely FileFlow targets. They require explicit user selection, clear preview, and no recursive default into unsafe descendants.

## Selected Roots

When a user selects a folder, FileFlow should record:

- raw user-selected path
- normalized absolute path
- canonical real path where safely obtainable
- volume identity if available
- whether any path component is a reparse point
- selected root metadata snapshot
- timestamp of analysis

Selection is blocked if:

- the path is empty or relative
- the path resolves to a filesystem root
- the path is a protected root
- the root cannot be inspected
- the root or a parent component is a reparse point whose target cannot be proven safe
- the root path changes identity between selection and preview

## Reparse Points and Redirects

FileFlow should not follow filesystem redirects blindly.

Blocked by default:

- selected root is a symlink, junction, mount point, or other reparse point
- any parent component needed to reach the selected root is a reparse point
- any scanned child directory is a reparse point unless a future advanced setting explicitly includes it
- destination directory is or becomes a reparse point
- planned source or destination crosses a reparse point between preview and apply

If the safety service cannot confidently classify a path, it blocks the operation and reports `REPARSE_POINT` or `SAFETY_BLOCK`.

## Cloud Placeholders

OneDrive and other cloud-backed placeholders can hydrate or change when read. FileFlow should:

- detect placeholder attributes where possible
- show cloud status in preview
- avoid hashing by default if hashing would force hydration
- block apply if a placeholder becomes unavailable, online-only, or materially changed
- prefer `STALE_PLAN` over guessing

## Path and Name Safety

Block or require user correction for:

- reserved Windows names: `CON`, `PRN`, `AUX`, `NUL`, `COM1` to `COM9`, `LPT1` to `LPT9`
- trailing spaces or periods in filenames
- invalid Windows filename characters
- alternate data stream syntax using `:`
- paths exceeding supported limits unless long-path handling is explicitly enabled and tested
- case-insensitive destination collisions
- case-only renames that cannot be performed safely on the current filesystem

## Hardlinks

Hardlinked files may have multiple visible paths. FileFlow should record link count where available. Moving a hardlink path normally moves one directory entry, not the underlying file content. Duplicate detection must not treat hardlinked paths as independent duplicate content without explaining that relationship.

## Race Conditions

Between preview and apply, any of these make a plan stale:

- source missing
- source changed identity
- size or timestamp changed beyond the stored tolerance
- destination now exists
- root became a reparse point
- destination parent became a reparse point
- rule set changed
- category mapping changed
- destination escaped the approved boundary after normalization
- selected folder contents changed in a way that affects this operation

Apply should stop before execution when batch-level safety is stale. Per-operation staleness can be reported, but stale operations must not be recalculated silently.

## Permissions and Locked Files

Access denied, sharing violations, locked files, disk full, and unavailable destination volumes are recoverable per-operation failures unless they compromise batch integrity. They should not cause unrelated safe operations to be counted as successful or silently skipped.

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

## Milestone 1 Windows Path Policy

Milestone 1 supports only normal absolute local drive paths beneath an explicitly selected allowed folder. Supported paths must use ordinary Windows filenames that are unambiguous under Win32 case-insensitive semantics.

Milestone 1 blocks or marks unsupported:

- drive-relative paths such as `C:foo`
- filesystem roots
- UNC paths and shares
- `\\?\` extended-length paths supplied as logical user paths
- alternate data stream syntax using `:`
- trailing dot names
- trailing space names
- reserved Windows device names
- ambiguous Unicode normalization cases
- paths outside the supported normal Win32 path policy
- unsupported long paths
- path escapes outside the selected root
- unsupported case-equivalent collisions

These are `SAFETY_BLOCK` or `UNSUPPORTED` conditions, not ordinary execution failures.

Logical path comparison in Milestone 1 uses:

- Windows case-insensitive semantics
- normalized separators
- lexical logical-path containment under the approved root
- no `Path.resolve()` trust decision
- no implicit filesystem redirect following

If two path spellings are ambiguous after Windows-style case folding or Unicode normalization checks, FileFlow blocks rather than guessing.

## File Identity Model

Path, size, and modified time are not identity.

FileFlow distinguishes:

- `FileIdentity`: handle-based filesystem identity used to prove the same filesystem object is still present.
- `MetadataSnapshot`: supporting attributes used to detect material changes.

For Windows files that may eventually be actionable, FileFlow should capture identity through a Windows-specific provider using handle-based filesystem information. The minimum logical identity is:

- volume serial number or equivalent volume identity
- Windows file ID / file index
- file type
- link count where available

Supporting metadata snapshot:

- logical pathname
- size
- mtime
- ctime
- attributes
- reparse status and reparse tag

If identity cannot be obtained for a file that would eventually be movable, the planner must not mark it safely actionable. It should be `UNSUPPORTED` or `SAFETY_BLOCK`.

Selected root identity must also be captured. Relevant existing directory/path-component identity should be captured where needed to detect replacement or newly introduced redirection between preview and apply.

Implementation boundary: define a `FileIdentityProvider` interface and a future `WindowsFileIdentityProvider`. Milestone 1 may implement the interface with test doubles and Windows-specific code later, but the model and tests must assume handle-based identity, not pathname metadata alone.

## Protected Roots

The safety service should block selecting or writing directly to:

- Drive roots such as `C:\`, `D:\`, UNC share roots, and volume mount roots.
- Windows directory, normally `C:\Windows`.
- `System32` and descendants.
- `Program Files`.
- `Program Files (x86)`.
- `ProgramData`.
- User profile root, for example `C:\Users\YourName`.
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

Milestone 1 rule: block any reparse point that participates in a potentially actionable path. FileFlow should not try to prove a particular reparse target is safe in Milestone 1.

Blocked by default:

- selected root is a symlink, junction, mount point, or other reparse point
- any parent component needed to reach the selected root is a reparse point
- any scanned child directory is a reparse point
- any source path chain includes a reparse point
- any destination parent chain includes a reparse point
- any planned destination chain includes a reparse point
- destination directory is or becomes a reparse point
- planned source or destination crosses a reparse point between preview and apply
- a redirect is introduced between preview and apply
- the reparse tag is unknown or unclassified
- cloud/reparse state cannot be confidently classified

If the safety service cannot confidently classify a path, it blocks the operation and reports `REPARSE_POINT` or `SAFETY_BLOCK`.

Implementation boundary: define a `ReparseInspector` interface and a future Windows implementation. It must inspect path components without using `Path.resolve()` as the trust model. If inspection fails, FileFlow fails closed.

## Cloud Placeholders

OneDrive and other cloud-backed placeholders can hydrate or change when read. Milestone 1 blocks:

- known Files On-Demand placeholders
- offline or unhydrated cloud files
- unknown cloud reparse states
- cloud state that cannot be classified confidently

FileFlow must not automatically hydrate cloud files, read contents merely to hydrate, hash placeholders, or assume cloud sync state is stable. These are safety/unsupported conditions with clear user-facing reasons.

Future support may classify more cloud states, but the initial policy is conservative blocking.

## Path and Name Safety

Block or require user correction for:

- reserved Windows names: `CON`, `PRN`, `AUX`, `NUL`, `COM1` to `COM9`, `LPT1` to `LPT9`
- trailing spaces or periods in filenames
- invalid Windows filename characters
- alternate data stream syntax using `:`
- paths exceeding supported limits unless long-path handling is explicitly enabled and tested
- case-insensitive destination collisions
- case-only renames

Case-only renames are `UNSUPPORTED` initially. FileFlow should not implement a two-step temporary-name strategy in Milestone 1 or the first real move milestone.

Extended-length `\\?\` support is deferred. If a logical path is outside the supported normal Win32 path policy, FileFlow blocks it. Future extended-length support must be added behind explicit tests.

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

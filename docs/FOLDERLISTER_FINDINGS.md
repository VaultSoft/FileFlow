# FolderLister Findings

Reference inspected: `C:\Users\Josh\FolderLister\main.py`.

FolderLister is useful prior art for FileFlow's user intent, category vocabulary, and preview-first shape. It is not a safe implementation model for FileFlow's filesystem engine.

## Existing Behavior

### Category Definitions

FolderLister defines a simple `CATEGORIES` mapping:

- Images: jpg, jpeg, png, gif, bmp, webp, tif, tiff, svg, ico, heic, heif, avif, raw, cr2, nef, arw, dng.
- Documents: pdf, doc, docx, odt, rtf, txt, md, epub, csv, xls, xlsx, ods, ppt, pptx, odp.
- Videos: mp4, mkv, avi, mov, wmv, flv, webm, m4v, mpg, mpeg, 3gp, ts.
- Installers: exe, msi, msix, msixbundle, appx, appxbundle, dmg, pkg, deb, rpm, apk.
- Archives: zip, rar, 7z, tar, gz, tgz, bz2, xz, iso, cab.
- Code: py, html, css, dll, json, java, js, cpp, c, yml, xml, ts, sh.

The category lookup is deterministic and lowercases only the final suffix.

### Preview Behavior

`SortPreviewDialog` lists one row per planned move:

- source display: filename only
- destination display: category folder only
- tooltip: `folder / category / path.name`
- confirmation: OK renamed to Confirm plus Cancel

This is a useful preview concept, but not enough for FileFlow because it does not show final collision-renamed destinations, safety status, stale state, reasons, metadata, or undo eligibility.

### Move and Collision Behavior

FolderLister scans only immediate children of the selected folder, then moves matching files into category subfolders. Collisions are handled by `unique_destination()` using deterministic suffixes:

- `name.ext`
- `name (1).ext`
- `name (2).ext`

The final destination is recalculated at apply time, after the user has approved the preview.

### Traversal Behavior

FolderLister uses `folder.iterdir()` and `path.is_file()`.

- It does not recurse.
- It skips directories.
- It may follow symlink semantics through `Path.is_file()` depending on the filesystem object.
- It does not check reparse points, junctions, mount points, OneDrive placeholders, hardlinks, protected folders, or filesystem roots.

### Failure Behavior

Each move is attempted in a loop. `OSError` failures are collected and shown after the batch. Successful moves continue despite unrelated failures.

This partial-failure approach is good conceptually, but it records no journal and cannot support undo or robust history.

## Classification

### Reuse Conceptually

- Simple category mapping by extension.
- Human-friendly category names.
- Deterministic category order.
- Preview-before-apply workflow.
- User-selected category toggles.
- Deterministic collision naming idea.
- Partial failure reporting rather than aborting the whole batch.
- Immediate-child folder analysis as a simple initial mode.

### Redesign

- Preview must show exact final destination paths, including collision-renamed names.
- Preview must store operation IDs, file metadata, rule/category reason, safety status, conflict status, and undo eligibility.
- Collision resolution must happen during preview and be revalidated at apply without silently choosing a different destination.
- Apply must revalidate that sources, roots, destinations, rules, and metadata still match the approved plan.
- Move execution must be journaled in SQLite.
- Failure accounting must distinguish safety blocks, stale plans, locked files, permission failures, and ordinary I/O errors.
- Directory traversal must be explicit and configurable, with safe defaults.

### Do Not Reuse

- Direct `shutil.move()` calls from UI code.
- Recalculating `unique_destination()` during apply.
- Treating a preview as blanket permission to operate on whatever the filesystem looks like later.
- Unchecked `Path.is_file()` behavior around symlinks/reparse points.
- Lack of protected-root checks.
- Lack of stale-plan detection.
- Lack of undo/journal persistence.
- Lack of structured errors.

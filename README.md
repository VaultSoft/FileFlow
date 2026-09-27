# FileFlow

FileFlow is a VaultSoft Windows desktop utility for safely organising the immediate files in a folder. It creates an exact, inspectable Preview before anything moves and records every Apply and Undo attempt in a local SQLite journal.

Version `0.1.0` supports controlled same-volume moves and identity-checked Undo.

## What It Does

- analyses immediate child files without traversing folders
- groups known extensions into built-in categories
- shows exact source and destination paths before Apply
- blocks collisions, redirected paths, ambiguous Windows names, cloud placeholders, and identity changes
- moves ready files on the same volume only
- records immutable Apply history
- previews and performs identity-checked Undo to the exact original path
- stops all further mutations when an operation cannot be conclusively verified

## Safety Boundaries

FileFlow deliberately does not support:

- copying or cross-volume moves
- overwrite or automatic rename
- deletion
- recursive folder organisation
- junction, symlink, mount-point, or other reparse-backed paths
- network shares or cloud placeholders
- monitoring, scheduling, or background automation

The only raw runtime mutation primitive is `os.rename` in `fileflow/operations/same_volume_move.py`. Apply and Undo both use that same executor boundary.

## Workflow

1. Choose a normal local folder.
2. Analyse its immediate files.
3. Review every Ready, Blocked, Collision, and Unsupported row.
4. Optionally validate the Preview again.
5. Explicitly confirm Apply. Cancel is always the safe default.
6. Review the persisted result in History.
7. For successful moves, create a fresh Undo Preview before explicitly confirming Undo.

The Rules page documents the active built-in mappings and includes a filename-only tester. The tester never reads or changes files and does not replace Preview safety checks.

## Portable Use

1. Download the portable ZIP.
2. Extract the ZIP to a normal local folder.
3. Run `FileFlow.exe` from the extracted `FileFlow` folder.

No installer is required.

## Current Limits

- Windows only
- immediate child files only; no folder recursion
- same-volume moves only
- destination category folders such as `Documents` and `Images` must already exist
- maximum 100 real operations per Apply or Undo; larger plans are blocked, not truncated
- no overwrite
- no automatic rename

FileFlow never creates destination category folders. If Preview reports one missing, create it inside the selected folder and choose **Analyse Again**.

## Run From Source

```powershell
python -m pip install -r requirements.txt
python -B -m fileflow
```

FileFlow stores history in `%LOCALAPPDATA%\FileFlow\fileflow.db`.

## Test

```powershell
python -B -m unittest discover -s tests -v
python -B -m compileall -q fileflow tests
```

The suite includes Windows path-policy tests, real temporary-directory Apply and Undo integration, journal recovery coverage, mutation-boundary scanning, and workers running through genuine `QThread` event loops with file-backed SQLite databases.

## Build

```powershell
python -m pip install -r requirements-build.txt
python -B build.py
```

Expected outputs:

- `dist\FileFlow\FileFlow.exe`
- `dist\FileFlow_v0.1.0_Portable.zip`

See [Release Candidate](docs/RELEASE_CANDIDATE.md), [Safety Model](docs/SAFETY_MODEL.md), [Threading Model](docs/THREADING_MODEL.md), and [Undo Safety](docs/UNDO_SAFETY.md) for the implementation contract.

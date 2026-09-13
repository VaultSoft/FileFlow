# UX Flow

FileFlow should feel calm, inspectable, and trustworthy. The preview screen is the heart of the product.

## Home

Purpose: start a new organization plan or continue history.

Elements:

- selected recent folders
- primary action: Select Folder
- recent batches with status
- safety note for last batch if any blocked operations exist

## Select Folder

Purpose: choose the root to analyse.

Elements:

- folder picker
- selected path
- safety status
- scan options: immediate children only by default, recursive later or opt-in
- disabled Apply path until preview exists

## Analyse

Purpose: show scanning progress.

Elements:

- current phase
- files inspected
- files matched
- blocked/skipped count
- cancel button

## Preview

Most important screen.

Needs to show:

- total files planned
- total data planned
- blocked operations
- conflicts
- source path
- destination path
- rule/category reason
- safety status
- undo eligibility
- filter tabs: All, Safe, Conflicts, Blocked, Unmatched
- search/filter by filename or category
- summary grouped by destination folder

Primary actions:

- Apply Safe Plan
- Re-preview
- Export/Copy Summary later
- Cancel

Apply is disabled when batch-level safety is blocked. If only some operations are blocked, the UI must make clear whether safe operations can proceed.

## Apply

Purpose: execute the approved plan.

Elements:

- operation progress
- current operation
- success/failure counts
- bytes moved
- cancellation state

Apply must show that it is applying an approved plan, not recalculating.

## Result

Purpose: summarize exactly what happened.

Elements:

- succeeded count
- failed count
- stale count
- blocked count
- skipped count
- total bytes moved
- error list grouped by code
- View History
- Undo Eligible Operations

## History

Purpose: inspect prior batches.

Elements:

- batches sorted newest first
- date, folder, result status
- counts
- undo status
- filter by folder/status

## Undo

Purpose: preview and apply reverse operations.

Elements:

- selected batch
- eligible operations
- ineligible operations with reasons
- exact restore paths
- conflicts
- Confirm Undo

Undo uses the same preview/apply pattern as normal operations.

## Rules

Purpose: manage simple rules.

Elements:

- ordered enabled rules
- category mappings
- disabled rules
- test rule against sample filename/path
- reset defaults

Initial version can be read-only defaults plus simple toggles.

## Settings

Purpose: safe preferences.

Elements:

- default preview mode
- recursive scanning opt-in
- auto-rename setting, initially off
- history retention
- database location
- diagnostics/export logs

Avoid premium/licensing UI until Pro work begins.

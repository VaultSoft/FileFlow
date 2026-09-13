# Rules Engine and Category Model

Rules produce intentions. They do not touch the filesystem.

Every planned operation must answer: "Why is FileFlow moving this file?"

## Initial Rule Types

Safe Free-tier rule types:

- extension/category
- filename contains
- filename starts with
- filename ends with
- minimum size
- maximum size
- file age/date
- source subfolder
- destination folder

Future Pro-capable rules can add monitoring, profiles, duplicate handling, and advanced conditions without changing the preview/apply contract.

## Rule Evaluation

Initial default:

- rules have explicit `priority`, `sort_order`, and stable rule ID
- lower numeric `priority` runs first
- ties are resolved by `(priority, sort_order, id)`
- first matching enabled rule wins
- fallback rule is "do not move" unless the user enables an "Other" destination
- disabled rules are ignored but preserved
- each match records rule ID, rule version, and explanation

Multi-match behavior should be deferred until it has a clear UI. It is harder to explain and easier to make unsafe.

Filename matching defaults to Windows case-insensitive behavior. Extension matching is case-insensitive. Source subfolder matching uses normalized root-relative logical paths only, never raw absolute strings.

Rules never receive permission to perform filesystem changes. They produce planning intent only.

## Rule Conflicts

Conflicts should be resolved during preview:

- two rules propose different destinations: first enabled rule wins, and preview shows the winning reason
- destination collision: handled by collision policy
- unsafe destination: operation becomes blocked
- invalid generated name: operation becomes blocked

## Default Categories

FolderLister's category vocabulary is a good starting point:

- Images
- Documents
- Videos
- Installers
- Archives
- Code

FileFlow should add:

- Audio
- Spreadsheets can remain under Documents initially
- Other can be optional and disabled by default

Initial mappings should be hardcoded defaults loaded into database records on first run. This keeps first implementation simple while allowing later editing, import/export, and migrations.

## Unknown Extensions

Default behavior:

- unknown extensions are not moved
- preview shows them as unmatched if the user enables that filter
- no automatic "Other" move unless the user explicitly enables an Other rule

## Category Records

Category data should include:

- category ID
- display name
- destination folder name
- extension list
- enabled flag
- sort order
- version
- built-in versus user-defined flag

## Explainability

Each planned operation should show:

- matched rule name
- matched condition
- category
- destination folder
- reason text, for example: "Extension .jpg matched Images"
- rule ID
- rule version or rule snapshot

Rules should be deterministic and testable without a GUI.

Rule changes after preview make the existing preview stale.

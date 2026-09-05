# Changelog

**English** · [中文](CHANGELOG.zh-CN.md)

All notable changes to SheetMeld are documented here. Format roughly follows [Keep a Changelog](https://keepachangelog.com/).

## Unreleased

Safer spreadsheet merges and clearer conflict handling.

**Data safety**
- Keep chosen merge results intact through SVN updates, with a recovery copy available if an update fails.
- Preserve UTF-16 files and rich-text values when saving, and reject invalid output before replacing the original file.

**Merge behavior**
- Honor Keep mine, Use latest, and Skip choices, including files in subfolders; skipped files remain visible as pending updates.
- Include newly populated data columns and stop unsupported worksheet structure changes with a clear message before saving.

**Display and language**
- Keep changed rows visible after deletions, safely display table names and headers, and show worksheet warnings in Chinese or English.
- Refresh an existing worksheet warning immediately when the interface language changes.

**Internal / API**
- Return a structured error code and affected sheet names for unsupported worksheet changes so the frontend can translate the message.
- Check remote changes against each file's BASE and validate XML before an atomic replacement.

**Tests**
- Add XML/API, real local SVN repository, and frontend regressions to CI; the complete suite contains 122 cases.

## v1.5.1 (2026-08-25)

Project renamed from SmartDiff to SheetMeld for better discoverability and lower name collision with existing tools.

**Changed**
- All user-facing references (window titles, UI text, tray tooltip, exe name) now read "SheetMeld"
- GitHub repository URL updated to `noahsarkcc/sheetmeld`
- Build output renamed: `SheetMeld.exe`
- Auto-updater asset and API target updated accordingly

**Unchanged**
- Internal localStorage keys (`smartdiff_locale`, `smartdiff_diff_view`) kept for backward compatibility

## v1.5.0 (2026-06-17)

Rebuilt SVN conflict semantic-merge flow + system tray.

**Rebuilt SVN conflict semantic-merge flow**
- True three-way comparison: when an SVN text conflict is detected, the app now reads the `.r<old>` / `.mine` / `.r<new>` sidecar files exposed by `svn info --xml` as BASE / MINE / THEIRS. Fixes the previously broken comparison where the working copy was already polluted with `<<<<<<<` markers and `svn cat -r BASE` returned the freshly pulled HEAD instead of the true common ancestor
- Unified update queue: semantic merge is now the 4th choice in the conflict dialog (alongside keep mine / take theirs / skip). Pressing "Confirm update" hands the marked files to a managed semantic-merge queue, guides through them one by one, then runs a single consolidated `svn update`; merged files are resolved with `--accept working`. Fixes the old behavior where picking semantic merge jumped away, `svn update` never actually ran, and remote-only files stayed on the old revision
- Focused on what needs handling: merge mode lists only the `.xml` files currently in SVN conflict (red status dot; `get_modified_files` reports the `conflicted` state). "Unresolved only" is on by default and four pure-local-noise row classes (`added_mine` / `added_both_same` / `removed_mine` / `removed_both`) are always hidden
- Auto decisions are overridable: the `auto_mine` / `auto_theirs` / `auto_both` cells now also show the Keep mine / Use theirs / Custom buttons (highlighting the current default side) — primarily to stop `auto_mine` from silently discarding the remote-correct value when your working copy carries uncommitted dirty edits; only cells changed away from their default are sent back
- "No semantic differences" guidance: files that differ only in formatting / whitespace / attribute order (0 conflicts, 0 auto-merges) no longer show a misleading "Merging…" ellipsis; instead an action-oriented banner + green CTA card lets you `svn resolve` and advance the queue in one click
- Every key step in the update flow (and `applyMerge`) re-checks the remote revision, so the top banner / update button no longer gets stuck on "checking"

**Data safety (critical fixes)**
- Fixed critical data corruption where the directory-wide `svn update` at the end of the semantic-merge queue froze `<<<<<<<` / `=======` / `>>>>>>>` markers into the working copy: SVN re-merges those files and re-injects markers, and `--accept working` by design does not strip them, so corrupted XML was persisted as "resolved". The new flow runs `svn resolve --accept working` + `svn update --accept working <file>` for each semantic file *before* the directory update, promoting BASE to HEAD individually so the directory update no longer touches them
- SVN external-state drift defense: `/api/merge/preview` returns a `merge_signature` (`is_conflict` + `theirs_revision` + file size / nanosecond mtime), echoed back on `applyMerge`; a mismatch makes the backend return HTTP 409 + `stale: true` and the frontend silently re-runs preview. Merge mode also polls the conflict list every 30s. Covers command-line `svn resolve` / `svn update` / external editor saves
- Semantic-merge finalization now carries each file's exact target revision into `/api/svn/update`, promotes with `svn update -r <theirs_revision> --accept working <file>`, and stops before the directory update if that single-file promote fails; relative `.mine` / `.rN` sidecar paths are resolved beside the conflicted file
- Once a queued semantic merge has written any file, the UI no longer allows cancelling the queue before the final promote/update step, preventing already-merged files from being left in a state where SVN can re-inject conflict markers
- A working copy containing `<<<<<<<` / `=======` / `>>>>>>>` markers, or a vanished `.mine` sidecar, now produces a clear Chinese error instead of an ElementTree `not well-formed` ParseError
- `applyMerge` reentry guard (`mergeApplyInFlight` + button disabled + clears `mergeData` / `activeSheet` on success) prevents re-applying while a queue advance / svn update is in flight

**System tray**
- Launched via `pythonw` / `--noconsole` by default; the main process runs the pystray icon with menu items: Open browser / Show log / Open workspace / Quit
- Tray icon switched to the Hatsune Miku pixel art (face crop) to match the in-app UI, reusing `static/img/miku.svg` with no new dependency; falls back to the built-in placeholder if it cannot be parsed
- Server output is redirected to `logs/server.log` (rotated 3 × 1 MB); use the new `start_console.bat` (equivalent to `python server.py --console`) for a live console
- Tray "Show log" opens the built-in `/log` viewer in a browser (newest first, 5s auto-refresh, dark monospace header with path / line count / byte size / mtime); falls back to Notepad / xdg-open if `webbrowser.open` fails
- svn / netstat / taskkill subprocesses pass `subprocess.CREATE_NO_WINDOW` (Windows only) to stop flashing console windows
- Fixed tray-mode Flask startup crash: `_StreamToLogger.write` accepts bytes / bytearray and exposes `encoding` / `isatty` / `fileno` / `writelines`, so `click.echo` no longer kills the background Flask thread (the tray stayed up but the port never opened)

**Usability & observability**
- Switching workspaces shows a full-screen progress overlay in three steps ("Submitting switch request → Loading file list → Checking SVN status"); the three SVN loads use `Promise.allSettled` so the overlay dismisses even if one fails
- A collapsible status-dot legend at the bottom of the sidebar (red / green / red / orange / grey = SVN conflict / new / deleted / data change / metadata-only change)
- The apply popup uses `total_changes` (actual row-level ops written to disk) and the copy changed from "resolutions" to "changes", instead of misleadingly reporting "0" when only counting manual resolutions
- INFO / WARNING business logs on key API entry points for tracing via the `/log` viewer: `/api/workspaces/switch|add|remove`, `/api/merge/preview|apply|svn-mark-resolved`, and `/api/svn/update` (entry skip/theirs/mine/semantic counts + exit updated/errors counts)
- Stopped spamming `[SVN] Using: svn`: `is_available()` now uses an `_svn_announced` flag and prints only once on first successful detection

**Internal / API**
- New `svn_helper.get_conflict_info()` parses the `<conflict>` sidecar info from `svn info --xml`
- New `/api/svn/conflicted` endpoint; `/api/svn/update` accepts a `semantic_files` parameter
- New frontend `state.updateContext` queue with `_processNextSemantic` / `_finishSemanticQueue` / `cancelSemanticQueue`
- `static/js/app.js` `api()` helper attaches `err.status` / `err.body` on the error path so callers can differentiate e.g. 409 stale from a generic 500 while remaining compatible with `alert(e.message)`

**Tests**
- `test_api_merge.py` grown to 28 cases, adding data-corruption regressions (`test_smart_update_semantic_files_promoted_first`, `test_apply_rejects_poisoned_working_copy`), semantic promote failure / relative sidecar regressions, and four SVN drift regressions (conflict→resolved, resolved→conflict, mtime change, vanished `.mine` sidecar)

## v1.4.2 (2026-06-12)

Auto-update restart fix.

**Bug fixes**
- The app now restarts automatically after an in-app update; previously the new exe was installed but the program stayed closed and had to be reopened manually
- GitHub release titles no longer repeat the version number; CI releases now get a "vX.Y.Z - summary" title taken from the changelog

**Internal**
- Root cause: the update helper script inherited PyInstaller bootloader environment variables (`_PYI_*` / `_MEIPASS2`) from the dying process, so the relaunched executable mistook itself for the extracted child stage, pointed at the deleted `_MEIxxxx` temp dir and crashed on startup; `apply_update` now strips these variables
- The helper script waits with `ping` instead of `timeout` (which can exit immediately when stdin is redirected), and the give-up branch now also restarts with the correct working directory
- 3 new updater tests: bootloader env stripping, script template guard, and a locked-file integration test of the swap script (Windows)

## v1.4.1 (2026-06-11)

Diff table display fixes.

**Bug fixes**
- Split view: the old-value (red) / new-value (green) backgrounds of modified cells now always fill the full cell height, so the red/green boundary no longer looks misaligned across cells with different content lengths
- Long text in diff tables now wraps and shows in full by default, instead of requiring a mouse hover to expand

## v1.4.0 (2026-06-10)

In-app auto-update.

**New features**
- In-app update check: 5 seconds after startup the app silently queries GitHub Releases; when a newer version exists, a red dot appears on the settings (gear) button
- New "Version & Updates" section in the settings dialog: shows the current version, a manual check button and the new release notes
- One-click update (exe mode): downloads the new `SheetMeld.exe` in the background → shows a progress bar → swaps the executable and restarts → the page reloads on the new version automatically; user config (`config.json`) is untouched
- When a direct GitHub connection fails, both the check and the download automatically retry through the acceleration proxy (`github.2436666.xyz`); the working channel is remembered for the session
- When running from source, the dialog suggests `git pull` and offers direct/proxied release page links

**Release infrastructure**
- New `.github/workflows/release.yml`: pushing a `v*` tag runs the full test suite on Windows, builds with PyInstaller and attaches `SheetMeld.exe` to the release (previous releases had no exe asset; one-click updates are fully closed-loop from this version on)
- `build.bat` fixed: installs `requirements.txt` (adds the missing openpyxl/xlrd) and uses the same build flags as CI

**API**
- New `GET /api/update/check` (result cached for 1 hour, `?force=1` bypasses), `POST /api/update/download`, `GET /api/update/progress`, `POST /api/update/apply`

**Tests**
- New `tests/test_updater.py` (20 cases): version comparison, proxy fallback, `check_update` parsing, download state machine, all four endpoints; wired into CI, all 76 cases pass

## v1.3.7 (2026-06-10)

Reliability hardening release (12 fixes from a full code review).

**Data safety (high)**
- Semantic merge write-back is now atomic: the result is written to a temp file in the same directory and swapped in with `os.replace()`, so a failed write can no longer corrupt the working copy and lose uncommitted local edits
- `ss:ExpandedRowCount` / `ss:ExpandedColumnCount` are kept in sync after merge inserts rows, preventing Excel from refusing to open files whose actual extent exceeds the declared counts
- Remote conflict detection and per-file history now decode SVN percent-encoded URLs consistently, so Chinese/non-ASCII file names no longer miss conflicts or lose history entries

**Robustness (medium)**
- All file-name-accepting APIs validate that the resolved path stays inside the workspace, rejecting `..` and absolute-path traversal
- Startup port cleanup matches the local-address port exactly instead of substring matching, so processes on neighboring ports (e.g. 55660-55669) are never killed by mistake
- Smart SVN update detects conflicts via `svn status --xml` instead of sniffing localized output strings (works correctly under Chinese locales); the updated-file count excludes `Updating` / `At revision` noise lines
- Merge write-back preserves `<!-- -->` XML comments inside the document (previously silently dropped by ElementTree)

**Other improvements (low)**
- SVN file content is fetched as raw bytes and decoded per the XML declaration, supporting UTF-16 and other non-UTF-8 spreadsheet files
- Auto ID-column detection respects the "Header Row" setting; metadata rows (obj/type/desc/key) no longer break the uniqueness check
- Config reads/writes are guarded by a lock; a failed `config.json` save now surfaces a `warning` in the API response instead of being silently ignored
- Opening the workspace directory uses `os.startfile`, removing the simulated Alt-key foreground-focus hack

**Tests**
- New `tests/test_differ.py` (11 cases): non-cascading insert/delete, Pass 2/3 matching, duplicate IDs, comment-column filtering, ID detection with header_row > 1, UTF-16 parsing; wired into CI
- `test_merger.py` gains ExpandedRowCount growth and XML comment preservation regression cases (29 total)
- `test_api_merge.py` gains a path-traversal rejection case (16 total); all 56 cases pass

## v1.3.6 (2026-06-09)

**Cell diff readability**
- Smart token-level diff: consecutive digits and ASCII letters are now treated as whole tokens, so a value change like `738` -> `7074` is highlighted as one block instead of scattered red/green character fragments. Greatly improves readability for compact structured data like `{5:738626,40,6:4235,400,...}`
- New "Split" view: shows the old value and new value on two separate lines (old on top, new below), each complete and with changed parts highlighted, so old -> new can be read at a glance
- View toggle (Inline / Split) in the toolbar for local, revision and overview modes; the preference is persisted to localStorage
- Long values now wrap at token boundaries instead of mid-number

## v1.3.5 (2026-06-04)

**New feature**
- Configurable header row: a new global "Header Row" setting lets users specify which row contains column headers (default: 1). Tables with metadata rows (obj/type/desc/key) before the actual headers are now fully supported — all columns are correctly recognized and compared
- Settings dialog accessible from the header toolbar (gear icon)
- Setting is persisted in `config.json` and takes effect immediately across all modes (local diff, revision diff, browse, overview, merge)

## v1.3.4 (2026-06-04)

**Bug fixes**
- Overview mode: files with non-ASCII (e.g. Chinese) names no longer fail with "Cannot get revision content". SVN percent-encodes such names in remote URLs; the change list now decodes them so the file name displays correctly and revision content can be fetched
- Overview now fetches revision content via the repository URL, so files that are not checked out locally can still be compared
- `get_file_at_revision` / `get_file_at_revision_raw` accept a repository URL directly

## v1.3.3 (2026-06-04)

**Internationalization (i18n)**
- Web UI now supports both English and Chinese with a one-click toggle in the header
- Auto-detects browser language on first visit (zh-* → Chinese, otherwise English)
- User selection is persisted to localStorage (`sheetmeld_locale`)
- Bilingual coverage: header, sidebar, all four diff modes, semantic merge UI, SVN conflict dialog, alerts, error messages

**Documentation**
- CHANGELOG split into separate `CHANGELOG.md` (English) and `CHANGELOG.zh-CN.md` (Chinese), matching README/DEVELOPMENT bilingual style

## v1.3.2 (2026-05-27)

**File list**
- Fixed subdirectory `.xls` files not matching by relative path in the SVN change list / changes-only filter
- Added regression tests for `/api/files` recursively listing `configs/items.xls` and `/api/svn/modified` returning relative paths

## v1.3.1 (2026-05-15)

**SVN conflict detection**
- `check_only` now matches local edits and remote changes by workspace-relative path, fixing subdirectory files being missed by `basename` matching
- Added API regression tests covering subdirectory conflict scenarios like `configs/items.xml`

## v1.3.0 (2026-05-15)

**Cell-level three-way semantic merge**
- Added `xml_merger.py` supporting `BASE / MINE / THEIRS` three-way diff, auto resolution, manual resolution, and write-back
- Added `/api/merge/preview`, `/api/merge/apply`, `/api/merge/svn-mark-resolved`
- Integrated a "Semantic merge" entry into the SVN conflict dialog; XML conflicts can be resolved cell-by-cell
- Added an automated test suite and a demo SVN repository script

<details>
<summary><b>Earlier versions (v1.0 – v1.2)</b></summary>

## v1.2.0 (2026-05-14)

**Excel binary file support**
- Added `.xlsx` / `.xls` parsing using `openpyxl` / `xlrd` to extract pure cell data
- SVN historical revisions support raw bytes, so revision diffs work for Excel files

## v1.1.0 (2026-05-13)

**Diff engine**
- Reworked the three-pass row matching algorithm: Pass 1 by ID value → Pass 2 by content hash + position proximity → Pass 3 by row number fallback. Inserting/deleting a few rows no longer produces large numbers of false diffs
- Improved SVN log filtering: correctly includes file revisions created via directory copy (branches/tags)

**Overview**
- Fixed the invisible horizontal scrollbar (dynamic diff-container height) and double Y-axis scrollbars after browser zoom
- Higher-contrast scrollbars; force reflow after dynamic HTML insertion so they render immediately

**Revision compare**
- The revision dropdown no longer includes "working copy"; defaults to the two most recent SVN revisions
- Single-revision files show "only 1 revision, cannot diff between revisions"
- Scroll hints (floating change indicators) support overview mode using `position: fixed`

**Other**
- The Explorer window auto-focuses to the foreground when opening a workspace directory (Windows)
- Fixed the port number shown in `start.bat` (5000 → 5566)
- Added `__version__` version management; `/api/config` now returns version info

## v1.0.0

First release. Includes local changes, revision compare, browse, and overview modes, smart SVN update, inline diff, PyInstaller packaging, and more.

</details>

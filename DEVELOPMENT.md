# Development Guide

**English** · [中文](DEVELOPMENT.zh-CN.md)

---

## Table of Contents

- [Project Layout](#project-layout)
- [Tech Stack](#tech-stack)
- [Module Walkthrough](#module-walkthrough)
  - [xml_parser.py / xlsx_parser.py — workbook parsers](#xml_parserpy--xlsx_parserpy--workbook-parsers)
  - [xml_differ.py — semantic diff engine](#xml_differpy--semantic-diff-engine)
  - [xml_merger.py — three-way semantic merge engine](#xml_mergerpy--three-way-semantic-merge-engine)
  - [svn_helper.py — SVN integration](#svn_helperpy--svn-integration)
  - [updater.py — in-app auto-update](#updaterpy--in-app-auto-update)
  - [server.py — Flask REST API](#serverpy--flask-rest-api)
  - [static/ — frontend SPA](#static--frontend-spa)
- [Extension Guide](#extension-guide)
- [Testing](#testing)
- [Roadmap](#roadmap)

---

## Project Layout

```
sheetmeld/
├── server.py            # Flask backend, REST API entry point
├── xml_parser.py        # SpreadsheetML 2003 parser
├── xlsx_parser.py       # XLSX (Office Open XML) parser
├── xml_differ.py        # Semantic diff engine
├── xml_merger.py        # Three-way semantic merge engine (BASE/MINE/THEIRS)
├── svn_helper.py        # SVN CLI integration
├── updater.py           # In-app auto-update (GitHub Releases + acceleration proxy)
├── tray.py              # System tray integration (pystray + PIL)
├── config.json          # Workspace config (generated at runtime, .gitignored)
├── requirements.txt     # Python dependencies
├── start.bat            # Windows launcher (tray mode, pythonw silent)
├── start_console.bat    # Console launcher (--console, for debugging)
├── build.bat            # Local PyInstaller build script (kept in sync with release.yml)
├── .github/workflows/
│   ├── test.yml         # CI: full test suite on 3 Python versions
│   └── release.yml      # Pushing a v* tag builds and uploads SheetMeld.exe
├── static/              # Frontend SPA (index.html + css + js + img)
├── tests/
│   ├── TESTING.md / TESTING.zh-CN.md   # Testing guides
│   ├── test_merger.py                  # xml_merger unit tests (29 cases)
│   ├── test_differ.py                  # xml_differ unit tests (11 cases)
│   ├── test_updater.py                 # updater + /api/update/* tests (23 cases)
│   ├── test_api_merge.py               # HTTP API + mock SVN end-to-end (28 cases)
│   ├── setup_demo_svn.bat              # Bootstrap a demo SVN repo for manual UI tests
│   ├── test_regressions.py             # XML/API regressions (10 cases)
│   ├── test_svn_update.py              # Real SVN integration (9 cases)
│   ├── test_frontend.js                # Rendering + localization (12 cases)
│   └── data/                           # Three-way fixtures: base.xml / mine.xml / theirs.xml
├── README.md / README.zh-CN.md
└── DEVELOPMENT.md / DEVELOPMENT.zh-CN.md
```

---

## Tech Stack

| Layer | Technology | Notes |
|---|---|---|
| Backend | Python 3 + Flask | Lightweight web framework, no database |
| Frontend | Vanilla JS + CSS | Zero-dependency SPA, no build step |
| XML parsing | `xml.etree.ElementTree` | Standard library |
| XLSX parsing | openpyxl | `read_only` + `data_only` mode |
| VCS | SVN CLI (subprocess) | Invokes `svn` for version info |
| Packaging | PyInstaller | Builds a standalone exe via `build.bat` |

---

## Module Walkthrough

### `xml_parser.py` / `xlsx_parser.py` — workbook parsers

Both parsers expose the same output shape, so the rest of the pipeline doesn't care about the source format.

**`xml_parser.py`** — SpreadsheetML 2003:

- Namespace: `urn:schemas-microsoft-com:office:spreadsheet`
- Honors `ss:Index` (non-contiguous numbering) and `ss:MergeAcross` / `ss:MergeDown` (skip merged-cell continuations)
- `max_col` only advances when a cell actually has a value, so empty trailing columns are dropped
- Trailing empty headers are trimmed automatically

**`xlsx_parser.py`** — Office Open XML:

- Uses `openpyxl` in `read_only=True, data_only=True` — values only, no formulas or styles
- Whole-number floats normalized: `1.0` → `"1"` (string output matches `xml_parser`)
- Two input sources: `parse_file(path)` and `parse_bytes(data)` — the latter is used for SVN historical revisions

**Shared output**:

```python
{
    "sheets": {
        "Sheet1": {
            "headers": ["ID", "Name", "Value"],
            "rows": [
                {"_row": 1, "cells": {"A": "ID", "B": "Name", "C": "Value"}},
                {"_row": 2, "cells": {"A": "1001", "B": "Item", "C": "100"}},
            ],
            "row_count": 10,
            "col_count": 3,
        }
    },
    "_parse_ms": 12.5,
}
```

### `xml_differ.py` — semantic diff engine

Compares two parsed workbooks and produces structured change information.

1. **Auto ID-column detection** (`_auto_detect_id_column`): scans headers for `ID / 编号 / Key / 序号 / 索引` substrings, requires ≥ 50% non-empty unique values; falls back to scanning the first 3 columns. Both old and new sheets are detected to a consistent result. Uniqueness is evaluated only on data rows below the configured header row (`header_row`, carried in the parse output), so metadata rows (obj/type/desc/key) never break detection.
2. **Valid-column filtering** (`valid_cols`): only columns with non-empty headers participate in diff; header-less columns are treated as annotations.
3. **Three-pass row matching** (`_diff_sheet`):
   - Pass 1: by ID column value
   - Pass 2: by content hash with positional proximity (handles row-shift from inserts/deletes)
   - Pass 3: by row number (fallback for content-changed + position-shifted rows)
4. **Empty rows** (`_is_empty_row`) are skipped from ID detection and every matching pass.

### `xml_merger.py` — three-way semantic merge engine

Cell-level + row-level three-way merge over SpreadsheetML 2003 (`.xml`) workbooks (`BASE / MINE / THEIRS`). Produces an interactive merge preview and writes user resolutions back to MINE's XML file.

**Core functions**:

- `three_way_diff(base, mine, theirs, id_column=None)` — structured comparison; each row's `cells` dict provides `base / mine / theirs / status / resolved`.
- `apply_resolutions(result, resolutions)` — writes user selections (`mine / theirs / base / custom` + row-level decisions), validates no conflicts remain, returns `{ok, unresolved, applied}`.
- `write_merged_xml(source_path, result, output_path)` — incremental modifications on top of MINE's original XML AST (Cell/Data text updates, Row clone/remove), then re-serializes.

**Cell-level semantics**:

| BASE | MINE | THEIRS | Result |
|---|---|---|---|
| X | X | X | `unchanged` |
| X | X | Y | `auto_theirs` |
| X | Y | X | `auto_mine` |
| X | Y | Y | `auto_both` |
| X | Y | Z | `conflict` (manual resolution required) |

**Row-level semantics** (depend on ID-column matching):

- `added_mine` / `added_theirs` — one-side addition
- `added_both_same` / `added_both_diff` — both added the same ID; if different, requires resolution (`keep_mine` / `accept_theirs` / `merge`)
- `removed_mine` / `removed_theirs` / `removed_both` — one-side or both-side deletion
- `mine_del_theirs_mod` / `mine_mod_theirs_del` — delete-vs-edit hard conflict

**XML write-back fidelity**: the original BOM / `<?xml ?>` / `<?mso-application ?>` PI / in-document `<!-- -->` comments (parsed with `insert_comments`) / namespace style (default ns vs `ss:` prefix) are all preserved. New rows / cells set `ss:Index` explicitly to avoid breaking implicit numbering, and only the `<Data>` text node is rewritten when updating a cell — `<Cell>` style attributes (`ss:StyleID`, etc.) and inline comments stay intact.

**Write-back safety**:

- **Atomic write**: the result is serialized to a temp file in the same directory, then swapped in with a single `os.replace()`. A mid-write failure (disk full, killed process) can no longer leave a truncated file or lose uncommitted local edits.
- **Table extent maintenance** (`_update_table_extent`): if the `Table` declares `ss:ExpandedRowCount` / `ss:ExpandedColumnCount`, they are grown (never shrunk) to cover the real content extent before writing, so Excel does not refuse to open the merged file.

### `svn_helper.py` — SVN integration

Wraps the SVN CLI and handles encoding.

- Auto-detects the `svn` binary (PATH lookup, TortoiseSVN install paths)
- `_decode_output`: tries UTF-8, then GBK, then UTF-8 with `replace` (CLI messages only; file content always uses the raw byte path)
- All operations have timeout protection
- **Remote-URL strategy**: `get_log` / `get_dir_log` / `get_file_at_revision` / `get_changed_files_between_revisions` prefer the remote URL (via `get_svn_info`), so the latest revision history is visible without `svn update`
- **URL decoding**: SVN percent-encodes non-ASCII file names in URLs; `get_log` / `get_changed_files_between_revisions` `unquote` them before comparing against local paths, so conflict detection and history filtering work for Chinese file names
- **Revision filtering**: `--stop-on-copy` avoids the copy-source history; `get_log` additionally filters by path; `get_changed_files_between_revisions` skips files outside the current directory
- **Binary support**: `_run_raw` / `get_file_at_revision_raw` / `get_base_content_raw` return raw bytes. XML content also goes through the raw path and is decoded by ElementTree per its XML declaration (UTF-16 etc. supported)
- **`smart_update`**: validate workspace-relative targets, pin the update revision, preserve clean semantic/mine bytes across SVN updates, then resolve only text conflicts. Theirs uses revert followed by update; skip excludes the file and retains its BASE. Protected paths are excluded from subsequent directory updates; failure retains a recovery copy and reports errors.
- **`get_remote_update_status`**: `svn status --xml --show-updates` checks each file against its own BASE, so skipped files remain discoverable in mixed-revision working copies.
- **`get_conflict_info(filepath)`** parses the `<conflict type="text">` block exposed by `svn info --xml` and returns the BASE / MINE / THEIRS sidecar file paths (`.r<old>` / `.mine` / `.r<new>`) plus their revisions. This lets `/api/merge/preview|apply` skip the polluted working copy (which contains `<<<<<<<` markers) and read the correct three-way contents directly from the sidecar files. Non-conflict files return `None` and the merge endpoints fall back to the regular `svn cat -r BASE` / working copy / `svn cat HEAD` path

### `updater.py` — in-app auto-update

Update check / download / self-replace, implemented with the standard library only (urllib).

- **Proxy fallback** (`_fetch`): try a direct connection first (8s timeout); on failure retry via `PROXY_PREFIX + url` (`github.2436666.xyz`). The working channel is remembered for the session (`_use_proxy`) and reused for subsequent requests and the download
- **`check_update(current)`**: queries GitHub `releases/latest`; versions are compared as int tuples (`v1.3.7` → `(1,3,7)`, zero-padded to equal length); the `SheetMeld.exe` asset is looked up in the release assets, `asset_url=None` when missing (the UI degrades to "Open Release Page")
- **Download state machine**: a module-level singleton `{status: idle|downloading|ready|error, percent, downloaded, total, error, path}`; `start_download` spawns a background thread that streams into `SheetMeld.exe.new.part` and `os.replace`s it to `.new` on completion (same atomic-write idea as the merge write-back)
- **`apply_update()`** (frozen only): writes a self-deleting `sheetmeld_update.bat` next to the exe (loops `del` until the old exe is unlocked → `move` the new one in → `start` it), launches it with `DETACHED_PROCESS` and exits the old process via a delayed `os._exit(0)`. Two Windows pitfalls are handled: the helper's environment is stripped of PyInstaller bootloader variables (`_PYI_*` / `_MEIPASS2`), otherwise the relaunched exe mistakes itself for the extracted child stage and crashes on startup; and the script waits with `ping` rather than `timeout`, which can exit immediately when stdin is redirected
- **Source mode**: check works; download/apply return a "use git pull" message and touch nothing
- `config.json` lives next to the exe, so swapping the executable never affects user config

### `server.py` — Flask REST API

| Method | Path | Description |
|---|---|---|
| GET | `/api/config` | Config + SVN status + workspace list |
| GET, POST | `/api/workspaces`, `/workspaces/{switch,add,remove}` | Workspace CRUD |
| POST, GET | `/api/pick-dir`, `/api/browse-dir?path=` | Directory chooser (native tkinter; falls back to a built-in browser) |
| GET | `/api/file-mtime?file=` | Last modified time (for auto-refresh) |
| GET | `/api/files` | List workspace files (XML + XLSX) |
| GET | `/api/svn/modified[-classify]` | Locally modified files; `classify` splits into `data` (real change) vs `meta` (formatting only) |
| GET | `/api/svn/log`, `/api/svn/dir-log`, `/api/svn/changed-files` | Revision history (file / dir / between two revs) |
| GET | `/api/svn/remote-revision` | Remote HEAD vs local BASE comparison |
| POST | `/api/svn/update` | Smart SVN update (`check_only` for conflict probing; body accepts `skip_files / theirs_files / mine_files / semantic_files`) |
| GET  | `/api/svn/conflicted` | List workspace files currently in SVN conflict state (used as the merge-mode default filter) |
| GET | `/api/parse?file=` | Parse a file (auto-detects XML/XLSX) |
| POST | `/api/diff/{local,revisions,overview,batch}` | Diff endpoints |
| POST | `/api/merge/preview` | Three-way merge preview (`.xml` only) |
| POST | `/api/merge/apply` | Apply resolutions and write back |
| POST | `/api/merge/svn-mark-resolved` | Invoke `svn resolve --accept working` |
| GET | `/api/update/check` | Check for a newer release (cached 1h, `?force=1` bypasses) |
| POST | `/api/update/download` | Start the background download of the new exe |
| GET | `/api/update/progress` | Download progress / state |
| POST | `/api/update/apply` | Self-replace and restart (frozen only; 400 in source mode) |

Every endpoint accepting a `file` parameter goes through `_safe_workspace_path`: the joined path is resolved with `realpath` and must stay inside the active workspace — `..` and absolute-path traversal return 400.

### `static/` — frontend SPA

**State management**: a single global `state` object holds the current mode, file list, diff result, etc. All UI updates go through `render*` functions that re-render from state.

**Rendering optimization**: batch rendering (`BATCH_SIZE = 150` rows) with `requestAnimationFrame` appending subsequent rows; each `tbody` has a unique ID so multiple tables can be expanded simultaneously.

**Inline Diff** (`inlineDiff`): LCS-based character-level diff inside modified cells. Short strings (≤ 200 chars) diff char-by-char; longer strings are tokenized by separators first. Outputs `<del>` / `<ins>` styled to match the GitHub Dark palette.

**Auto refresh & remote revision polling**: `/api/file-mtime` is polled every 3 s — the diff is reloaded automatically when the file changes (Local / Browse modes). `/api/svn/remote-revision` is polled every 30 s — when remote HEAD is ahead of local BASE, a yellow banner appears, and clicking **Update** triggers the smart update flow (conflict detection + categorized handling). Switching workspaces resets all polling state.

**Semantic merge mode**: a 4th mode tab; the sidebar auto-filters to `.xml` only. Selecting a file calls `/api/merge/preview` for the three-way comparison and renders Sheet → Row → Cell:

- Sticky top: sheet tabs + revision source bar (BASE ← MINE · THEIRS)
- Each row is a card with header (row ID / status badge / row-level resolution buttons / collapse toggle), an always-visible row data table (the source mine / theirs / base / resolved is chosen by `row_decision`; deletions render with strike-through), and a collapsible "change details" pane (per-cell BASE / MINE / THEIRS + resolution buttons; `added_both_diff` shows MINE vs THEIRS side-by-side)
- Cell badges: `M` (mine, blue), `T` (theirs, purple), `=` (auto_both, green), `✎` (resolved, yellow), `!` (unresolved, red)
- Toolbar: auto / conflict / unresolved counts + progress bar + "show only unresolved" filter + "expand all / collapse all / smart" tri-state. Smart mode (default) expands rows that need manual resolution and collapses auto-resolved ones
- **Apply merge** calls `/api/merge/apply` with both cell and row-level selections via `collectResolutions()`
- The SVN conflict dialog renders an extra **Semantic merge** button on `.conflict-item` for `.xml` files: clicking it only marks the file with `choice='semantic'` (sitting alongside `mine / theirs / skip`), no immediate navigation. When the user presses "Confirm update", the frontend's `state.updateContext` builds a queue from every `semantic` file and guides the user through them one by one via `setMode('merge')` + `selectFile()`. Each successful `applyMerge` writes the merged content back and the backend auto-runs `svn resolve --accept working`. After the queue empties, a single `/api/svn/update` consolidates everything (mine/theirs/skip choices + `semantic_files` resolved with `--accept working`)
- In merge mode, the sidebar defaults to files currently in SVN conflict state (sourced from `/api/svn/conflicted`); a header toggle switches to "All XML"; conflicted entries get a red status dot

### Runtime modes: tray vs console

- **Tray mode (default)**: `start.bat` launches via `pythonw` → no console window → the main thread runs the pystray icon (menu: Open browser / Show log / Open workspace / Quit), Flask runs on a background thread; stdout/stderr are redirected to `logs/server.log` (`RotatingFileHandler`, 3 × 1 MB)
- **Console mode**: `start_console.bat` or `python server.py --console` → preserves the original behaviour, console shows live logs and the tray icon is skipped; intended for development / troubleshooting
- Packaging: `SheetMeld.spec`, `build.bat` and `.github/workflows/release.yml` all use `--noconsole` + `--hidden-import pystray._win32 / PIL.Image / PIL.ImageDraw`. When pystray/PIL are unavailable (e.g. missing deps), the server falls back to console mode automatically

**Internationalization (i18n)**: `static/js/i18n.js` provides a lightweight i18n framework. `I18N.messages` holds complete `zh` and `en` dictionaries. The global `t(key, ...args)` function looks up the current locale and performs `{0}`, `{1}` placeholder replacement. On load, `I18N.init()` detects the locale from `localStorage` (`sheetmeld_lang`) or `navigator.language`. Clicking the header's language toggle calls `I18N.setLocale()`, which saves the preference, applies `data-i18n` / `data-i18n-title` / `data-i18n-placeholder` attributes on static DOM elements, and calls `reRenderAll()` to regenerate all dynamic UI. Constants that used to be static objects (`ROW_STATUS_LABELS`, `CELL_STATUS_LABELS`) are now getter functions (`getRowStatusLabel`, `getCellStatusLabel`) so labels are evaluated at render time.

**Other UI details**:

- Modified-file dot indicators (orange = real data change, gray = meta only, green = added, red = removed) classified periodically via `/api/svn/modified-classify`
- Floating horizontal scroll hints `← N changes on the left` / `N changes on the right →` for wide tables; click to scroll to the nearest changed column
- Overview pane height adapts to the viewport (200–600 px clamp) to keep the horizontal scrollbar visible; expand / collapse uses local DOM updates to preserve scroll position
- Revision compare defaults to the two most recent SVN revisions; degraded UI for files with 0 or 1 revisions
- Directory chooser prefers `tkinter.filedialog.askdirectory()` and falls back to a built-in modal in PyInstaller bundles (with path input, breadcrumbs, single-click parent navigation)

---

## Extension Guide

### Adding a new validation rule

Extend `diff_workbooks` in `xml_differ.py`, or add a standalone `validate_workbook` function:

```python
def validate_workbook(parsed: dict, rules: list) -> list:
    """Run validation rules against a parsed workbook.
    rules: [{"type": "range_check", "sheet": "...", "col": "C", "min": 0, "max": 100}, ...]
    returns: [{"sheet": "...", "row": N, "col": "C", "message": "..."}]
    """
```

Then add a `/api/validate` endpoint in `server.py`.

### Adding a new data source

To support other spreadsheet formats (see `xlsx_parser.py` for reference):

1. Create a new parser next to `xml_parser.py`
2. Keep the same output shape (`sheets` > `headers` + `rows`)
3. Dispatch by extension in `server.py`'s `_parse_file` / `_parse_content`
4. For binary formats, use the `_raw` family of helpers in `svn_helper` to obtain raw bytes

### PyInstaller packaging & releasing

Run `build.bat` for a local build, which is equivalent to:

```bash
pip install -r requirements.txt pyinstaller
pyinstaller --onefile --noconsole --add-data "static;static" --hidden-import pystray._win32 --hidden-import PIL.Image --hidden-import PIL.ImageDraw --name SheetMeld server.py
```

The resulting `dist/SheetMeld.exe` runs standalone, no Python required. `config.json` is generated on first launch — don't bundle it.

**Release flow**: write the `## vX.Y.Z` section in `CHANGELOG.zh-CN.md` / `CHANGELOG.md` first, then push a `v*` tag (e.g. `git tag v1.4.0 && git push origin v1.4.0`). `.github/workflows/release.yml` runs the full test suite on a Windows runner, builds with PyInstaller, generates the release body via `.github/release_notes.py`, and attaches `SheetMeld.exe` to the GitHub Release. The in-app updater (`updater.py`) downloads exactly that asset and shows the release body as update notes.

Pending local changes may stay under `Unreleased` / `未发布`; the release generator does not publish those sections. Before a release, move them to the dated version sections and align `server.py::__version__`, both README version banners, and the release tag. A local commit alone does not create an app update.

**Release body conventions** (`.github/release_notes.py`):

- English only — the section is taken from `CHANGELOG.md`; the Chinese changelog stays in-repo
- The release page is a compact highlight summary: the one-line version intro plus at most 5 non-technical bullets (2 per changelog subsection)
- Bold subsections whose title matches the blacklist (Tests / API / infrastructure / internal / CI) are excluded from the release page
- When a release skips versions (intermediate versions never released on their own), each one is summarized as a single `Also includes vX.Y.Z: <intro line>` — so the first line under every version heading should be a one-sentence summary
- The body ends with `See CHANGELOG for details.`; technical details stay in `CHANGELOG.md`

---

## Testing

```powershell
# 1) Merge engine unit tests (no SVN required)
python tests\test_merger.py     # 29 cases

# 2) Diff engine unit tests (no SVN required)
python tests\test_differ.py     # 11 cases

# 3) Updater + /api/update/* tests (mocked network)
python tests\test_updater.py    # 23 cases

# 4) HTTP API end-to-end (mock SVN)
python tests\test_api_merge.py  # 28 cases

# 5) XML/API regressions
python tests\test_regressions.py  # 10 cases

# 6) Real SVN integration (requires svn + svnadmin)
python tests\test_svn_update.py   # 9 cases

# 7) Frontend rendering and localization (requires Node.js 22+)
node --test tests/test_frontend.js # 12 cases

# 8) Manual UI testing (requires the svn CLI)
tests\setup_demo_svn.bat        # Creates a three-way aligned demo repo at %TEMP%\xmldev_demo_svn\
start.bat                       # Then add the wc directory as a workspace from the header
```

Full walkthrough in [tests/TESTING.md](tests/TESTING.md): per-case expected results, manual UI steps, SVN conflict integration verification, and common issues.

---

## Roadmap

| Feature | Notes | Modules involved |
|---|---|---|
| Validation engine | Configurable rule system: numeric range checks, required-field validation, cross-table reference validation (e.g. column X of table A must exist in table B) | New `validator.py`, `/api/validate` |
| File favorites | Quick access to frequently used files, per-workspace | `config.json` extension |

For a full version-by-version change history, see [CHANGELOG.md](CHANGELOG.md).

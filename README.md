# SheetMeld

**English** · [中文](README.zh-CN.md)

[![License](https://img.shields.io/github/license/noahsarkcc/sheetmeld)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.8+-blue)](https://www.python.org/downloads/)
[![Tests](https://github.com/noahsarkcc/sheetmeld/actions/workflows/test.yml/badge.svg)](https://github.com/noahsarkcc/sheetmeld/actions/workflows/test.yml)
[![Release](https://img.shields.io/github/v/release/noahsarkcc/sheetmeld)](https://github.com/noahsarkcc/sheetmeld/releases)

> **v1.5.0** · Semantic diff and three-way merge for spreadsheet-based configuration data

SheetMeld is a zero-dependency, locally-runnable diff tool for structured configuration data maintained as Excel spreadsheets (`.xml` / `.xlsx` / `.xls`). It automatically filters out style, window-state, and column-width noise to show **only the real data changes**, with ID-based smart row matching, cell-level three-way semantic merge, and optional SVN integration.

- Parsing and diffing support `.xml` (SpreadsheetML 2003), `.xlsx` (Office Open XML), and `.xls`
- Cell-level three-way semantic merge currently supports `.xml` only
- Flask backend + zero-dependency frontend SPA — run with `python server.py`, or package as a single `.exe`

---

## Screenshots

**Local changes** — only the real data changes are highlighted: green rows added, red rows deleted, yellow cells modified (old → new).

<p align="center">
  <img src="assets/1.png" alt="SheetMeld local changes mode" width="900">
</p>

**Overview** — GitHub-style "Files changed" across two revisions, showing every changed file at once; pictured in split view (old value above, new value below).

<p align="center">
  <img src="assets/2.png" alt="SheetMeld overview mode" width="900">
</p>

**Semantic merge** — cell- and row-level three-way resolution over `BASE / MINE / THEIRS`, written back to the original `.xml`.

<p align="center">
  <img src="assets/3.png" alt="SheetMeld semantic merge mode" width="900">
</p>

---

## Table of Contents

- [Screenshots](#screenshots)
- [Features](#features)
- [Install & Run](#install--run)
- [Usage](#usage)
- [Testing](#testing)
- [FAQ](#faq)
- [Changelog](#changelog)
- [Documentation](#documentation)

---

## Features

| Category | Capability |
|---|---|
| **Diff core** | Four modes: local changes (working copy vs BASE), revision compare (any two SVN revisions), browse (parsed tables), and overview (GitHub-style "Files changed" across two revisions). Auto ID-column detection matches rows by content instead of row number, so inserts/deletes don't cascade into false diffs. Comment-column filtering ignores header-less annotation data. Smart token-level highlighting inside modified cells (digits/words as whole blocks), with switchable inline / split (old vs new on two lines) views. |
| **Three-way merge** | Cell-level + row-level auto merge over `BASE / MINE / THEIRS` (`.xml` only). Same-cell conflicts, delete-vs-edit, and same-ID-different-content additions can be resolved one by one and written back to the original XML. |
| **SVN integration** | Polls the remote repository for new revisions (top banner reminder); smart update with conflict categorization (keep mine / take latest / skip / semantic merge for `.xml`). On conflict, a true three-way comparison uses SVN's `.r<old>` / `.mine` / `.r<new>` sidecars and a unified merge queue, so the directory `svn update` never freezes conflict markers into your files. Revision history via the remote URL — no `svn update` required. |
| **Format & UX** | Parses `.xml` (SpreadsheetML 2003), `.xlsx` (Office Open XML), and `.xls` with a unified diff view. Configurable header row for tables with metadata rows (obj/type/desc/key) before the actual column headers. Multi-sheet, multi-workspace, auto-refresh on file change, batch rendering for large tables. |
| **System tray** | Runs in the system tray (console hidden) with a Miku pixel-art icon and an Open browser / Show log / Open workspace / Quit menu. Logs go to `logs/server.log` and are viewable live via the built-in `/log` viewer. |
| **Auto-update** | In-app update check (red dot on the settings gear); one-click download, swap and restart in exe mode. |

---

## Install & Run

### Option 1: Download the exe (recommended)

Download the latest `SheetMeld.exe` from [Releases](https://github.com/noahsarkcc/sheetmeld/releases) and double-click to run — no Python required. When a new version is released, you can update with one click from inside the app.

### Option 2: Run from source

Requirements:

- Python 3.8+
- Flask / openpyxl / xlrd (`pip install -r requirements.txt`)
- SVN command-line tools (optional; TortoiseSVN's `svn.exe` works too)

```bash
# Install dependencies
pip install -r requirements.txt

# Start the server
python server.py
```

Or just double-click `start.bat` (Windows).

The browser opens `http://localhost:5566` automatically.

### Configure workspaces

The first launch generates `config.json`. Edit it manually or add workspaces from the UI:

```json
{
  "workspaces": [
    {"name": "Project A", "path": "D:\\svn\\project_a\\xml"},
    {"name": "Project B", "path": "D:\\svn\\project_b\\xml"}
  ],
  "active_workspace": 0,
  "header_row": 1
}
```

Use the dropdown at the top of the page to switch workspaces, click `+` to add a new one, and `×` to remove the current one. `header_row` sets the header start row, which can also be changed from the in-app settings (see below).

---

## Usage

### Local changes mode

Select a file on the left to auto-compare the working copy against the SVN BASE revision. Green rows are additions, red rows are deletions, yellow cells are modifications (showing old → new).

### Inline / split view

Modified cells can be displayed in two ways: **inline** (old → new on the same line) or **split** (old value above, new value below). Toggle via the toolbar button; the choice is remembered and applies to all diff views.

### Revision compare mode

After selecting a file, pick an old and a new revision from the dropdowns, then click **Compare revisions**.

### Overview mode

No file selection needed. Pick two revision numbers and click **Compare revisions** to list every changed file with details. Supports a "data changes only" filter.

### Browse mode

View file content as tables, with multi-sheet switching.

### Semantic merge mode

After switching to **Semantic merge**, the sidebar shows the `.xml` files currently in SVN conflict only. Once you pick a file, the tool reads `BASE` and the remote revision from SVN and performs a three-way comparison against your working copy:

- Single-side edits, both-side identical edits, and edits to different columns are resolved automatically
- The same cell changed to different values, delete-vs-edit, and both-side additions with the same ID but different content require manual resolution
- Click **Apply merge & save** to write the result back to the local `.xml`
- If entered from the SVN conflict dialog, saving automatically attempts to mark the SVN conflict as resolved

#### Three-way semantics

- **BASE**: the server revision you had before this update (SVN's `.r<oldRev>` sidecar)
- **Mine**: your working copy before this update, including all uncommitted edits (SVN's `.mine`)
- **Theirs**: the latest server revision pulled in this update (SVN's `.r<newRev>`)

#### Auto-resolution rules (per-cell three-way comparison)

- **Auto · mine**: theirs didn't touch this cell, you did → defaults to keeping yours
- **Auto · theirs**: you didn't touch this cell, theirs changed it → defaults to using theirs
- **Auto · both**: both sides changed to the same value → no ambiguity
- **Conflict**: all three sides differ → manual resolution required

Every "auto" decision is overridable from the merge view (Keep mine / Use theirs / Custom); defaults are never enforced.

> "Unresolved only" is on by default, hiding auto-resolved items; uncheck it to review and override them.
> Four "no-remote-change" row classes (`added_mine` / `added_both_same` / `removed_mine` / `removed_both`) are always hidden as pure local noise.

### SVN update

When new revisions are detected on the remote, a yellow banner appears at the top of the page. Click **Update**:

- If there is no conflict, it updates directly
- If there is a conflict (local edits + remote edits), a conflict panel opens. For each conflicting file you can choose:
  - **Keep mine** — preserve local edits
  - **Take latest** — overwrite with the server version
  - **Skip** — exclude this file from the update
  - **Semantic merge** — `.xml` only; enters cell-level three-way merge

Conflict detection matches by workspace-relative path, so same-named XML files in subdirectories (e.g. `configs/items.xml`) are identified correctly.

### Settings: header start row

Click the gear icon at the top right to open settings. If the first few rows of your tables are metadata (obj/type/desc/key), set **Header start row** to the row containing the actual column names — ID detection and column display will follow it.

### Check for updates

The app checks for new versions in the background after startup; a red dot appears on the settings gear when one is available. Open settings to see the current version and click **Check for updates**:

- In exe mode, download the new version with one click — it is swapped in and restarted automatically
- When running from source, use `git pull` to get the latest code

---

## Testing

From the project root:

```powershell
python tests\test_merger.py
python tests\test_differ.py
python tests\test_updater.py
python tests\test_api_merge.py
```

Current coverage:

- `test_merger.py`: 29 merge-engine cases covering 5 cell states, 10 row-level states, resolution validation, XML write-back roundtrip, ExpandedRowCount maintenance, and comment preservation
- `test_differ.py`: 11 diff-engine cases covering three-pass row matching (ID / content hash / row-number fallback), duplicate IDs, comment-column filtering, ID detection with header_row > 1, and UTF-16 parsing
- `test_updater.py`: 23 updater cases covering version comparison, proxy fallback, release parsing, the download state machine, the self-replace update script, and the `/api/update/*` endpoints
- `test_api_merge.py`: 26 API cases covering preview / apply / svn-mark-resolved / recursive file listing / path-traversal rejection / SVN update `check_only` subdirectory conflict detection / conflict merge flow / data-corruption regressions / external-state drift

See [tests/TESTING.md](tests/TESTING.md) for the full manual test walkthrough.

---

## FAQ

**Q: Why are some columns missing?**

A: Columns without a header are treated as annotation data and won't appear in diff results. They also never make it into export artifacts.

**Q: I edited one row but it doesn't show a lot of changes — why?**

A: The tool auto-detects the ID column for smart matching. If you insert a row, only that row shows as an addition; the others are unaffected.

**Q: What if SVN isn't installed?**

A: The tool auto-detects the SVN CLI. If it's missing, "Local changes" and "Revision compare" are unavailable, but "Browse" mode still works.

**Q: The page is slow / the table stutters?**

A: Tables over 150 rows use batch rendering. If the file itself is very large (>5MB), parsing may take a few seconds.

**Q: The revision list isn't up to date?**

A: The tool now fetches revision history via the remote URL, so you see the latest commits without `svn update`. To pull file content locally, use the **Update** button at the top.

---

## Changelog

See [CHANGELOG.md](CHANGELOG.md) for the full version history.

---

## Documentation

- [Development Guide](DEVELOPMENT.md) — architecture, module internals, API list, extension guide
- [Testing Guide](tests/TESTING.md) — automated tests + manual UI walkthrough

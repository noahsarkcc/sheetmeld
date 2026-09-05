# Cell-level Semantic Merge — Testing Guide

**English** · [中文](TESTING.zh-CN.md)

This guide targets the cell-level three-way semantic merge feature introduced in v1.3 (SpreadsheetML 2003 `.xml` files only).

---

## Table of Contents

- [1. Quick verification](#1-quick-verification-5-minute-checklist)
- [2. Coverage matrix](#2-coverage-matrix)
- [3. Manual UI walkthrough](#3-manual-ui-walkthrough)
- [4. Regression coverage](#4-regression-coverage)
- [5. Troubleshooting](#5-troubleshooting)
- [6. Reporting issues](#6-reporting-issues)

---

## 1. Quick verification (5-minute checklist)

### 1.1 Run the automated tests

From the project root (`C:\xmldev`):

```powershell
# Make sure PowerShell displays Chinese correctly
chcp 65001

# 1) Merge engine unit tests (no SVN required, runs in seconds)
python tests\test_merger.py

# 2) HTTP API end-to-end tests (mock SVN, no real repo needed)
python tests\test_api_merge.py
python tests\test_differ.py
python tests\test_updater.py
python tests\test_regressions.py
python tests\test_svn_update.py
node --test tests/test_frontend.js
```

The additional regressions cover XML encoding/structure, real SVN update behavior, and frontend escaping/row rendering. SVN integration tests create disposable local file:// repositories and require both `svn` and `svnadmin` on PATH (otherwise explicitly skipped). Frontend tests require Node.js 22+. The full local suite currently runs 119 tests.

Expected tail output:

```
== All passed: 29/29 ==     (test_merger.py)
== All passed: 28/28 ==     (test_api_merge.py)
```

If anything fails, send me the full output.

---

## 2. Coverage matrix

`tests/data/` contains `base.xml` / `mine.xml` / `theirs.xml`, deliberately constructed to cover every possible merge scenario. Each row maps to a clear expected status:

| ID | BASE | MINE (local) | THEIRS (remote) | Expected status | Default resolution |
|---|---|---|---|---|---|
| **1001** | original / 10 | original / 10 | original / 10 | `unchanged` (hidden) | row kept as-is |
| **1002** | rename in mine / 20 | **new name (mine)** / 20 | rename in mine / 20 | `modified` + B = `auto_mine` | take mine's name |
| **1003** | num change in theirs / 30 | num change in theirs / 30 | num change in theirs / **33** | `modified` + C = `auto_theirs` | value becomes 33 |
| **1004** | identical both / 40 | **identical-both** / 40 | **identical-both** / 40 | `modified` + B = `auto_both` | name takes the consensus |
| **1005** | different cols / 50 | **rename (mine)** / 50 | different cols / **55** | `modified` + B = `auto_mine` + C = `auto_theirs` | B and C accepted respectively |
| **1006** | same-col conflict / 60 | **mine version** / 66 | **theirs version** / 66 | `modified` + **B = conflict** | ⚠ manual selection required for B; C = auto_both |
| **1007** | mine deletes / 70 | _(deleted)_ | mine deletes / 70 | `removed_mine` | confirm deletion |
| **1008** | theirs deletes / 80 | theirs deletes / 80 | _(deleted)_ | `removed_theirs` | accept deletion |
| **1009** | both delete / 90 | _(deleted)_ | _(deleted)_ | `removed_both` | confirm deletion |
| **1010** | mine del, theirs mod / 100 | _(deleted)_ | mine del, theirs mod / 100 / **theirs modified** | ⚠ `mine_del_theirs_mod` | **manual**: keep deletion vs restore-and-accept theirs |
| **1011** | mine mod, theirs del / 110 | mine mod, theirs del / **119** / **mine modified** | _(deleted)_ | ⚠ `mine_mod_theirs_del` | **manual**: keep my edit vs accept theirs's deletion |
| **2001** | _(absent)_ | mine adds / 201 | _(absent)_ | `added_mine` | keep addition |
| **2002** | _(absent)_ | _(absent)_ | theirs adds / 202 | `added_theirs` | accept addition |
| **2003** | _(absent)_ | both add / 203 | both add / 203 | `added_both_same` | auto-keep |
| **2004** | _(absent)_ | **mine version** / 204 / **mine note** | **theirs version** / 204 / **theirs note** | ⚠ `added_both_diff` | **manual**: keep mine / use theirs / merge per cell |

Summary:
- **3 row-level hard conflicts**: 1010, 1011, 2004
- **1 cell-level conflict**: 1006.B (numeric column, same-column different-value change)
- **Everything else** resolves automatically

---

## 3. Manual UI walkthrough

Use this to verify: merge UI interactions, resolution buttons, write-back correctness, SVN integration.

### 3.1 Prepare a local SVN demo repository

Use the one-shot script `tests/setup_demo_svn.bat`:

```cmd
cd C:\xmldev\tests
setup_demo_svn.bat
```

After running it, `%TEMP%\xmldev_demo_svn\` will contain:

```
%TEMP%\xmldev_demo_svn\
├── repo\           # SVN repository (svnadmin create)
└── wc\             # working copy, already in a "locally modified" state
    └── items.xml
```

**What it does**:
1. `svnadmin create repo` — creates a local repository
2. Commits `base.xml` as r1
3. Overwrites with `theirs.xml` and commits as r2 (simulates "someone has already pushed theirs to remote")
4. `svn update -r 1 wc` — rolls the working copy back to r1 (simulates "local BASE is r1")
5. Overwrites `items.xml` in the working copy with `mine.xml` (simulates "unconfirmed local edits")

The final state perfectly aligns the three-way merge:
- **SVN BASE** = `base.xml`
- **MINE (working copy)** = `mine.xml`
- **THEIRS (HEAD)** = `theirs.xml`

### 3.2 Launch the app

```cmd
cd C:\xmldev
start.bat
```

Or simply `python server.py`.

### 3.3 Switch to the demo workspace

1. Click the **+** button in the header and pick the `%TEMP%\xmldev_demo_svn\wc` directory
2. The workspace list switches to this directory and `items.xml` shows in the file list

### 3.4 Entry point A: standalone "Semantic merge" mode

1. Click the **Semantic merge** tab in the header
2. The sidebar shows `.xml` files only
3. Click `items.xml`
4. The main area loads the three-way comparison. The toolbar shows:

   ```
   items.xml  Compare against: HEAD  [Refresh]
   Auto-merged N · Conflicts 4 · Unresolved 4    [Apply merge & save] (disabled)
   ```

5. Resolve conflicts one by one:

| Conflict | Action | Verification |
|---|---|---|
| **Row 1010** (red border) | Click "Restore and accept theirs" | Red border disappears, unresolved -1 |
| **Row 1011** (red border) | Click "Accept theirs' deletion" | Red border disappears, unresolved -1 |
| **Row 2004** (red border) | Click "Use theirs version" | Red border disappears, unresolved -1 |
| **Row 1006 → Cell B** (red bg) | Click "Use theirs" | Red bg turns green, below shows "→ theirs version [theirs]", unresolved -1 |

6. Toolbar stats turn green: "All resolved". **Apply merge & save** lights up
7. Click **Apply merge & save**
8. Dialog: "Merge complete, N resolutions written"
9. Auto-switches back to "Local changes" mode

### 3.5 Verify the write-back

Open `%TEMP%\xmldev_demo_svn\wc\items.xml` in a file explorer (or stay in the UI and look at local changes). You should see:

- ✅ 1003's value became 33 (theirs' change)
- ✅ 1005's name is "rename (mine)" and value is 55 (each side accepted respectively)
- ✅ 1006's name became "theirs version" (user picked theirs)
- ✅ 1010 was restored, its note is "theirs modified"
- ✅ 1011 was removed
- ✅ 2002 (the row theirs added) is now present
- ✅ 2004's name is "theirs version"

### 3.6 Entry point B: SVN conflict dialog integration

1. Switch back to "Local changes" mode
2. The SVN update banner shows "1 new revision available" (because wc is still at r1)
3. Click **Update** → a "Conflict files detected" dialog opens
4. `items.xml` shows four buttons: `Keep mine` / `Take latest` / `Skip` / **`Semantic merge`** (purple)
5. Click **Semantic merge** → the dialog closes automatically and the app jumps to merge mode, loading the three-way comparison
6. Resolve conflicts → apply merge → the system automatically calls `svn resolve --accept working`
7. Verify: the dialog contains "SVN conflict marked as resolved"

### 3.7 Edge cases

| Scenario | Action | Expected |
|---|---|---|
| File with no changes | Pick an unmodified `.xml` | Shows "This sheet has no changes" |
| Picking a `.xlsx` file | Switch to the merge tab, look at the sidebar | `.xlsx` doesn't appear (filtered out) |
| Hitting the API with `.xlsx` directly | `curl -X POST -d '{"file":"x.xlsx"}'` | Returns 400 |
| Click Apply with unresolved conflicts | Click without resolving | Alert: "There are still unresolved conflicts..." |
| Custom value | Click "Custom..." on 1006.B | A prompt opens; any value is accepted |

---

## 4. Regression coverage

The table below lists what automated tests already cover:

| Dimension | Items covered | Where |
|---|---|---|
| Cell states | unchanged / auto_mine / auto_theirs / auto_both / conflict | `test_merger.py` §1 |
| Row states | All 9 states | `test_merger.py` §2 |
| Summary | Row-level + cell-level conflict counts | `test_merger.py` §3 |
| Resolution API | Unresolved, fully resolved, custom value | `test_merger.py` §4 |
| XML roundtrip | Write back → re-parse → content match | `test_merger.py` §5 |
| Preamble preservation | XML declaration / mso-application PI | `test_merger.py` §5 |
| Namespace style | Default namespace not rewritten to `ss:` prefix | `test_merger.py` §5 |
| HTTP endpoints | preview / apply / svn-mark-resolved | `test_api_merge.py` §1-3 |
| Error handling | xlsx rejected, missing file, unresolved 400 | `test_api_merge.py` |
| File listing | `/api/files` lists subdirectory `.xls` recursively | `test_api_merge.py` §4 |
| SVN conflict detection | `check_only` matches subdirectory files by relative path | `test_api_merge.py` §4 |
| Idempotency | After apply, preview leaves no leftover conflicts | `test_api_merge.py` §5 |

---

## 5. Troubleshooting

**Q1: PowerShell output is full of garbled characters.**
A: Run `chcp 65001` first, or use Windows Terminal / the VS Code integrated terminal.

**Q2: The automated tests fail with `ImportError`.**
A: You must run from the project root: `cd C:\xmldev` then `python tests\test_merger.py`. Don't `cd` into `tests` first.

**Q3: `setup_demo_svn.bat` says "svn not found".**
A: Reinstall TortoiseSVN with the "command line client tools" option checked, or install SlikSVN separately and put its `bin` on PATH. Verify: `svn --version`.

**Q4: I want to clean up the demo data.**
A: Delete `%TEMP%\xmldev_demo_svn\` entirely.

**Q5: What differences will I see in the written-back XML vs the original?**
A: We do our best to preserve the original BOM / declaration / mso-application PI / default namespace. New rows will carry an explicit `ss:Index="N"`. Re-opening in Excel and saving may make minor adjustments, but they're functionally equivalent.

---

## 6. Reporting issues

If you hit anything unexpected, please record:

1. The reproduction steps (screenshots help)
2. Output from `tests/test_merger.py` and `tests/test_api_merge.py`
3. Browser DevTools console errors (press F12 during merge UI operations)
4. If needed, attach `%TEMP%\xmldev_demo_svn\wc\items.xml`

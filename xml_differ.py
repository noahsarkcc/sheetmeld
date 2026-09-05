"""
Semantic diff engine for parsed Excel XML Spreadsheet data.
Compares two parsed workbooks at cell level, producing structured diff output.
"""
from typing import Optional
from xml_parser import col_to_letter, letter_to_col

_ID_SUBSTRINGS = {"ID", "Id", "id", "编号", "Key", "key", "KEY", "序号", "索引"}


def _is_empty_row(row: dict) -> bool:
    """A row is empty if it has no cells or all cell values are blank."""
    cells = row.get("cells", {})
    return not cells or all(v == "" for v in cells.values())


def _auto_detect_id_column(sheet: dict) -> Optional[str]:
    """Try to find a column with unique values suitable for row identity matching.

    Strategy: first check columns whose header contains known ID substrings,
    then fall back to checking the first 3 columns for value uniqueness.
    Rows at or above the configured header row (meta rows like obj/type/desc
    plus the header itself) are excluded from the uniqueness check.
    """
    rows = sheet.get("rows", [])
    headers = sheet.get("headers", [])
    header_row = sheet.get("header_row", 1)
    if len(rows) < 3:
        return None
    # Mirror build_headers: if no row sits at the configured header row, the
    # first row acts as the header instead.
    if not any(r["_row"] == header_row for r in rows):
        header_row = rows[0]["_row"]
    data_rows = [r for r in rows if r["_row"] > header_row and not _is_empty_row(r)]
    if len(data_rows) < 2:
        return None

    for i, h in enumerate(headers):
        if h and any(kw in h for kw in _ID_SUBSTRINGS):
            col = col_to_letter(i + 1)
            vals = [r["cells"].get(col, "") for r in data_rows if r["cells"].get(col, "")]
            if len(vals) >= len(data_rows) * 0.5 and len(vals) == len(set(vals)):
                return col

    for ci in range(1, min(4, len(headers) + 1)):
        col = col_to_letter(ci)
        vals = [r["cells"].get(col, "") for r in data_rows if r["cells"].get(col, "")]
        if len(vals) >= len(data_rows) * 0.5 and len(vals) == len(set(vals)):
            return col

    return None


def diff_workbooks(old: dict, new: dict, id_column: Optional[str] = None) -> dict:
    """
    Compare two parsed workbook dicts at cell level.

    Args:
        old: parsed workbook (from xml_parser.parse_*)
        new: parsed workbook (from xml_parser.parse_*)
        id_column: optional column letter to use as row identity (e.g. "A").
                   If None, rows are matched by row number.

    Returns:
        {
            "summary": {"added_sheets": int, "removed_sheets": int, ...},
            "sheets": {
                "SheetName": {
                    "status": "added"|"removed"|"modified"|"unchanged",
                    "added_rows": [{"_row": N, "cells": {...}}, ...],
                    "removed_rows": [{"_row": N, "cells": {...}}, ...],
                    "modified_cells": [
                        {"row": N, "col": "B", "header": "名称",
                         "old": "旧值", "new": "新值"},
                        ...
                    ],
                    "old_headers": [...],
                    "new_headers": [...],
                }
            }
        }
    """
    old_sheets = old.get("sheets", {})
    new_sheets = new.get("sheets", {})
    all_sheet_names = sorted(set(old_sheets.keys()) | set(new_sheets.keys()))

    sheets_diff = {}
    total_added_rows = 0
    total_removed_rows = 0
    total_modified_cells = 0
    added_sheets = 0
    removed_sheets = 0
    modified_sheets = 0

    for sheet_name in all_sheet_names:
        old_sheet = old_sheets.get(sheet_name)
        new_sheet = new_sheets.get(sheet_name)

        if old_sheet is None:
            added_sheets += 1
            total_added_rows += new_sheet["row_count"]
            sheets_diff[sheet_name] = {
                "status": "added",
                "added_rows": new_sheet["rows"],
                "removed_rows": [],
                "modified_cells": [],
                "old_headers": [],
                "new_headers": new_sheet["headers"],
            }
            continue

        if new_sheet is None:
            removed_sheets += 1
            total_removed_rows += old_sheet["row_count"]
            sheets_diff[sheet_name] = {
                "status": "removed",
                "added_rows": [],
                "removed_rows": old_sheet["rows"],
                "modified_cells": [],
                "old_headers": old_sheet["headers"],
                "new_headers": [],
            }
            continue

        sheet_result = _diff_sheet(old_sheet, new_sheet, id_column)
        sheets_diff[sheet_name] = sheet_result

        if sheet_result["status"] == "modified":
            modified_sheets += 1
        total_added_rows += len(sheet_result["added_rows"])
        total_removed_rows += len(sheet_result["removed_rows"])
        total_modified_cells += len(sheet_result["modified_cells"])

    return {
        "summary": {
            "added_sheets": added_sheets,
            "removed_sheets": removed_sheets,
            "modified_sheets": modified_sheets,
            "unchanged_sheets": len(all_sheet_names) - added_sheets - removed_sheets - modified_sheets,
            "total_added_rows": total_added_rows,
            "total_removed_rows": total_removed_rows,
            "total_modified_cells": total_modified_cells,
            "has_changes": (added_sheets + removed_sheets + modified_sheets +
                           total_added_rows + total_removed_rows + total_modified_cells) > 0,
        },
        "sheets": sheets_diff,
    }


def _diff_sheet(old_sheet: dict, new_sheet: dict, id_column: Optional[str]) -> dict:
    """Compare two sheets using three-pass matching:
    Pass 1 – ID value (exact identity)
    Pass 2 – Content hash with position proximity (handles row insertions)
    Pass 3 – Row number fallback (rows that shifted AND were modified)
    """
    effective_id_col = id_column
    if not effective_id_col:
        old_detected = _auto_detect_id_column(old_sheet)
        new_detected = _auto_detect_id_column(new_sheet)
        if old_detected and old_detected == new_detected:
            effective_id_col = old_detected
        elif old_detected and new_detected is None:
            effective_id_col = old_detected
        elif new_detected and old_detected is None:
            effective_id_col = new_detected

    old_rows = [r for r in old_sheet["rows"] if not _is_empty_row(r)]
    new_rows = [r for r in new_sheet["rows"] if not _is_empty_row(r)]

    common_pairs = []
    matched_old = set()
    matched_new = set()

    if effective_id_col:
        id_old = {}
        id_new = {}
        for r in old_rows:
            v = r["cells"].get(effective_id_col, "")
            if v:
                id_old[v] = r
        for r in new_rows:
            v = r["cells"].get(effective_id_col, "")
            if v:
                id_new[v] = r

        # Pass 1: match by ID value
        for id_val in set(id_old.keys()) & set(id_new.keys()):
            old_r = id_old[id_val]
            new_r = id_new[id_val]
            common_pairs.append((old_r, new_r))
            matched_old.add(old_r["_row"])
            matched_new.add(new_r["_row"])

    # Pass 2: match remaining by content hash (position-aware)
    # Correctly pairs rows that shifted due to insertions/deletions
    def _row_hash(row):
        return tuple(sorted(row["cells"].items()))

    hash_old = {}
    for r in old_rows:
        if r["_row"] not in matched_old:
            h = _row_hash(r)
            hash_old.setdefault(h, []).append(r)

    for r in sorted((r for r in new_rows if r["_row"] not in matched_new),
                     key=lambda r: r["_row"]):
        h = _row_hash(r)
        if h in hash_old and hash_old[h]:
            candidates = hash_old[h]
            candidates.sort(key=lambda c: abs(c["_row"] - r["_row"]))
            old_r = candidates.pop(0)
            if not candidates:
                del hash_old[h]
            common_pairs.append((old_r, r))
            matched_old.add(old_r["_row"])
            matched_new.add(r["_row"])

    # Pass 3: match remaining by row number (fallback for shifted+modified rows)
    pos_old = {r["_row"]: r for r in old_rows if r["_row"] not in matched_old}
    pos_new = {r["_row"]: r for r in new_rows if r["_row"] not in matched_new}

    for row_num in sorted(set(pos_old.keys()) & set(pos_new.keys())):
        common_pairs.append((pos_old[row_num], pos_new[row_num]))
        matched_old.add(row_num)
        matched_new.add(row_num)

    old_headers = old_sheet.get("headers", [])
    new_headers = new_sheet.get("headers", [])
    merged_headers = [
        (new_headers[i] if i < len(new_headers) and new_headers[i]
         else old_headers[i] if i < len(old_headers) else "")
        for i in range(max(len(old_headers), len(new_headers)))
    ]

    valid_cols = set()
    for i, h in enumerate(merged_headers):
        if h:
            valid_cols.add(col_to_letter(i + 1))

    def _filter_cells(row):
        filtered = {k: v for k, v in row["cells"].items() if k in valid_cols}
        return {**row, "cells": filtered}

    # Unmatched rows → added / removed
    added_rows = [r for r in (_filter_cells(r) for r in new_rows if r["_row"] not in matched_new)
                  if not _is_empty_row(r)]
    removed_rows = [r for r in (_filter_cells(r) for r in old_rows if r["_row"] not in matched_old)
                    if not _is_empty_row(r)]

    # Matched pairs → check for cell-level changes
    modified_cells = []
    modified_rows = []
    for old_r, new_r in common_pairs:
        old_cells = old_r["cells"]
        new_cells = new_r["cells"]
        all_cols = sorted((set(old_cells.keys()) | set(new_cells.keys())) & valid_cols,
                          key=lambda c: (len(c), c))

        row_changes = []
        for col in all_cols:
            old_val = old_cells.get(col, "")
            new_val = new_cells.get(col, "")
            if old_val != new_val:
                col_num = letter_to_col(col)
                header = merged_headers[col_num - 1] if col_num <= len(merged_headers) else ""
                change = {
                    "row": new_r["_row"],
                    "row_key": new_r["_row"],
                    "col": col,
                    "header": header,
                    "old": old_val,
                    "new": new_val,
                }
                modified_cells.append(change)
                row_changes.append(change)

        if row_changes:
            modified_rows.append({
                "_row": new_r["_row"],
                "cells": {k: v for k, v in new_r["cells"].items() if k in valid_cols},
                "old_cells": {k: v for k, v in old_r["cells"].items() if k in valid_cols},
                "changes": {c["col"]: c for c in row_changes},
            })

    has_changes = bool(added_rows or removed_rows or modified_cells)
    status = "modified" if has_changes else "unchanged"

    return {
        "status": status,
        "added_rows": added_rows,
        "removed_rows": removed_rows,
        "modified_cells": modified_cells,
        "modified_rows": modified_rows,
        "old_headers": old_headers,
        "new_headers": new_headers,
    }

"""
Three-way semantic merger for Excel XML Spreadsheet (SpreadsheetML 2003) workbooks.

Pipeline:
1. `three_way_diff(base, mine, theirs)` -> structured per-sheet/row/cell diff with
   pre-computed auto-resolutions and conflicts.
2. `apply_resolutions(result, resolutions)` -> fills user choices into the structure
   and validates completeness.
3. `write_merged_xml(source_path, result, output_path)` -> applies merged decisions
   to a copy of MINE's XML AST, preserving namespaces, declaration, processing
   instructions (`mso-application`), and untouched elements.
"""
import os
import re
import codecs
import tempfile
import xml.etree.ElementTree as ET
from typing import Optional

from xml_parser import (
    col_to_letter, letter_to_col,
    NS, WORKSHEET_TAG, TABLE_TAG, ROW_TAG, CELL_TAG, DATA_TAG,
    SS_NAME, SS_INDEX, SS_TYPE, SS_MERGE_ACROSS,
)
from xml_differ import _is_empty_row, _auto_detect_id_column

SS_EXPANDED_ROW_COUNT = f'{{{NS["ss"]}}}ExpandedRowCount'
SS_EXPANDED_COL_COUNT = f'{{{NS["ss"]}}}ExpandedColumnCount'


CELL_UNCHANGED = "unchanged"
CELL_AUTO_MINE = "auto_mine"
CELL_AUTO_THEIRS = "auto_theirs"
CELL_AUTO_BOTH = "auto_both"
CELL_CONFLICT = "conflict"

ROW_UNCHANGED = "unchanged"
ROW_MODIFIED = "modified"
ROW_ADDED_MINE = "added_mine"
ROW_ADDED_THEIRS = "added_theirs"
ROW_ADDED_BOTH_SAME = "added_both_same"
ROW_ADDED_BOTH_DIFF = "added_both_diff"
ROW_REMOVED_MINE = "removed_mine"
ROW_REMOVED_THEIRS = "removed_theirs"
ROW_REMOVED_BOTH = "removed_both"
ROW_MINE_DEL_THEIRS_MOD = "mine_del_theirs_mod"
ROW_MINE_MOD_THEIRS_DEL = "mine_mod_theirs_del"

ROW_CONFLICT_STATUSES = {
    ROW_ADDED_BOTH_DIFF,
    ROW_MINE_DEL_THEIRS_MOD,
    ROW_MINE_MOD_THEIRS_DEL,
}


class UnsupportedSheetChange(ValueError):
    """A structural change that the row/cell merger cannot safely write."""


def _choose_id_column(base_sheet: dict, mine_sheet: dict, theirs_sheet: dict,
                      hint: Optional[str] = None) -> Optional[str]:
    if hint:
        return hint
    for sheet in (mine_sheet, theirs_sheet, base_sheet):
        if not sheet:
            continue
        col = _auto_detect_id_column(sheet)
        if col:
            return col
    return None


def _build_row_index(rows: list, id_col: Optional[str]) -> dict:
    """Map a row's identity key -> row dict.
    Uses the ID column value when available; otherwise `row:N`.
    Handles duplicate keys by suffixing the actual row number.
    """
    idx = {}
    for r in rows:
        if _is_empty_row(r):
            continue
        if id_col:
            v = (r["cells"].get(id_col, "") or "").strip()
            key = v if v else f"row:{r['_row']}"
        else:
            key = f"row:{r['_row']}"
        if key in idx:
            key = f"{key}@{r['_row']}"
        idx[key] = r
    return idx


def _classify_cell(base_v: str, mine_v: str, theirs_v: str):
    """Three-way classify a single cell value. Returns (status, auto_resolved_value)."""
    base_v = base_v or ""
    mine_v = mine_v or ""
    theirs_v = theirs_v or ""
    if mine_v == theirs_v:
        if mine_v == base_v:
            return (CELL_UNCHANGED, mine_v)
        return (CELL_AUTO_BOTH, mine_v)
    if mine_v == base_v:
        return (CELL_AUTO_THEIRS, theirs_v)
    if theirs_v == base_v:
        return (CELL_AUTO_MINE, mine_v)
    return (CELL_CONFLICT, None)


def _rows_equal(r1: Optional[dict], r2: Optional[dict], valid_cols: set) -> bool:
    if r1 is None or r2 is None:
        return r1 is r2
    c1 = r1["cells"]
    c2 = r2["cells"]
    for col in valid_cols:
        if c1.get(col, "") != c2.get(col, ""):
            return False
    return True


def _row_cells_diff(base_row, mine_row, theirs_row, valid_cols, headers):
    base_cells = base_row["cells"] if base_row else {}
    mine_cells = mine_row["cells"] if mine_row else {}
    theirs_cells = theirs_row["cells"] if theirs_row else {}

    cells = {}
    for col in sorted(valid_cols, key=letter_to_col):
        b = base_cells.get(col, "")
        m = mine_cells.get(col, "")
        t = theirs_cells.get(col, "")
        status, resolved = _classify_cell(b, m, t)
        col_num = letter_to_col(col)
        header = headers[col_num - 1] if col_num <= len(headers) else ""
        cells[col] = {
            "base": b, "mine": m, "theirs": t,
            "status": status,
            "resolved": resolved,
            "header": header,
        }
    return cells


def _initial_row_decision(row_status: str) -> Optional[str]:
    """Default action that fully reflects the row's status.
    For row-level conflicts, returns None (user must choose).
    """
    if row_status == ROW_UNCHANGED:
        return "keep"
    if row_status == ROW_MODIFIED:
        return "merge"
    if row_status == ROW_ADDED_MINE:
        return "keep_mine"
    if row_status == ROW_ADDED_THEIRS:
        return "accept_theirs"
    if row_status == ROW_ADDED_BOTH_SAME:
        return "keep"
    if row_status == ROW_REMOVED_MINE:
        return "keep_mine_delete"
    if row_status == ROW_REMOVED_THEIRS:
        return "accept_theirs_delete"
    if row_status == ROW_REMOVED_BOTH:
        return "delete"
    return None


def _key_sort_value(key, mine_idx, theirs_idx, base_idx):
    """Sort key by mine row number first, theirs second, base third."""
    if key in mine_idx:
        return (0, mine_idx[key]["_row"])
    if key in theirs_idx:
        return (1, theirs_idx[key]["_row"])
    if key in base_idx:
        return (2, base_idx[key]["_row"])
    return (3, 0)


def _diff_sheet(base_sheet, mine_sheet, theirs_sheet, id_col_hint=None) -> dict:
    id_col = _choose_id_column(base_sheet, mine_sheet, theirs_sheet, id_col_hint)

    base_rows = base_sheet.get("rows", []) if base_sheet else []
    mine_rows = mine_sheet.get("rows", []) if mine_sheet else []
    theirs_rows = theirs_sheet.get("rows", []) if theirs_sheet else []

    base_idx = _build_row_index(base_rows, id_col)
    mine_idx = _build_row_index(mine_rows, id_col)
    theirs_idx = _build_row_index(theirs_rows, id_col)

    headers_candidates = [
        mine_sheet.get("headers", []) if mine_sheet else [],
        theirs_sheet.get("headers", []) if theirs_sheet else [],
        base_sheet.get("headers", []) if base_sheet else [],
    ]
    # A previously blank header can become a data column on either side.
    # Selecting just the longest list silently discards those columns on ties.
    headers = [next((hs[i] for hs in headers_candidates if i < len(hs) and hs[i]), "")
               for i in range(max(map(len, headers_candidates)))]

    valid_cols = set()
    for i, h in enumerate(headers):
        if h:
            valid_cols.add(col_to_letter(i + 1))

    all_keys = sorted(
        set(base_idx) | set(mine_idx) | set(theirs_idx),
        key=lambda k: _key_sort_value(k, mine_idx, theirs_idx, base_idx),
    )

    out_rows = []
    auto_count = 0
    cell_conflicts = 0
    row_conflicts = 0

    for key in all_keys:
        b = base_idx.get(key)
        m = mine_idx.get(key)
        t = theirs_idx.get(key)

        if b is None and m is None and t is None:
            continue

        row_status = None
        if b is None:
            if m is not None and t is None:
                row_status = ROW_ADDED_MINE
            elif m is None and t is not None:
                row_status = ROW_ADDED_THEIRS
            else:
                same = _rows_equal(m, t, valid_cols)
                row_status = ROW_ADDED_BOTH_SAME if same else ROW_ADDED_BOTH_DIFF
        elif m is None and t is None:
            row_status = ROW_REMOVED_BOTH
        elif m is None:
            row_status = ROW_REMOVED_MINE if _rows_equal(b, t, valid_cols) else ROW_MINE_DEL_THEIRS_MOD
        elif t is None:
            row_status = ROW_REMOVED_THEIRS if _rows_equal(b, m, valid_cols) else ROW_MINE_MOD_THEIRS_DEL

        cells = _row_cells_diff(b, m, t, valid_cols, headers)

        if row_status is None:
            has_changes = any(c["status"] != CELL_UNCHANGED for c in cells.values())
            row_status = ROW_MODIFIED if has_changes else ROW_UNCHANGED

        is_row_conflict = row_status in ROW_CONFLICT_STATUSES
        if is_row_conflict:
            row_conflicts += 1

        # auto_resolved cells are always informative; cell-level conflicts only
        # count toward summary when the row will actually surface them to the
        # user (i.e. plain modified rows). For row-conflict rows the user
        # decides at the row level first.
        for c in cells.values():
            s = c["status"]
            if s in (CELL_AUTO_MINE, CELL_AUTO_THEIRS, CELL_AUTO_BOTH):
                auto_count += 1
            elif s == CELL_CONFLICT and row_status == ROW_MODIFIED:
                cell_conflicts += 1

        row_decision = _initial_row_decision(row_status)

        if row_status == ROW_UNCHANGED:
            continue

        out_rows.append({
            "row_key": key,
            "row_num_base": b["_row"] if b else None,
            "row_num_mine": m["_row"] if m else None,
            "row_num_theirs": t["_row"] if t else None,
            "status": row_status,
            "row_decision": row_decision,
            "is_row_conflict": is_row_conflict,
            "cells": cells,
        })

    return {
        "id_column": id_col,
        "headers": headers,
        "valid_cols": sorted(valid_cols, key=letter_to_col),
        "rows": out_rows,
        "auto_resolved_count": auto_count,
        "cell_conflict_count": cell_conflicts,
        "row_conflict_count": row_conflicts,
        "conflict_count": cell_conflicts + row_conflicts,
    }


def three_way_diff(base: dict, mine: dict, theirs: dict,
                   id_column: Optional[str] = None) -> dict:
    """Compute a three-way diff across all sheets in the three workbook dicts.

    Each input is the output of `xml_parser.parse_*`.
    """
    base_sheets = base.get("sheets", {}) if base else {}
    mine_sheets = mine.get("sheets", {}) if mine else {}
    theirs_sheets = theirs.get("sheets", {}) if theirs else {}

    changed_names = set(base_sheets) ^ set(theirs_sheets)
    # Local-only additions are already present in the template. Local deletion
    # is also safe unless THEIRS changed the deleted sheet in the meantime.
    changed_names.update(name for name in set(base_sheets) - set(mine_sheets)
                         if name in theirs_sheets and base_sheets[name] != theirs_sheets[name])
    if changed_names:
        raise UnsupportedSheetChange(
            "暂不支持合并工作表新增/删除：" + ", ".join(sorted(changed_names)) +
            "。请先在表格编辑器中处理工作表结构后重新预览；原文件未修改。")

    all_names = sorted(set(base_sheets) | set(mine_sheets) | set(theirs_sheets))
    sheets_out = {}
    total_auto = 0
    total_cell_conflicts = 0
    total_row_conflicts = 0

    for name in all_names:
        b = base_sheets.get(name) or {"rows": [], "headers": []}
        m = mine_sheets.get(name)
        t = theirs_sheets.get(name)

        if m is None and t is None:
            continue

        if m is None:
            # THEIRS is unchanged from BASE (checked above): keep the local
            # sheet deletion instead of misclassifying it as a remote addition.
            continue
        if t is None:
            sheets_out[name] = {
                "id_column": None,
                "headers": m.get("headers", []),
                "valid_cols": [],
                "rows": [],
                "sheet_status": "mine_only",
                "auto_resolved_count": 0,
                "cell_conflict_count": 0,
                "row_conflict_count": 0,
                "conflict_count": 0,
            }
            continue

        result = _diff_sheet(b, m, t, id_column)
        sheets_out[name] = result
        total_auto += result["auto_resolved_count"]
        total_cell_conflicts += result["cell_conflict_count"]
        total_row_conflicts += result["row_conflict_count"]

    return {
        "sheets": sheets_out,
        "summary": {
            "auto_resolved": total_auto,
            "cell_conflicts": total_cell_conflicts,
            "row_conflicts": total_row_conflicts,
            "conflicts": total_cell_conflicts + total_row_conflicts,
            "sheets": len(sheets_out),
        },
    }


def apply_resolutions(three_way_result: dict, resolutions: list) -> dict:
    """Apply user resolutions to a three-way diff result (mutates input).

    Each resolution is either:
      Cell-level: {"sheet", "row_key", "col", "choice": mine|theirs|base|custom, "value"?}
      Row-level:  {"sheet", "row_key", "choice": <one of the row decisions>}

    Returns: {"ok": bool, "unresolved": [{sheet, row_key, col?, kind}], "applied": int}
    """
    sheets = three_way_result.get("sheets", {})
    applied = 0

    for res in resolutions or []:
        sheet_name = res.get("sheet")
        row_key = res.get("row_key")
        col = res.get("col")
        choice = res.get("choice")
        value = res.get("value", "")

        sheet = sheets.get(sheet_name)
        if not sheet:
            continue
        row = next((r for r in sheet["rows"] if r["row_key"] == row_key), None)
        if not row:
            continue

        if col is None:
            row["row_decision"] = choice
            applied += 1
            continue

        cell = row["cells"].get(col)
        if not cell:
            continue
        if choice == "mine":
            cell["resolved"] = cell["mine"]
        elif choice == "theirs":
            cell["resolved"] = cell["theirs"]
        elif choice == "base":
            cell["resolved"] = cell["base"]
        elif choice == "custom":
            cell["resolved"] = value
        else:
            continue
        applied += 1

    unresolved = []
    for sheet_name, sheet in sheets.items():
        for row in sheet["rows"]:
            if row["is_row_conflict"] and row["row_decision"] is None:
                unresolved.append({
                    "sheet": sheet_name,
                    "row_key": row["row_key"],
                    "kind": "row",
                })
                continue
            if _row_keeps_cells(row):
                for col, cell in row["cells"].items():
                    if cell["status"] == CELL_CONFLICT and cell["resolved"] is None:
                        unresolved.append({
                            "sheet": sheet_name,
                            "row_key": row["row_key"],
                            "col": col,
                            "kind": "cell",
                        })

    return {"ok": not unresolved, "unresolved": unresolved, "applied": applied}


def _row_keeps_cells(row: dict) -> bool:
    """True if a row decision yields a kept/merged row whose cells matter."""
    decision = row.get("row_decision")
    status = row.get("status")
    if status == ROW_MODIFIED:
        return True
    if status == ROW_ADDED_BOTH_DIFF:
        return decision == "merge"
    return False


# ── XML mutation ──────────────────────────────────────────────────────────


def _compute_sheet_operations(sheet_result: dict) -> dict:
    """Translate per-row decisions into low-level XML mutation operations."""
    ops = {
        "update_rows": [],
        "remove_rows": [],
        "insert_rows": [],
    }
    for row in sheet_result.get("rows", []):
        status = row["status"]
        decision = row["row_decision"]
        mine_num = row["row_num_mine"]

        if status == ROW_UNCHANGED:
            continue

        if status == ROW_MODIFIED:
            updates = _collect_resolved_diffs(row)
            if updates and mine_num is not None:
                ops["update_rows"].append({"row_num_mine": mine_num, "cells": updates})
            continue

        if status in (ROW_ADDED_MINE, ROW_ADDED_BOTH_SAME):
            continue

        if status == ROW_ADDED_THEIRS:
            if decision == "accept_theirs":
                cells = _cells_from_side(row, "theirs")
                ops["insert_rows"].append({"cells": cells, "source": "theirs"})
            continue

        if status == ROW_ADDED_BOTH_DIFF:
            if decision == "keep_mine":
                continue
            if decision == "accept_theirs":
                updates = {col: c["theirs"] for col, c in row["cells"].items()}
                if mine_num is not None:
                    ops["update_rows"].append({
                        "row_num_mine": mine_num,
                        "cells": updates,
                    })
            elif decision == "merge":
                updates = _collect_resolved_diffs(row)
                if updates and mine_num is not None:
                    ops["update_rows"].append({"row_num_mine": mine_num, "cells": updates})
            continue

        if status == ROW_REMOVED_MINE:
            if decision == "accept_theirs":
                cells = _cells_from_side(row, "theirs")
                ops["insert_rows"].append({"cells": cells, "source": "theirs"})
            continue

        if status == ROW_REMOVED_THEIRS:
            if decision == "accept_theirs_delete" and mine_num is not None:
                ops["remove_rows"].append(mine_num)
            continue

        if status == ROW_REMOVED_BOTH:
            continue

        if status == ROW_MINE_DEL_THEIRS_MOD:
            if decision == "accept_theirs":
                cells = _cells_from_side(row, "theirs")
                ops["insert_rows"].append({"cells": cells, "source": "theirs"})
            continue

        if status == ROW_MINE_MOD_THEIRS_DEL:
            if decision == "accept_theirs_delete" and mine_num is not None:
                ops["remove_rows"].append(mine_num)
            continue

    return ops


def _collect_resolved_diffs(row: dict) -> dict:
    """Return {col: value} for cells whose resolved value differs from MINE."""
    out = {}
    for col, cell in row["cells"].items():
        resolved = cell.get("resolved")
        if resolved is None:
            continue
        if resolved != cell["mine"]:
            out[col] = resolved
    return out


def _cells_from_side(row: dict, side: str) -> dict:
    out = {}
    for col, cell in row["cells"].items():
        v = cell.get(side, "")
        if v:
            out[col] = v
    return out


def _read_xml_text(filepath: str) -> tuple:
    """Decode XML without losing its BOM or declared byte encoding."""
    with open(filepath, "rb") as f:
        raw = f.read()
    for bom, encoding in ((codecs.BOM_UTF32_LE, "utf-32-le"),
                          (codecs.BOM_UTF32_BE, "utf-32-be"),
                          (codecs.BOM_UTF8, "utf-8"),
                          (codecs.BOM_UTF16_LE, "utf-16-le"),
                          (codecs.BOM_UTF16_BE, "utf-16-be")):
        if raw.startswith(bom):
            return bom, encoding, raw[len(bom):].decode(encoding)
    if raw.startswith(b"<\x00"):
        encoding = "utf-16-le"
    elif raw.startswith(b"\x00<"):
        encoding = "utf-16-be"
    else:
        declaration = re.match(br'''\s*<\?xml\b[^>]*\bencoding\s*=\s*["']([^"']+)["']''', raw)
        encoding = declaration.group(1).decode("ascii") if declaration else "utf-8"
    return b"", encoding, raw.decode(encoding)


def _read_preamble(filepath: str) -> tuple:
    """Return (bom, encoding, preamble) before the root element."""
    bom, encoding, text = _read_xml_text(filepath)

    pos = 0
    n = len(text)
    while pos < n:
        idx = text.find("<", pos)
        if idx < 0:
            return bom, encoding, text
        nxt = text[idx + 1] if idx + 1 < n else ""
        if nxt.isalpha() or nxt == "_":
            return bom, encoding, text[:idx]
        if nxt == "?":
            end = text.find("?>", idx)
            if end < 0:
                return bom, encoding, text
            pos = end + 2
        elif nxt == "!":
            if text[idx:idx + 4] == "<!--":
                end = text.find("-->", idx)
                if end < 0:
                    return bom, encoding, text
                pos = end + 3
            else:
                end = text.find(">", idx)
                if end < 0:
                    return bom, encoding, text
                pos = end + 1
        else:
            return bom, encoding, text[:idx]
    return bom, encoding, text


def _find_row_map(table_el):
    """Build {computed_row_num: row_element} mirroring xml_parser's traversal."""
    row_map = {}
    row_idx = 0
    for row_el in table_el.findall(ROW_TAG):
        idx_attr = row_el.get(SS_INDEX)
        if idx_attr:
            try:
                row_idx = int(idx_attr)
            except ValueError:
                row_idx += 1
        else:
            row_idx += 1
        row_map[row_idx] = row_el
    return row_map


def _find_or_create_cell(row_el, target_col_num: int):
    """Locate a Cell element by computed column index. Create one (with explicit
    ss:Index) if missing.
    """
    col_idx = 0
    for cell_el in row_el.findall(CELL_TAG):
        idx_attr = cell_el.get(SS_INDEX)
        if idx_attr:
            try:
                col_idx = int(idx_attr)
            except ValueError:
                col_idx += 1
        else:
            col_idx += 1
        if col_idx == target_col_num:
            return cell_el
        if col_idx > target_col_num:
            new_cell = ET.Element(CELL_TAG, {SS_INDEX: str(target_col_num)})
            row_el.insert(list(row_el).index(cell_el), new_cell)
            return new_cell
        merge = cell_el.get(SS_MERGE_ACROSS)
        if merge:
            merged_end = col_idx + int(merge)
            if col_idx < target_col_num <= merged_end:
                raise ValueError("Cannot write into a merged-cell continuation")
            col_idx = merged_end

    new_cell = ET.SubElement(row_el, CELL_TAG)
    new_cell.set(SS_INDEX, str(target_col_num))
    return new_cell


def _set_cell_value(cell_el, value: str):
    """Update or create the <Data> child of a Cell element."""
    data_el = cell_el.find(DATA_TAG)
    if data_el is None and value:
        data_el = ET.SubElement(cell_el, DATA_TAG)
        data_el.set(SS_TYPE, "String")
    if data_el is not None:
        # Rich text lives in nested HTML elements, not only Data.text.
        for child in list(data_el):
            data_el.remove(child)
        data_el.text = value or ""


def _build_row(table_el, row_num: int, cells_dict: dict):
    """Append a new Row to table_el with explicit ss:Index attrs."""
    new_row = ET.SubElement(table_el, ROW_TAG)
    new_row.set(SS_INDEX, str(row_num))
    for col in sorted(cells_dict.keys(), key=letter_to_col):
        v = cells_dict[col]
        if not v:
            continue
        col_num = letter_to_col(col)
        cell_el = ET.SubElement(new_row, CELL_TAG)
        cell_el.set(SS_INDEX, str(col_num))
        data_el = ET.SubElement(cell_el, DATA_TAG)
        data_el.set(SS_TYPE, "String")
        data_el.text = v
    return new_row


def _apply_sheet_ops(table_el, ops: dict):
    """Apply update/remove/insert operations to a Table element."""
    row_map = _find_row_map(table_el)

    for u in ops["update_rows"]:
        row_el = row_map.get(u["row_num_mine"])
        if row_el is None:
            continue
        for col, value in u["cells"].items():
            col_num = letter_to_col(col)
            cell_el = _find_or_create_cell(row_el, col_num)
            _set_cell_value(cell_el, value)

    for row_num in ops["remove_rows"]:
        row_el = row_map.get(row_num)
        if row_el is not None:
            try:
                table_el.remove(row_el)
            except ValueError:
                pass

    if ops["insert_rows"]:
        current_max = max(row_map.keys()) if row_map else 0
        for ins in ops["insert_rows"]:
            current_max += 1
            _build_row(table_el, current_max, ins["cells"])


def _update_table_extent(table_el):
    """Keep ss:ExpandedRowCount / ExpandedColumnCount >= the real content extent.

    SpreadsheetML readers (Excel) refuse to open files whose actual row/column
    count exceeds the declared expanded counts. After inserting rows or cells we
    must grow these attributes. We only ever grow (never shrink) so SVN diffs
    stay minimal and trailing blank space is preserved.
    """
    max_row = 0
    max_col = 0
    row_idx = 0
    for row_el in table_el.findall(ROW_TAG):
        idx_attr = row_el.get(SS_INDEX)
        if idx_attr:
            try:
                row_idx = int(idx_attr)
            except ValueError:
                row_idx += 1
        else:
            row_idx += 1
        if row_idx > max_row:
            max_row = row_idx

        col_idx = 0
        for cell_el in row_el.findall(CELL_TAG):
            c_attr = cell_el.get(SS_INDEX)
            if c_attr:
                try:
                    col_idx = int(c_attr)
                except ValueError:
                    col_idx += 1
            else:
                col_idx += 1
            if col_idx > max_col:
                max_col = col_idx
            merge = cell_el.get(SS_MERGE_ACROSS)
            if merge:
                try:
                    col_idx += int(merge)
                    if col_idx > max_col:
                        max_col = col_idx
                except ValueError:
                    pass

    rc = table_el.get(SS_EXPANDED_ROW_COUNT)
    if rc is not None:
        try:
            table_el.set(SS_EXPANDED_ROW_COUNT, str(max(int(rc), max_row)))
        except ValueError:
            table_el.set(SS_EXPANDED_ROW_COUNT, str(max_row))

    cc = table_el.get(SS_EXPANDED_COL_COUNT)
    if cc is not None:
        try:
            table_el.set(SS_EXPANDED_COL_COUNT, str(max(int(cc), max_col)))
        except ValueError:
            table_el.set(SS_EXPANDED_COL_COUNT, str(max_col))


_DEFAULT_NS_PATTERN = re.compile(
    r'<\w+\b[^>]*\bxmlns\s*=\s*"urn:schemas-microsoft-com:office:spreadsheet"',
    re.IGNORECASE,
)


def _detect_default_ns_style(source_path: str) -> bool:
    """True if the original XML declares SpreadsheetML as default namespace
    (elements written without `ss:` prefix). When True, we post-process the
    ET output to match that style and keep SVN diffs minimal.
    """
    try:
        _bom, _encoding, text = _read_xml_text(source_path)
        return bool(_DEFAULT_NS_PATTERN.search(text))
    except OSError:
        return False


def _strip_ss_element_prefix(body: str) -> str:
    """Rewrite `<ss:Foo>` -> `<Foo>` for element tags only; keep attributes
    (`ss:Name="..."`) unchanged. Also adds a default `xmlns=` declaration on
    the root element alongside the existing `xmlns:ss=`.
    """
    body = re.sub(r'<(/?)ss:([A-Za-z_][\w.-]*)', r'<\1\2', body)
    body = body.replace(
        'xmlns:ss="urn:schemas-microsoft-com:office:spreadsheet"',
        'xmlns="urn:schemas-microsoft-com:office:spreadsheet" '
        'xmlns:ss="urn:schemas-microsoft-com:office:spreadsheet"',
        1,
    )
    return body


def write_merged_xml(source_path: str, three_way_result: dict, output_path: str):
    """Apply merge decisions to a copy of MINE XML and write to output_path.

    Preserves: BOM, XML declaration, mso-application processing instruction,
    and the original namespace prefix style (default-namespace vs ss-prefix)
    so SVN diffs remain readable.
    """
    for prefix, uri in NS.items():
        ET.register_namespace(prefix, uri)

    use_default_ns = _detect_default_ns_style(source_path)
    bom, encoding, preamble = _read_preamble(source_path)

    # Preserve XML comments inside the document body (TreeBuilder insert_comments
    # requires Python 3.8+, which is our minimum). The default parser drops them.
    parser = ET.XMLParser(target=ET.TreeBuilder(insert_comments=True))
    tree = ET.parse(source_path, parser=parser)
    root = tree.getroot()

    sheets = three_way_result.get("sheets", {})
    if any(s.get("sheet_status") == "added_theirs" for s in sheets.values()):
        raise UnsupportedSheetChange("暂不支持写回远端新增工作表；原文件未修改。")
    for ws_el in root.iter(WORKSHEET_TAG):
        name = ws_el.get(SS_NAME, "Unknown")
        sheet_result = sheets.get(name)
        if not sheet_result:
            continue
        table_el = ws_el.find(TABLE_TAG)
        if table_el is None:
            continue
        ops = _compute_sheet_operations(sheet_result)
        _apply_sheet_ops(table_el, ops)
        _update_table_extent(table_el)

    body_text = ET.tostring(root, encoding="unicode")
    if use_default_ns:
        body_text = _strip_ss_element_prefix(body_text)
    if not preamble:
        preamble = '<?xml version="1.0"?>\n'
    output_bytes = bom + (preamble + body_text).encode(encoding, errors="xmlcharrefreplace")
    # Reject malformed output (including custom values with XML control chars)
    # before touching the working copy or resolving an SVN conflict.
    ET.fromstring(output_bytes)

    # Atomic write: serialize to a temp file in the same directory, then replace
    # the target in one os.replace() call. A crash mid-write can no longer leave
    # the working copy truncated and lose the user's uncommitted edits.
    out_dir = os.path.dirname(os.path.abspath(output_path)) or "."
    fd, tmp_path = tempfile.mkstemp(dir=out_dir, suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(output_bytes)
        os.replace(tmp_path, output_path)
    except Exception:
        try:
            os.remove(tmp_path)
        except OSError:
            pass
        raise

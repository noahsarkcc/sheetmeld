"""Review regressions: XML roundtrips, structural guards and HTTP behavior.

Run with: python tests/test_regressions.py
"""
import codecs
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import server
import xml_parser as xp
import xml_merger as xm
import xml_differ as xd


def workbook(rows, name="Items"):
    root = ET.Element("Workbook", {"xmlns": xp.NS["ss"], "xmlns:ss": xp.NS["ss"],
                                   "xmlns:html": xp.NS["html"]})
    table = ET.SubElement(ET.SubElement(root, "Worksheet", {"ss:Name": name}), "Table")
    for values in rows:
        row = ET.SubElement(table, "Row")
        for i, value in enumerate(values, 1):
            if value is None:
                continue
            cell = ET.SubElement(row, "Cell", {"ss:Index": str(i)})
            ET.SubElement(cell, "Data", {"ss:Type": "String"}).text = value
    return ET.tostring(root, encoding="unicode")


ROWS = [["ID", "Value"], ["1", "old"], ["2", "stable"]]


class MergeRegressions(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="sheetmeld-regression-")
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name, "items.xml")

    def merge(self, base, mine, theirs, resolutions=None):
        def data(value):
            return value.encode("utf-8") if isinstance(value, str) else value
        self.path.write_bytes(data(mine))
        result = xm.three_way_diff(*(xp.parse_string(data(v)) for v in (base, mine, theirs)))
        self.assertTrue(xm.apply_resolutions(result, resolutions or [])["ok"])
        xm.write_merged_xml(str(self.path), result, str(self.path))
        return xp.parse_file(str(self.path))

    def test_utf16_and_utf8_bom_roundtrip(self):
        for encoding, bom in (("utf-16-le", codecs.BOM_UTF16_LE),
                              ("utf-16-be", codecs.BOM_UTF16_BE),
                              ("utf-8", codecs.BOM_UTF8)):
            with self.subTest(encoding=encoding):
                declaration = "UTF-16" if encoding.startswith("utf-16") else "UTF-8"
                xml = '<?xml version="1.0" encoding="' + declaration + '"?>\n'
                xml += '<?mso-application progid="Excel.Sheet"?>\n' + workbook(ROWS)
                base = bom + xml.encode(encoding)
                theirs = bom + xml.replace(">old<", ">新值<").encode(encoding)
                parsed = self.merge(base, base, theirs)
                self.assertEqual(parsed["sheets"]["Items"]["rows"][1]["cells"]["B"], "新值")
                output = self.path.read_bytes()
                self.assertTrue(output.startswith(bom))
                text = output[len(bom):].decode(encoding)
                self.assertIn('<?mso-application progid="Excel.Sheet"?>', text)
                self.assertNotIn("<ss:Workbook", text)

    def test_rich_text_replace_and_clear(self):
        plain = workbook(ROWS)
        rich = plain.replace(">old<", "><html:B>old</html:B><")
        for replacement in ("new", ""):
            with self.subTest(replacement=replacement):
                parsed = self.merge(rich, rich, plain.replace(">old<", ">" + replacement + "<"))
                self.assertEqual(parsed["sheets"]["Items"]["rows"][1]["cells"].get("B", ""), replacement)

    def test_new_data_column_and_sparse_cell_order(self):
        base = workbook([["ID", None, "Value"], ["1", None, "a"], ["2", None, "b"]])
        theirs = workbook([["ID", "Name", "Value"], ["1", "one", "a"], ["2", "two", "b"]])
        parsed = self.merge(base, base, theirs)
        self.assertEqual(parsed["sheets"]["Items"]["headers"], ["ID", "Name", "Value"])
        self.assertEqual(parsed["sheets"]["Items"]["rows"][1]["cells"]["B"], "one")
        for row in ET.parse(self.path).getroot().iter(xp.ROW_TAG):
            self.assertEqual([int(c.get(xp.SS_INDEX)) for c in row.findall(xp.CELL_TAG)], [1, 2, 3])

    def test_remote_sheet_addition_and_deletion_are_rejected(self):
        base = xp.parse_string(workbook(ROWS))
        other = xp.parse_string(workbook(ROWS, "Other"))
        added = {"sheets": dict(base["sheets"], **other["sheets"])}
        for theirs in (added, {"sheets": {}}):
            with self.subTest(theirs=list(theirs["sheets"])):
                with self.assertRaises(xm.UnsupportedSheetChange):
                    xm.three_way_diff(base, base, theirs)

    def test_local_sheet_deletion_is_preserved_if_remote_unchanged(self):
        base = workbook(ROWS)
        mine = workbook(ROWS, "Local")
        # Local addition and deletion require no structural mutation of MINE.
        parsed = self.merge(base, mine, base)
        self.assertEqual(list(parsed["sheets"]), ["Local"])

    def test_local_sheet_delete_remote_modify_is_rejected(self):
        base = xp.parse_string(workbook(ROWS))
        theirs = xp.parse_string(workbook(ROWS).replace(">old<", ">new<"))
        with self.assertRaises(xm.UnsupportedSheetChange):
            xm.three_way_diff(base, {"sheets": {}}, theirs)

    def test_legacy_added_sheet_plan_cannot_overwrite_file(self):
        original = workbook(ROWS).encode()
        self.path.write_bytes(original)
        with self.assertRaises(xm.UnsupportedSheetChange):
            xm.write_merged_xml(str(self.path), {"sheets": {"Other": {"sheet_status": "added_theirs"}}}, str(self.path))
        self.assertEqual(self.path.read_bytes(), original)

    def test_invalid_custom_value_does_not_replace_working_file(self):
        base = workbook(ROWS)
        mine = base.replace(">old<", ">mine<")
        theirs = base.replace(">old<", ">theirs<")
        with self.assertRaises(ET.ParseError):
            self.merge(base, mine, theirs, [{"sheet": "Items", "row_key": "1", "col": "B",
                                            "choice": "custom", "value": "bad\x01value"}])
        self.assertEqual(self.path.read_text(encoding="utf-8"), mine)

    def test_removed_column_is_still_visible_in_diff(self):
        old = xp.parse_string(workbook([["ID", "Name", "Value"], ["1", "one", "a"], ["2", "two", "b"]]))
        new = xp.parse_string(workbook([["ID", None, "Value"], ["1", None, "a"], ["2", None, "b"]]))
        changes = xd.diff_workbooks(old, new)["sheets"]["Items"]["modified_cells"]
        self.assertEqual(len([c for c in changes if c["col"] == "B"]), 3)

    def test_sheet_guard_returns_400_without_writing_or_resolving(self):
        original = workbook(ROWS).encode()
        self.path.write_bytes(original)
        sources = {"base": original, "mine": original,
                   "theirs": workbook(ROWS, "Other").encode(), "template_path": str(self.path)}
        with patch.object(server, "_get_work_dir", return_value=self.tmp.name), \
             patch.object(server, "_get_header_row", return_value=1), \
             patch.object(server, "_resolve_merge_sources", return_value=sources), \
             patch.object(server.svn_helper, "is_available", return_value=True), \
             patch.object(server.svn_helper, "_run") as run:
            client = server.app.test_client()
            for route in ("preview", "apply"):
                response = client.post("/api/merge/" + route, json={"file": "items.xml", "mark_resolved": True})
                self.assertEqual(response.status_code, 400, response.get_json())
                self.assertEqual(response.get_json()["error_code"], "unsupported_sheet_change")
                self.assertEqual(response.get_json()["sheets"], ["Items", "Other"])
                self.assertIn("original file has not been changed", response.get_json()["error"])
            run.assert_not_called()
        self.assertEqual(self.path.read_bytes(), original)


if __name__ == "__main__":
    unittest.main(verbosity=2)

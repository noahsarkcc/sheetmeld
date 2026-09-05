"""Real SVN regressions; uses disposable file:// repositories, never a server.

Requires svn and svnadmin on PATH. Run: python tests/test_svn_update.py
"""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import server
import svn_helper as svn
import xml_parser

XML = '''<?xml version="1.0"?>
<Workbook xmlns="urn:schemas-microsoft-com:office:spreadsheet" xmlns:ss="urn:schemas-microsoft-com:office:spreadsheet">
<Worksheet ss:Name="Items"><Table>
<Row><Cell><Data ss:Type="String">ID</Data></Cell><Cell><Data ss:Type="String">Value</Data></Cell></Row>
<Row><Cell><Data ss:Type="String">1</Data></Cell><Cell><Data ss:Type="String">old</Data></Cell></Row>
<Row><Cell><Data ss:Type="String">2</Data></Cell><Cell><Data ss:Type="String">middle</Data></Cell></Row>
<Row><Cell><Data ss:Type="String">3</Data></Cell><Cell><Data ss:Type="String">middle</Data></Cell></Row>
<Row><Cell><Data ss:Type="String">4</Data></Cell><Cell><Data ss:Type="String">stable</Data></Cell></Row>
</Table></Worksheet></Workbook>
'''


@unittest.skipUnless(shutil.which("svn") and shutil.which("svnadmin"), "svn and svnadmin required")
class SmartUpdateIntegration(unittest.TestCase):
    def command(self, *args):
        proc = subprocess.run(args, capture_output=True, timeout=30, **svn._hidden_kwargs())
        self.assertEqual(proc.returncode, 0, (args, proc.stdout, proc.stderr))
        return svn._decode_output(proc.stdout).strip()

    def write(self, path, content):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content.encode("utf-8"))

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="sheetmeld-svn-test-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.remote = self.root / "remote"
        self.local = self.root / "local"
        repo = self.root / "repo"
        self.command("svnadmin", "create", str(repo))
        self.url = repo.as_uri()
        self.command("svn", "checkout", self.url, str(self.remote))
        for name in ("items.xml", "safe.xml", "nested/skip.xml", "nested/other.xml"):
            self.write(self.remote / name, XML)
        self.commit()
        self.command("svn", "checkout", self.url, str(self.local))

    def commit(self):
        self.command("svn", "add", "--force", str(self.remote))
        self.command("svn", "commit", str(self.remote), "-m", "regression fixture")

    def revision(self, path):
        return int(self.command("svn", "info", "--show-item", "revision", str(path)))

    def assert_clean_xml(self, path, expected):
        content = path.read_bytes()
        self.assertFalse(server._has_conflict_markers(content))
        parsed = xml_parser.parse_file(str(path))
        self.assertEqual(parsed["sheets"]["Items"]["rows"][1]["cells"]["B"], expected)
        self.assertEqual(svn.get_conflicted_files(str(path), strict=True), [])

    def test_semantic_api_then_update_preserves_selected_value(self):
        self.write(self.local / "items.xml", XML.replace(">old<", ">mine<"))
        self.write(self.remote / "items.xml", XML.replace(">old<", ">theirs<"))
        self.commit()
        with patch.object(server, "_get_work_dir", return_value=str(self.local)), \
             patch.object(server, "_get_header_row", return_value=1):
            client = server.app.test_client()
            preview = client.post("/api/merge/preview", json={"file": "items.xml"})
            self.assertEqual(preview.status_code, 200, preview.get_json())
            plan = preview.get_json()
            applied = client.post("/api/merge/apply", json={
                "file": "items.xml", "theirs_rev": plan["theirs_revision"],
                "merge_signature": plan["merge_signature"], "mark_resolved": True,
                "resolutions": [{"sheet": "Items", "row_key": "1", "col": "B", "choice": "mine"}],
            })
            self.assertEqual(applied.status_code, 200, applied.get_json())
            expected = (self.local / "items.xml").read_bytes()
            result = svn.smart_update(str(self.local), [], [], [],
                                      [{"file": "items.xml", "theirs_revision": plan["theirs_revision"]}])
        self.assertEqual(result["errors"], [])
        self.assertEqual(result["semantic"], ["items.xml"])
        self.assertEqual((self.local / "items.xml").read_bytes(), expected)
        self.assert_clean_xml(self.local / "items.xml", "mine")
        self.assertEqual(self.revision(self.local / "items.xml"), 2)

    def test_semantic_revision_stays_pinned_when_head_advances(self):
        self.write(self.remote / "items.xml", XML.replace(">old<", ">r2<"))
        self.commit()
        self.write(self.remote / "items.xml", XML.replace(">old<", ">r3<"))
        self.write(self.remote / "safe.xml", XML.replace(">old<", ">latest<"))
        self.commit()
        self.write(self.local / "items.xml", XML.replace(">old<", ">chosen<"))
        result = svn.smart_update(str(self.local), [], [], [], [{"file": "items.xml", "theirs_revision": 2}])
        self.assertEqual(result["errors"], [])
        self.assert_clean_xml(self.local / "items.xml", "chosen")
        self.assertEqual(self.revision(self.local / "items.xml"), 2)
        self.assertEqual(self.revision(self.local / "safe.xml"), 3)
        self.assertIn(b">latest<", (self.local / "safe.xml").read_bytes())

    def test_explicit_choices_work_even_without_text_conflict(self):
        copies = {}
        for policy in ("mine", "theirs", "skip"):
            wc = self.root / policy
            self.command("svn", "checkout", self.url, str(wc))
            self.write(wc / "items.xml", XML.replace(">old<", ">mine<"))
            copies[policy] = wc
        theirs = XML.replace(">stable<", ">remote<")
        self.write(self.remote / "items.xml", theirs)
        self.commit()
        for policy, wc in copies.items():
            with self.subTest(policy=policy):
                before = (wc / "items.xml").read_bytes()
                opts = {"skip_files": [], "theirs_files": [], "mine_files": []}
                opts[policy + "_files"] = ["items.xml"]
                result = svn.smart_update(str(wc), **opts)
                self.assertEqual(result["errors"], [])
                expected = theirs.encode() if policy == "theirs" else before
                self.assertEqual((wc / "items.xml").read_bytes(), expected)
                self.assertEqual(self.revision(wc / "items.xml"), 1 if policy == "skip" else 2)
                self.assertEqual(svn.get_conflicted_files(str(wc), strict=True), [])
                if policy == "skip":
                    with patch.object(server, "_get_work_dir", return_value=str(wc)):
                        client = server.app.test_client()
                        status = client.get("/api/svn/remote-revision").get_json()
                        self.assertTrue(status["has_update"], status)
                        self.assertEqual(status["local_revision"], 1)
                        check = client.post("/api/svn/update", json={"check_only": True}).get_json()
                        self.assertEqual(check["conflicts"], ["items.xml"])

    def test_nested_skip_survives_remote_delete_while_siblings_update(self):
        self.write(self.local / "nested/skip.xml", XML.replace(">old<", ">mine<"))
        before = (self.local / "nested/skip.xml").read_bytes()
        before_stat = (self.local / "nested/skip.xml").stat().st_mtime_ns
        self.command("svn", "delete", str(self.remote / "nested/skip.xml"))
        self.command("svn", "delete", str(self.remote / "safe.xml"))
        self.write(self.remote / "nested/other.xml", XML.replace(">old<", ">new<"))
        self.write(self.remote / "new folder/新表.xml", XML)
        self.commit()
        result = svn.smart_update(str(self.local), ["nested/skip.xml"], [], [])
        self.assertEqual(result["errors"], [])
        self.assertEqual((self.local / "nested/skip.xml").read_bytes(), before)
        self.assertEqual((self.local / "nested/skip.xml").stat().st_mtime_ns, before_stat)
        self.assertEqual(self.revision(self.local / "nested/skip.xml"), 1)
        self.assertFalse((self.local / "safe.xml").exists())
        self.assertTrue((self.local / "new folder/新表.xml").exists())
        self.assert_clean_xml(self.local / "nested/other.xml", "new")

    def test_existing_text_conflict_mine_uses_sidecar(self):
        mine = XML.replace(">old<", ">mine<")
        self.write(self.local / "items.xml", mine)
        self.write(self.remote / "items.xml", XML.replace(">old<", ">theirs<"))
        self.commit()
        self.command("svn", "update", "--accept", "postpone", str(self.local))
        self.assertTrue(svn.get_conflict_info(str(self.local / "items.xml")))
        result = svn.smart_update(str(self.local), [], [], ["items.xml"])
        self.assertEqual(result["errors"], [])
        self.assertEqual((self.local / "items.xml").read_bytes(), mine.encode())
        self.assert_clean_xml(self.local / "items.xml", "mine")

    def test_failed_update_restores_file_and_retains_recovery_copy(self):
        self.write(self.remote / "items.xml", XML.replace(">old<", ">theirs<"))
        self.commit()
        target = self.local / "items.xml"
        original = XML.replace(">old<", ">chosen<").encode()
        target.write_bytes(original)
        calls = []
        real_run = svn._run

        def fail_update(*args, **kwargs):
            calls.append(args)
            if args[0] == "update":
                target.write_bytes(b"<<<<<<< partially updated")
                return 1, "", "simulated update failure"
            return real_run(*args, **kwargs)

        with patch.object(svn, "_run", side_effect=fail_update):
            result = svn.smart_update(str(self.local), [], [], [], [{"file": "items.xml", "theirs_revision": 2}])
        self.assertIn("simulated update failure", result["errors"][0])
        self.assertEqual(result["semantic"], [])
        self.assertEqual(target.read_bytes(), original)
        self.assertEqual(len([c for c in calls if c[0] == "update"]), 1)
        backup = Path(result["backups"][0])
        self.assertEqual(backup.read_bytes(), original)
        backup.unlink()

    def test_deleted_parent_of_skipped_file_is_not_removed(self):
        before = (self.local / "nested/skip.xml").read_bytes()
        self.command("svn", "delete", str(self.remote / "nested"))
        self.commit()
        result = svn.smart_update(str(self.local), ["nested/skip.xml"], [], [])
        self.assertTrue(result["errors"])
        self.assertEqual((self.local / "nested/skip.xml").read_bytes(), before)
        self.assertEqual(self.revision(self.local / "nested/skip.xml"), 1)

    def test_paths_and_missing_semantic_revisions_are_rejected_before_mutation(self):
        with patch.object(svn, "_run") as run:
            for opts in ((["../outside.xml"], [], [], []),
                         ([], [], [], ["items.xml"]),
                         (["items.xml"], ["items.xml"], [], [])):
                self.assertTrue(svn.smart_update(str(self.local), *opts)["errors"])
            run.assert_not_called()

    def test_status_errors_do_not_report_success(self):
        with patch.object(svn, "_run", return_value=(1, "", "locked")):
            with self.assertRaisesRegex(RuntimeError, "locked"):
                svn.get_conflicted_files(str(self.local), strict=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)

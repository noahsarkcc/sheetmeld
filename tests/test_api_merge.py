"""
/api/merge/* 端到端测试（Flask test client + mock SVN）
=====================================================

不需要真实 SVN 仓库，通过 unittest.mock 替换 svn_helper 的网络/磁盘调用。

直接运行：python tests/test_api_merge.py
"""
import os
import sys
import json
import shutil
import tempfile
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import server
import svn_helper
import xml_parser


DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
BASE_PATH = os.path.join(DATA_DIR, "base.xml")
MINE_PATH = os.path.join(DATA_DIR, "mine.xml")
THEIRS_PATH = os.path.join(DATA_DIR, "theirs.xml")


RESET = "\033[0m"; RED = "\033[91m"; GREEN = "\033[92m"; CYAN = "\033[96m"

_passed = 0
_failed = 0
_failures = []


def t(name):
    def deco(fn):
        def wrapper(*a, **kw):
            global _passed, _failed
            try:
                fn(*a, **kw)
                _passed += 1
                print(f"  {GREEN}PASS{RESET} {name}")
            except AssertionError as e:
                _failed += 1
                _failures.append((name, str(e)))
                print(f"  {RED}FAIL{RESET} {name}")
                print(f"       {e}")
            except Exception as e:
                _failed += 1
                _failures.append((name, f"EXCEPTION: {type(e).__name__}: {e}"))
                print(f"  {RED}ERROR{RESET} {name}")
                print(f"       {type(e).__name__}: {e}")
        return wrapper
    return deco


def section(title):
    print(f"\n{CYAN}── {title} ──{RESET}")


def with_workspace(fn):
    """装饰器：每个测试创建临时工作区 + 复制 mine.xml，并 mock SVN。"""
    def wrapper():
        workdir = tempfile.mkdtemp(prefix="xmldev_test_")
        try:
            fpath = os.path.join(workdir, "items.xml")
            shutil.copy(MINE_PATH, fpath)
            with open(BASE_PATH, "r", encoding="utf-8") as f:
                base_text = f.read()
            with open(THEIRS_PATH, "r", encoding="utf-8") as f:
                theirs_text = f.read()

            client = server.app.test_client()
            with patch.object(server, "_get_work_dir", return_value=workdir), \
                 patch.object(svn_helper, "is_available", return_value=True), \
                 patch.object(svn_helper, "get_base_content_raw",
                              return_value=base_text.encode("utf-8")), \
                 patch.object(svn_helper, "get_svn_info",
                              return_value={"url": "https://svn.example/items"}), \
                 patch.object(svn_helper, "get_remote_head_revision", return_value=42), \
                 patch.object(svn_helper, "get_file_at_revision_raw",
                              return_value=theirs_text.encode("utf-8")):
                fn(client, workdir, fpath)
        finally:
            shutil.rmtree(workdir, ignore_errors=True)
    return wrapper


# ── 1. /api/merge/preview ────────────────────────────────


@t("preview：返回结构包含 sheets / summary / labels")
@with_workspace
def test_preview_structure(client, workdir, fpath):
    r = client.post("/api/merge/preview", json={"file": "items.xml"})
    assert r.status_code == 200, f"status={r.status_code} body={r.get_data(as_text=True)}"
    data = r.get_json()
    assert "sheets" in data
    assert "summary" in data
    assert data["theirs_label"] == "r42"
    assert data["theirs_revision"] == 42
    assert data["base_label"] == "SVN BASE"
    assert "Items" in data["sheets"]


@t("preview：summary 反映出冲突数量")
@with_workspace
def test_preview_summary(client, workdir, fpath):
    r = client.post("/api/merge/preview", json={"file": "items.xml"})
    s = r.get_json()["summary"]
    assert s["row_conflicts"] == 3, f"实际 {s['row_conflicts']}（期望 3）"
    assert s["cell_conflicts"] == 1, f"实际 {s['cell_conflicts']}（期望 1）"
    assert s["conflicts"] == 4


@t("preview：拒绝 .xlsx 文件，返回 400")
@with_workspace
def test_preview_rejects_xlsx(client, workdir, fpath):
    r = client.post("/api/merge/preview", json={"file": "items.xlsx"})
    assert r.status_code == 400
    assert "xml" in r.get_json()["error"].lower() or "XML" in r.get_json()["error"]


@t("preview：缺失 file 参数 → 400")
@with_workspace
def test_preview_missing_file(client, workdir, fpath):
    r = client.post("/api/merge/preview", json={})
    assert r.status_code == 400


@t("preview：file 不存在 → 404")
@with_workspace
def test_preview_missing_actual_file(client, workdir, fpath):
    r = client.post("/api/merge/preview", json={"file": "nonexistent.xml"})
    assert r.status_code == 404


# ── 2. /api/merge/apply ──────────────────────────────────


@t("apply：未决议时返回 400 + unresolved 列表")
@with_workspace
def test_apply_unresolved(client, workdir, fpath):
    r = client.post("/api/merge/apply", json={
        "file": "items.xml",
        "resolutions": [],
    })
    assert r.status_code == 400
    body = r.get_json()
    assert "unresolved" in body
    assert len(body["unresolved"]) == 4


@t("apply：全部决议后写回成功，文件内容符合预期")
@with_workspace
def test_apply_full(client, workdir, fpath):
    resolutions = [
        {"sheet": "Items", "row_key": "1006", "col": "B", "choice": "theirs"},
        {"sheet": "Items", "row_key": "1010", "choice": "accept_theirs"},
        {"sheet": "Items", "row_key": "1011", "choice": "accept_theirs_delete"},
        {"sheet": "Items", "row_key": "2004", "choice": "merge"},
        {"sheet": "Items", "row_key": "2004", "col": "B", "choice": "custom", "value": "调和版本"},
        {"sheet": "Items", "row_key": "2004", "col": "D", "choice": "mine"},
    ]
    r = client.post("/api/merge/apply", json={
        "file": "items.xml",
        "resolutions": resolutions,
    })
    assert r.status_code == 200, f"body={r.get_data(as_text=True)}"
    body = r.get_json()
    assert body["ok"] is True
    assert body["applied"] == 6
    assert body["svn_resolved"] is False  # 默认不标记

    parsed = xml_parser.parse_file(fpath)
    rows = parsed["sheets"]["Items"]["rows"]
    by_id = {r["cells"]["A"]: r["cells"] for r in rows[1:] if r["cells"].get("A")}

    # 单元格冲突 1006.B 用了 theirs
    assert by_id["1006"]["B"] == "远程改后"
    # 自动决议项也都生效
    assert by_id["1003"]["C"] == "33"
    assert by_id["1005"]["B"] == "本地改名称"
    assert by_id["1005"]["C"] == "55"
    # 1010 行被恢复
    assert "1010" in by_id and by_id["1010"]["D"] == "远程修改"
    # 1011 被删除
    assert "1011" not in by_id
    # 2004 走 merge：B=custom(调和版本)，D=mine(本地说)
    assert by_id["2004"]["B"] == "调和版本"
    assert by_id["2004"]["D"] == "本地说"


@t("apply：total_changes 包含自动合并，不仅是用户决议数")
@with_workspace
def test_apply_total_changes_includes_auto_merge(client, workdir, fpath):
    """用户只手动决议冲突单元，自动合并的行（theirs 单方新增/修改）也要被计入 total_changes。

    base/mine/theirs 测试数据里 1003.C 是 THEIRS 单方修改、1005 行 THEIRS 单方修改、
    1009 行 THEIRS 单方新增、2004 行 BOTH-DIFF（用户必须决议）等等。
    """
    resolutions = [
        {"sheet": "Items", "row_key": "1006", "col": "B", "choice": "theirs"},
        {"sheet": "Items", "row_key": "1010", "choice": "accept_theirs"},
        {"sheet": "Items", "row_key": "1011", "choice": "accept_theirs_delete"},
        {"sheet": "Items", "row_key": "2004", "choice": "accept_theirs"},
    ]
    r = client.post("/api/merge/apply", json={
        "file": "items.xml",
        "resolutions": resolutions,
    })
    assert r.status_code == 200, f"body={r.get_data(as_text=True)}"
    body = r.get_json()
    # 用户传了 4 条决议
    assert body["applied"] == 4
    # 自动合并的行（1003 单方修改、1005 单方修改、1009 单方新增 等）也要计入；
    # 所以 total_changes 必须 > applied
    assert body["total_changes"] > body["applied"], \
        f"total_changes={body['total_changes']} 应大于 applied={body['applied']}"


@t("apply：用户可 override auto_mine cell 到 theirs 值")
@with_workspace
def test_apply_auto_mine_cell_overridden(client, workdir, fpath):
    """1005 行的 B 列「名称」：BASE='双方改不同列' == THEIRS='双方改不同列' != MINE='本地改名称'。
    这是 auto_mine（远端没改、本地改了）。默认决议是 mine='本地改名称'。
    现在前端允许用户主动 override：传 {col:'B', choice:'theirs'} 想改回远端原值。
    验证 apply 后 1005.B 写入的是 '双方改不同列' 而不是 '本地改名称'。
    """
    resolutions = [
        # 必填的"真正冲突"决议（沿用其他用例的最小集）
        {"sheet": "Items", "row_key": "1006", "col": "B", "choice": "mine"},
        {"sheet": "Items", "row_key": "1010", "choice": "accept_theirs"},
        {"sheet": "Items", "row_key": "1011", "choice": "accept_theirs_delete"},
        {"sheet": "Items", "row_key": "2004", "choice": "accept_theirs"},
        # 关键：override auto_mine cell
        {"sheet": "Items", "row_key": "1005", "col": "B", "choice": "theirs"},
    ]
    r = client.post("/api/merge/apply", json={
        "file": "items.xml",
        "resolutions": resolutions,
    })
    assert r.status_code == 200, f"body={r.get_data(as_text=True)}"
    assert r.get_json()["ok"] is True

    parsed = xml_parser.parse_file(fpath)
    rows = parsed["sheets"]["Items"]["rows"]
    by_id = {r["cells"]["A"]: r["cells"] for r in rows[1:] if r["cells"].get("A")}
    # 关键断言：1005.B 是 theirs 的值（远端原值），不是 mine 的默认 auto 值
    assert by_id["1005"]["B"] == "双方改不同列", \
        f"override auto_mine 失败：1005.B={by_id['1005']['B']!r}，期望 '双方改不同列'"


@t("apply：mark_resolved=true 时尝试 svn resolve（mocked）")
@with_workspace
def test_apply_mark_resolved(client, workdir, fpath):
    resolutions = [
        {"sheet": "Items", "row_key": "1006", "col": "B", "choice": "mine"},
        {"sheet": "Items", "row_key": "1010", "choice": "keep_mine_delete"},
        {"sheet": "Items", "row_key": "1011", "choice": "keep_mine"},
        {"sheet": "Items", "row_key": "2004", "choice": "keep_mine"},
    ]
    with patch.object(svn_helper, "_run", return_value=(0, "Resolved", "")):
        r = client.post("/api/merge/apply", json={
            "file": "items.xml",
            "resolutions": resolutions,
            "mark_resolved": True,
        })
    assert r.status_code == 200
    assert r.get_json()["svn_resolved"] is True


@t("apply：拒绝 .xlsx 文件")
@with_workspace
def test_apply_rejects_xlsx(client, workdir, fpath):
    r = client.post("/api/merge/apply", json={
        "file": "items.xlsx",
        "resolutions": [],
    })
    assert r.status_code == 400


@t("apply：冲突状态下用 .mine 旁路文件作模板，污染的工作副本仍能写回")
def test_apply_in_svn_conflict_uses_mine_template():
    """模拟 svn update 后产生 text conflict 的真实场景：

    工作副本里的 items.xml 已经被 SVN 写入 <<<<<<< 标记不是合法 XML，
    旁边有 items.xml.mine（合法本地版）/ items.xml.r1（BASE）/ items.xml.r2（THEIRS）。
    /api/merge/apply 必须用 .mine 当模板而不是被污染的工作副本，
    否则 ET.parse 会直接挂掉。
    """
    workdir = tempfile.mkdtemp(prefix="xmldev_test_")
    try:
        # 工作副本：完全是非法 XML（典型的冲突标记内容）
        fpath = os.path.join(workdir, "items.xml")
        with open(fpath, "w", encoding="utf-8") as f:
            f.write("<<<<<<< .mine\n??? not well-formed ???\n=======\nstill broken\n>>>>>>> .r42\n")

        # 旁路文件：复用现成测试数据
        mine_sidecar = os.path.join(workdir, "items.xml.mine")
        base_sidecar = os.path.join(workdir, "items.xml.r1")
        theirs_sidecar = os.path.join(workdir, "items.xml.r2")
        shutil.copy(MINE_PATH, mine_sidecar)
        shutil.copy(BASE_PATH, base_sidecar)
        shutil.copy(THEIRS_PATH, theirs_sidecar)

        client = server.app.test_client()
        conflict_info = {
            "is_text_conflict": True,
            "base_file": base_sidecar,
            "mine_file": mine_sidecar,
            "theirs_file": theirs_sidecar,
            "base_rev": 1,
            "theirs_rev": 2,
        }
        resolutions = [
            {"sheet": "Items", "row_key": "1006", "col": "B", "choice": "theirs"},
            {"sheet": "Items", "row_key": "1010", "choice": "accept_theirs"},
            {"sheet": "Items", "row_key": "1011", "choice": "accept_theirs_delete"},
            {"sheet": "Items", "row_key": "2004", "choice": "accept_theirs"},
        ]
        with patch.object(server, "_get_work_dir", return_value=workdir), \
             patch.object(svn_helper, "is_available", return_value=True), \
             patch.object(svn_helper, "get_conflict_info",
                          return_value=conflict_info), \
             patch.object(svn_helper, "_run", return_value=(0, "Resolved", "")):
            r = client.post("/api/merge/apply", json={
                "file": "items.xml",
                "resolutions": resolutions,
            })
        assert r.status_code == 200, f"body={r.get_data(as_text=True)}"
        body = r.get_json()
        assert body["ok"] is True
        assert body["from_svn_conflict"] is True
        # 冲突状态下即使前端没传 mark_resolved，也应该自动 svn resolve --accept working
        assert body["svn_resolved"] is True

        # 关键验收：写回工作副本不再是冲突标记，并且能被重新解析为合法 XML
        with open(fpath, "rb") as f:
            written = f.read()
        assert b"<<<<<<<" not in written and b">>>>>>>" not in written, \
            "工作副本仍包含冲突标记，写回失败"
        # 解析回 Items sheet 验证合并结果
        parsed = xml_parser.parse_file(fpath)
        assert "Items" in parsed["sheets"]
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


# ── 3. /api/merge/svn-mark-resolved ──────────────────────


@t("svn-mark-resolved：成功路径")
@with_workspace
def test_svn_mark_resolved_ok(client, workdir, fpath):
    with patch.object(svn_helper, "_run", return_value=(0, "Resolved", "")):
        r = client.post("/api/merge/svn-mark-resolved", json={"file": "items.xml"})
    assert r.status_code == 200
    assert r.get_json()["ok"] is True


@t("svn-mark-resolved：svn resolve 失败 → 500")
@with_workspace
def test_svn_mark_resolved_fail(client, workdir, fpath):
    with patch.object(svn_helper, "_run", return_value=(1, "", "svn resolve failed")):
        r = client.post("/api/merge/svn-mark-resolved", json={"file": "items.xml"})
    assert r.status_code == 500


# ── 4. SVN update 冲突检测 ───────────────────────────────


@t("files：递归列出子目录 .xls 文件")
def test_files_lists_nested_xls():
    workdir = tempfile.mkdtemp(prefix="xmldev_test_")
    try:
        nested_dir = os.path.join(workdir, "configs")
        os.makedirs(nested_dir, exist_ok=True)
        with open(os.path.join(nested_dir, "items.xls"), "wb") as f:
            f.write(b"placeholder")
        client = server.app.test_client()
        with patch.object(server, "_get_work_dir", return_value=workdir):
            r = client.get("/api/files")
        assert r.status_code == 200
        body = r.get_json()
        names = [f["name"] for f in body["files"]]
        assert "configs/items.xls" in names
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


@t("svn/modified：子目录 .xls 使用工作区相对路径作为 name")
def test_svn_modified_nested_xls_name_is_relative():
    workdir = tempfile.mkdtemp(prefix="xmldev_test_")
    try:
        nested = os.path.join(workdir, "configs", "items.xls")
        client = server.app.test_client()
        with patch.object(server, "_get_work_dir", return_value=workdir), \
             patch.object(svn_helper, "is_available", return_value=True), \
             patch.object(svn_helper, "get_modified_files", return_value=[
                 {"path": nested, "status": "modified", "name": "items.xls"},
             ]):
            r = client.get("/api/svn/modified")
        assert r.status_code == 200
        body = r.get_json()
        assert body["files"][0]["name"] == "configs/items.xls"
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


@t("api/parse：路径穿越（.. / 绝对路径）被拒绝 → 400")
def test_parse_path_traversal_rejected():
    workdir = tempfile.mkdtemp(prefix="xmldev_test_")
    try:
        outside_abs = os.path.abspath(os.path.join(workdir, "..", "outside.xml"))
        client = server.app.test_client()
        with patch.object(server, "_get_work_dir", return_value=workdir):
            r1 = client.get("/api/parse?file=../escape.xml")
            r2 = client.get(f"/api/parse?file={outside_abs}")
            r3 = client.post("/api/merge/apply", json={
                "file": "../escape.xml", "resolutions": []})
        assert r1.status_code == 400, f"..穿越未被拒绝: {r1.status_code}"
        assert r2.status_code == 400, f"绝对路径未被拒绝: {r2.status_code}"
        assert r3.status_code in (400, 503), f"merge 穿越未被拒绝: {r3.status_code}"
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


@t("svn/update check_only：子目录文件按工作区相对路径匹配冲突")
def test_svn_update_check_only_nested_conflict():
    workdir = tempfile.mkdtemp(prefix="xmldev_test_")
    try:
        nested = os.path.join(workdir, "configs", "items.xml")
        local_only = os.path.join(workdir, "configs", "local_only.xml")
        client = server.app.test_client()
        with patch.object(server, "_get_work_dir", return_value=workdir), \
             patch.object(svn_helper, "is_available", return_value=True), \
             patch.object(svn_helper, "get_modified_files", return_value=[
                 {"path": nested, "status": "modified", "name": "items.xml"},
                 {"path": local_only, "status": "modified", "name": "local_only.xml"},
             ]), \
             patch.object(svn_helper, "get_remote_changed_files", return_value=[
                 "configs/items.xml",
                 "configs/remote_only.xml",
             ]):
            r = client.post("/api/svn/update", json={"check_only": True})
        assert r.status_code == 200, f"body={r.get_data(as_text=True)}"
        body = r.get_json()
        assert body["conflicts"] == ["configs/items.xml"]
        assert body["safe_updates"] == 1
        assert body["local_only"] == ["configs/local_only.xml"]
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


# ── 5. preview/apply 二次幂等 ────────────────────────────


@t("apply 后再次 preview：summary 显示已无冲突")
@with_workspace
def test_idempotent_after_apply(client, workdir, fpath):
    resolutions = [
        {"sheet": "Items", "row_key": "1006", "col": "B", "choice": "theirs"},
        {"sheet": "Items", "row_key": "1010", "choice": "accept_theirs"},
        {"sheet": "Items", "row_key": "1011", "choice": "accept_theirs_delete"},
        {"sheet": "Items", "row_key": "2004", "choice": "accept_theirs"},
    ]
    r = client.post("/api/merge/apply", json={
        "file": "items.xml",
        "resolutions": resolutions,
    })
    assert r.status_code == 200

    # MINE 文件已被覆盖；用合并后的内容当 MINE，再用同样的 BASE/THEIRS 跑一次 preview
    # 这次本地（MINE）已经 == THEIRS（除被本地保留的部分），冲突应大幅减少
    r2 = client.post("/api/merge/preview", json={"file": "items.xml"})
    assert r2.status_code == 200
    s = r2.get_json()["summary"]
    # 接受了所有远程后，与 BASE 的 diff 是合并后的状态；冲突应为 0
    assert s["conflicts"] == 0, f"二次 preview 仍有冲突: {s}"


# ── 数据损坏防护 ────────────────────────────────────


@t("smart_update: semantic_files 必须先于整目录 update 单独 update --accept working")
def test_smart_update_semantic_files_promoted_first():
    """回归 18:51 那次数据损坏：

    svn_helper.smart_update 必须先对每个 semantic_file 跑
    `resolve --accept working` + `update --accept working <fpath>` 把 BASE 推到
    HEAD，再跑整目录 `update --accept postpone`。如果顺序反了，整目录 update
    会重新触发冲突并把 `<<<<<<<` 标记写进工作副本，后续 resolve --accept
    working 会把损坏内容固化为 SVN 认可的"已解决"状态。
    """
    workdir = tempfile.mkdtemp(prefix="xmldev_test_")
    try:
        calls = []

        def _fake_run(*args, **kwargs):
            # _run 的第一个 positional 是 svn 子命令名
            calls.append(args)
            return (0, "", "")

        with patch.object(svn_helper, "_run", side_effect=_fake_run), \
             patch.object(svn_helper, "get_conflicted_files", return_value=[]):
            svn_helper.smart_update(workdir, [], [], [], [
                {"file": "a.xml", "theirs_revision": 10},
                {"file": "b.xml", "theirs_revision": 11},
            ])

        cmds = [a[0] for a in calls if a]
        # 必须有两个 semantic_files × (resolve, update) = 至少 4 条命令在
        # 整目录 "update --accept postpone <workdir>" 之前
        idx_dir_update = None
        for i, a in enumerate(calls):
            if (len(a) >= 4 and a[0] == "update"
                    and a[1] == "--accept" and a[2] == "postpone"):
                idx_dir_update = i
                break
        assert idx_dir_update is not None, (
            "smart_update never ran the directory-wide `svn update --accept postpone`")

        prefix = calls[:idx_dir_update]
        prefix_cmds = [a[:5] for a in prefix]
        expected = [
            ("resolve", "--accept", "working", os.path.join(workdir, "a.xml")),
            ("update", "-r", "10", "--accept", "working"),
            ("resolve", "--accept", "working", os.path.join(workdir, "b.xml")),
            ("update", "-r", "11", "--accept", "working"),
        ]
        assert prefix_cmds == expected, (
            f"semantic_files must be processed before the directory update, "
            f"got prefix={prefix_cmds!r}")
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


@t("smart_update: semantic_files 缺目标版本或单文件 update 失败时停止整目录 update")
def test_smart_update_semantic_promote_failure_stops_directory_update():
    workdir = tempfile.mkdtemp(prefix="xmldev_test_")
    try:
        calls = []

        result = svn_helper.smart_update(workdir, [], [], [], ["a.xml"])
        assert result["errors"], "missing target revision should be reported"

        def _fake_run(*args, **kwargs):
            calls.append(args)
            if args and args[0] == "update" and args[1] == "-r":
                return (1, "", "locked")
            return (0, "", "")

        with patch.object(svn_helper, "_run", side_effect=_fake_run), \
             patch.object(svn_helper, "get_conflicted_files", return_value=[]):
            result = svn_helper.smart_update(workdir, [], [], [], [
                {"file": "a.xml", "theirs_revision": 10},
            ])

        assert result["errors"] and "locked" in result["errors"][0]
        assert not any(a[0] == "update" and len(a) >= 4 and a[2] == "postpone"
                       for a in calls), "directory update must not run after promote failure"
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


@t("get_conflict_info: 相对 sidecar 路径按冲突文件目录解析")
def test_get_conflict_info_resolves_relative_sidecars():
    workdir = tempfile.mkdtemp(prefix="xmldev_test_")
    try:
        fpath = os.path.join(workdir, "nested", "items.xml")
        os.makedirs(os.path.dirname(fpath), exist_ok=True)
        for suffix in (".r1", ".mine", ".r2"):
            with open(fpath + suffix, "w", encoding="utf-8") as f:
                f.write("<Workbook/>")

        xml = """<?xml version="1.0"?>
<info>
  <entry path="items.xml">
    <conflict type="text">
      <prev-base-file>items.xml.r1</prev-base-file>
      <prev-wc-file>items.xml.mine</prev-wc-file>
      <cur-base-file>items.xml.r2</cur-base-file>
      <version side="source-left" revision="1" />
      <version side="source-right" revision="2" />
    </conflict>
  </entry>
</info>"""
        with patch.object(svn_helper, "_run", return_value=(0, xml, "")):
            info = svn_helper.get_conflict_info(fpath)

        assert info is not None
        assert info["base_file"] == fpath + ".r1"
        assert info["mine_file"] == fpath + ".mine"
        assert info["theirs_file"] == fpath + ".r2"
        assert info["theirs_rev"] == 2
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


@t("apply: 工作副本含 SVN 冲突标记时报清晰错误而不是 ET ParseError")
def test_apply_rejects_poisoned_working_copy():
    """回归 18:51:14 那次崩溃：

    一次失败的语义合并 + svn update 把 `<<<<<<<` / `>>>>>>>` 标记冻进了
    工作副本，但 SVN 自己已经把文件标记为 resolved（旁路 .mine 文件被清理）。
    再次调用 /api/merge/apply 时 _resolve_merge_sources 走 fallback 路径
    读工作副本，ET 解析直接挂在 "not well-formed line 3 column 1"。

    应当在 ValueError 阶段给出可读消息（含"冲突标记"关键词）。
    """
    workdir = tempfile.mkdtemp(prefix="xmldev_test_")
    try:
        fpath = os.path.join(workdir, "items.xml")
        with open(fpath, "w", encoding="utf-8") as f:
            f.write("<?xml version=\"1.0\"?>\n"
                    "<Workbook>\n"
                    "<<<<<<< .mine\n"
                    "  <Row><Cell>A</Cell></Row>\n"
                    "=======\n"
                    "  <Row><Cell>B</Cell></Row>\n"
                    ">>>>>>> .r42\n"
                    "</Workbook>\n")

        client = server.app.test_client()
        with patch.object(server, "_get_work_dir", return_value=workdir), \
             patch.object(svn_helper, "is_available", return_value=True), \
             patch.object(svn_helper, "get_conflict_info", return_value=None), \
             patch.object(svn_helper, "get_base_content_raw",
                          return_value=b"<?xml version=\"1.0\"?><Workbook/>"), \
             patch.object(svn_helper, "get_svn_info",
                          return_value={"url": "file:///x", "revision": "1"}), \
             patch.object(svn_helper, "get_remote_head_revision", return_value=2), \
             patch.object(svn_helper, "get_file_at_revision_raw",
                          return_value=b"<?xml version=\"1.0\"?><Workbook/>"):
            r = client.post("/api/merge/apply", json={
                "file": "items.xml",
                "resolutions": [],
            })

        assert r.status_code == 500, f"unexpected status {r.status_code}"
        body = r.get_json()
        err = (body or {}).get("error", "")
        assert "\u51b2\u7a81\u6807\u8bb0" in err, (
            f"error message should mention conflict markers, got: {err!r}")
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


# ── 7. SVN 状态漂移防御（preview/apply signature 校验） ────


def _conflict_workdir():
    """Build a working copy that is in SVN text-conflict state, complete with
    poisoned working copy + three sidecar files. Returns (workdir, fpath,
    conflict_info)."""
    workdir = tempfile.mkdtemp(prefix="xmldev_test_")
    fpath = os.path.join(workdir, "items.xml")
    with open(fpath, "w", encoding="utf-8") as f:
        f.write("<<<<<<< .mine\nstill broken\n=======\nstill broken\n>>>>>>> .r2\n")
    mine_sidecar = os.path.join(workdir, "items.xml.mine")
    base_sidecar = os.path.join(workdir, "items.xml.r1")
    theirs_sidecar = os.path.join(workdir, "items.xml.r2")
    shutil.copy(MINE_PATH, mine_sidecar)
    shutil.copy(BASE_PATH, base_sidecar)
    shutil.copy(THEIRS_PATH, theirs_sidecar)
    conflict_info = {
        "is_text_conflict": True,
        "base_file": base_sidecar,
        "mine_file": mine_sidecar,
        "theirs_file": theirs_sidecar,
        "base_rev": 1,
        "theirs_rev": 2,
    }
    return workdir, fpath, conflict_info


@t("apply: preview 冲突态 -> apply 已被外部 svn resolve（漂移）-> 409 stale")
def test_apply_stale_signature_conflict_to_resolved():
    """Drift-1: preview saw SVN text conflict; between preview and apply, the
    user ran ``svn resolve`` from the command line so the conflict vanished
    (and the .mine sidecar was deleted by SVN). The fingerprint flips
    is_conflict 1 -> 0 and apply must refuse with 409."""
    workdir, fpath, conflict_info = _conflict_workdir()
    try:
        client = server.app.test_client()
        base_bytes = open(BASE_PATH, "rb").read()
        theirs_bytes = open(THEIRS_PATH, "rb").read()
        # First call (preview) returns conflict_info; second (apply) None.
        calls = {"n": 0}
        def conflict_side_effect(_path):
            calls["n"] += 1
            return conflict_info if calls["n"] == 1 else None

        with patch.object(server, "_get_work_dir", return_value=workdir), \
             patch.object(svn_helper, "is_available", return_value=True), \
             patch.object(svn_helper, "get_conflict_info",
                          side_effect=conflict_side_effect), \
             patch.object(svn_helper, "get_base_content_raw",
                          return_value=base_bytes), \
             patch.object(svn_helper, "get_svn_info",
                          return_value={"url": "file:///x", "revision": "1"}), \
             patch.object(svn_helper, "get_remote_head_revision", return_value=42), \
             patch.object(svn_helper, "get_file_at_revision_raw",
                          return_value=theirs_bytes):
            r1 = client.post("/api/merge/preview", json={"file": "items.xml"})
            assert r1.status_code == 200, r1.get_data(as_text=True)
            sig = r1.get_json().get("merge_signature")
            assert sig and sig.startswith("1:"), f"unexpected preview signature {sig!r}"

            # Simulate external `svn resolve --accept theirs-full`: the
            # working copy becomes a clean XML (theirs content).
            shutil.copy(THEIRS_PATH, fpath)

            r2 = client.post("/api/merge/apply", json={
                "file": "items.xml",
                "resolutions": [],
                "merge_signature": sig,
            })
            assert r2.status_code == 409, (
                f"expected 409, got {r2.status_code}: {r2.get_data(as_text=True)}")
            body = r2.get_json()
            assert body.get("stale") is True
            assert "\u5916\u90e8" in body.get("error", ""), \
                f"error should mention 外部, got: {body.get('error')!r}"
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


@t("apply: preview 非冲突 -> apply 被外部 svn update 引入冲突 -> 409 stale")
def test_apply_stale_signature_resolved_to_conflict():
    """Drift-2: preview saw a clean working copy (no SVN conflict); between
    preview and apply, the user ran ``svn update`` from the command line
    which introduced a text conflict. The fingerprint flips is_conflict
    0 -> 1 and apply must refuse with 409."""
    workdir = tempfile.mkdtemp(prefix="xmldev_test_")
    try:
        fpath = os.path.join(workdir, "items.xml")
        shutil.copy(MINE_PATH, fpath)
        base_bytes = open(BASE_PATH, "rb").read()
        theirs_bytes = open(THEIRS_PATH, "rb").read()

        # First call (preview): no conflict; second (apply): conflict appeared.
        mine_sidecar = os.path.join(workdir, "items.xml.mine")
        base_sidecar = os.path.join(workdir, "items.xml.r1")
        theirs_sidecar = os.path.join(workdir, "items.xml.r2")
        shutil.copy(MINE_PATH, mine_sidecar)
        shutil.copy(BASE_PATH, base_sidecar)
        shutil.copy(THEIRS_PATH, theirs_sidecar)
        conflict_info = {
            "is_text_conflict": True,
            "base_file": base_sidecar,
            "mine_file": mine_sidecar,
            "theirs_file": theirs_sidecar,
            "base_rev": 1, "theirs_rev": 2,
        }
        calls = {"n": 0}
        def conflict_side_effect(_path):
            calls["n"] += 1
            return None if calls["n"] == 1 else conflict_info

        client = server.app.test_client()
        with patch.object(server, "_get_work_dir", return_value=workdir), \
             patch.object(svn_helper, "is_available", return_value=True), \
             patch.object(svn_helper, "get_conflict_info",
                          side_effect=conflict_side_effect), \
             patch.object(svn_helper, "get_base_content_raw",
                          return_value=base_bytes), \
             patch.object(svn_helper, "get_svn_info",
                          return_value={"url": "file:///x", "revision": "1"}), \
             patch.object(svn_helper, "get_remote_head_revision", return_value=42), \
             patch.object(svn_helper, "get_file_at_revision_raw",
                          return_value=theirs_bytes):
            r1 = client.post("/api/merge/preview", json={"file": "items.xml"})
            assert r1.status_code == 200, r1.get_data(as_text=True)
            sig = r1.get_json().get("merge_signature")
            assert sig and sig.startswith("0:"), f"unexpected preview signature {sig!r}"

            # External `svn update` poisoned the working copy with conflict markers.
            with open(fpath, "w", encoding="utf-8") as f:
                f.write("<<<<<<< .mine\nbroken\n=======\nbroken\n>>>>>>> .r2\n")

            r2 = client.post("/api/merge/apply", json={
                "file": "items.xml",
                "resolutions": [],
                "merge_signature": sig,
            })
            assert r2.status_code == 409, (
                f"expected 409, got {r2.status_code}: {r2.get_data(as_text=True)}")
            body = r2.get_json()
            assert body.get("stale") is True
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


@t("apply: preview 后工作副本 mtime 被外部编辑变化 -> 409 stale")
def test_apply_stale_signature_mtime_changed():
    """Drift-4: preview ran against working copy at mtime T; user opened the
    file in another editor and saved (or `touch`-ed it via CLI). mtime
    component of the signature differs and apply must refuse with 409."""
    workdir = tempfile.mkdtemp(prefix="xmldev_test_")
    try:
        fpath = os.path.join(workdir, "items.xml")
        shutil.copy(MINE_PATH, fpath)
        base_bytes = open(BASE_PATH, "rb").read()
        theirs_bytes = open(THEIRS_PATH, "rb").read()
        client = server.app.test_client()
        with patch.object(server, "_get_work_dir", return_value=workdir), \
             patch.object(svn_helper, "is_available", return_value=True), \
             patch.object(svn_helper, "get_conflict_info", return_value=None), \
             patch.object(svn_helper, "get_base_content_raw",
                          return_value=base_bytes), \
             patch.object(svn_helper, "get_svn_info",
                          return_value={"url": "file:///x", "revision": "1"}), \
             patch.object(svn_helper, "get_remote_head_revision", return_value=42), \
             patch.object(svn_helper, "get_file_at_revision_raw",
                          return_value=theirs_bytes):
            r1 = client.post("/api/merge/preview", json={"file": "items.xml"})
            assert r1.status_code == 200, r1.get_data(as_text=True)
            sig = r1.get_json().get("merge_signature")
            assert sig, "preview should return a merge_signature"

            # Bump mtime forward by 10s to mimic an external save.
            current_mtime = os.path.getmtime(fpath)
            os.utime(fpath, (current_mtime + 10, current_mtime + 10))

            r2 = client.post("/api/merge/apply", json={
                "file": "items.xml",
                "resolutions": [],
                "merge_signature": sig,
            })
            assert r2.status_code == 409, (
                f"expected 409, got {r2.status_code}: {r2.get_data(as_text=True)}")
            body = r2.get_json()
            assert body.get("stale") is True
            assert body.get("client_signature") != body.get("current_signature")
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


@t("apply: preview .mine 旁路在 -> apply 被外部清掉 -> 500 含 '旁路文件'")
def test_apply_mine_sidecar_vanished():
    """Drift-3: preview saw the conflict and read the .mine sidecar; user ran
    ``svn resolve`` from the command line which made SVN delete the
    sidecars BUT for some reason ``get_conflict_info`` still reports the
    conflict (e.g. stale `.svn/wc.db` cache, race). _read_bytes(mine_file)
    returns None and _resolve_merge_sources should raise the friendly
    'cannot read sidecar file' ValueError, surfaced as 500.

    This is a different failure mode from Drift-1: signature won't catch
    it because get_conflict_info still says conflict, but _read_bytes
    raises first."""
    workdir, fpath, conflict_info = _conflict_workdir()
    try:
        # Delete the .mine sidecar between preview and apply by deleting it
        # *before* calling apply. We do not run preview here because the goal
        # is just to verify the apply-path error path is clean.
        os.remove(conflict_info["mine_file"])

        client = server.app.test_client()
        with patch.object(server, "_get_work_dir", return_value=workdir), \
             patch.object(svn_helper, "is_available", return_value=True), \
             patch.object(svn_helper, "get_conflict_info",
                          return_value=conflict_info):
            r = client.post("/api/merge/apply", json={
                "file": "items.xml",
                "resolutions": [],
            })
        assert r.status_code == 500, (
            f"expected 500, got {r.status_code}: {r.get_data(as_text=True)}")
        body = r.get_json()
        err = (body or {}).get("error", "")
        assert "\u65c1\u8def\u6587\u4ef6" in err, (
            f"error should mention 旁路文件, got: {err!r}")
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


# ── /log smoke test ────────────────────────────────────


@t("/log: 缺失日志文件也返回 200 HTML")
def test_log_view_no_file():
    """The /log route should serve a placeholder HTML page even when the
    log file does not exist yet (e.g. fresh install, console-only run)."""
    client = server.app.test_client()
    with patch("server._log_path", return_value=os.path.join(tempfile.gettempdir(),
                                                              "sheetmeld-no-such-file.log")):
        r = client.get("/log")
    assert r.status_code == 200, f"unexpected status {r.status_code}"
    ctype = r.headers.get("Content-Type", "")
    assert "text/html" in ctype, f"unexpected content-type {ctype!r}"


# ── Main ────────────────────────────────────────────────


def main():
    print(f"{CYAN}/api/merge/* 端到端测试（mocked SVN）{RESET}")
    print(f"  数据目录: {DATA_DIR}")

    section("1. /api/merge/preview")
    test_preview_structure()
    test_preview_summary()
    test_preview_rejects_xlsx()
    test_preview_missing_file()
    test_preview_missing_actual_file()

    section("2. /api/merge/apply")
    test_apply_unresolved()
    test_apply_full()
    test_apply_total_changes_includes_auto_merge()
    test_apply_auto_mine_cell_overridden()
    test_apply_mark_resolved()
    test_apply_rejects_xlsx()
    test_apply_in_svn_conflict_uses_mine_template()

    section("3. /api/merge/svn-mark-resolved")
    test_svn_mark_resolved_ok()
    test_svn_mark_resolved_fail()

    section("4. SVN update 冲突检测")
    test_files_lists_nested_xls()
    test_svn_modified_nested_xls_name_is_relative()
    test_parse_path_traversal_rejected()
    test_svn_update_check_only_nested_conflict()

    section("5. 幂等性")
    test_idempotent_after_apply()

    section("6. 数据损坏防护")
    test_smart_update_semantic_files_promoted_first()
    test_smart_update_semantic_promote_failure_stops_directory_update()
    test_get_conflict_info_resolves_relative_sidecars()
    test_apply_rejects_poisoned_working_copy()

    section("7. SVN 状态漂移防御")
    test_apply_stale_signature_conflict_to_resolved()
    test_apply_stale_signature_resolved_to_conflict()
    test_apply_stale_signature_mtime_changed()
    test_apply_mine_sidecar_vanished()

    section("8. /log 查看器")
    test_log_view_no_file()

    print()
    total = _passed + _failed
    if _failed == 0:
        print(f"{GREEN}== 全部通过：{_passed}/{total} =={RESET}")
        return 0
    print(f"{RED}== 失败 {_failed} / 通过 {_passed} / 共 {total} =={RESET}")
    for name, msg in _failures:
        print(f"  {RED}- {name}{RESET}")
        print(f"      {msg}")
    return 1


if __name__ == "__main__":
    sys.exit(main())

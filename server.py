"""
Flask server for SheetMeld.
Provides REST API for parsing, diffing, and SVN integration.
"""
__version__ = "1.5.2"

import os
import sys
import json
import time
import html
import datetime
import logging
import webbrowser
import threading
from logging.handlers import RotatingFileHandler
from flask import Flask, request, jsonify, send_from_directory

import xml_parser
import xlsx_parser
import xml_differ
import xml_merger
import svn_helper
import updater

try:
    import tray as tray_module
    _HAS_TRAY = tray_module.AVAILABLE
except Exception:
    tray_module = None
    _HAS_TRAY = False


# Module-level logger for business INFO records (workspace switch, merge
# preview/apply, svn update). Wired up by setup_logging() later in the file;
# safe to reference at import time because logging.getLogger is global.
logger = logging.getLogger("sheetmeld")


SUPPORTED_EXTENSIONS = (".xml", ".xlsx", ".xls")


def _is_excel_binary(filename: str) -> bool:
    low = filename.lower()
    return low.endswith(".xlsx") or low.endswith(".xls")


def _parse_file(filepath: str, header_row: int = 1) -> dict:
    """Parse a file, auto-selecting parser by extension."""
    if _is_excel_binary(filepath):
        return xlsx_parser.parse_file(filepath, header_row=header_row)
    return xml_parser.parse_file(filepath, header_row=header_row)


def _parse_content(content, filename: str, header_row: int = 1) -> dict:
    """Parse file content from SVN (bytes preferred; str accepted for XML)."""
    if _is_excel_binary(filename):
        raw = content if isinstance(content, bytes) else content.encode("latin-1")
        return xlsx_parser.parse_bytes(raw, header_row=header_row)
    return xml_parser.parse_string(content, header_row=header_row)


def _get_base_content(filepath: str):
    """Get SVN BASE content as raw bytes.

    XML also goes through the raw path so ElementTree can decode it per its
    own XML declaration (UTF-8, UTF-16, ...) instead of a UTF-8/GBK guess.
    """
    return svn_helper.get_base_content_raw(filepath)


def _get_file_at_revision(filepath: str, revision: int):
    """Get file content at an SVN revision as raw bytes (see _get_base_content)."""
    return svn_helper.get_file_at_revision_raw(filepath, revision)


def _read_bytes(path: str):
    """Read a file as raw bytes; returns None when missing/unreadable."""
    try:
        with open(path, "rb") as f:
            return f.read()
    except OSError:
        return None


# SVN conflict markers as written by `svn update --accept postpone`. Detecting
# them in working-copy bytes lets us surface a clear error message instead of
# the cryptic "not well-formed (invalid token)" from ElementTree.
_CONFLICT_MARKER_PREFIXES = (b"<<<<<<<", b"=======", b">>>>>>>")


def _has_conflict_markers(data: bytes) -> bool:
    """True when *any* line in *data* starts with an SVN conflict marker."""
    if not data:
        return False
    for line in data.splitlines():
        if any(line.startswith(p) for p in _CONFLICT_MARKER_PREFIXES):
            return True
    return False


def _resolve_merge_sources(fpath: str, theirs_rev_hint="HEAD") -> dict:
    """Resolve BASE / MINE / THEIRS contents for three-way merge.

    When the working-copy file is in an SVN text-conflict state, the working
    copy itself has conflict markers (and svn cat -r BASE returns the freshly
    pulled HEAD, not the pre-update ancestor). In that case we read the three
    sidecar files SVN keeps next to the conflicted file:

      <name>.r<oldRev> -- BASE
      <name>.mine      -- MINE
      <name>.r<newRev> -- THEIRS

    Otherwise we fall back to the regular path (svn cat BASE / working copy /
    svn cat HEAD-or-revision). Returns a dict with raw bytes plus labels.
    Raises ValueError when one of the three sources cannot be obtained.
    """
    conflict = svn_helper.get_conflict_info(fpath)
    if conflict:
        base = _read_bytes(conflict["base_file"])
        mine = _read_bytes(conflict["mine_file"])
        theirs = _read_bytes(conflict["theirs_file"])
        if base is None or mine is None or theirs is None:
            raise ValueError("\u65e0\u6cd5\u8bfb\u53d6 SVN \u51b2\u7a81\u7684\u65c1\u8def\u6587\u4ef6")
        # Sidecars should be clean (SVN writes them before injecting markers
        # into the working copy), but verify so an upstream surprise raises
        # the friendly message instead of an ElementTree ParseError.
        if _has_conflict_markers(mine):
            raise ValueError(
                "SVN \u65c1\u8def\u6587\u4ef6 .mine \u542b\u51b2\u7a81\u6807\u8bb0\uff0c"
                "\u8bf7\u5148\u624b\u52a8\u6e05\u7406\u540e\u91cd\u8bd5"
            )
        return {
            "is_conflict": True,
            "base": base,
            "mine": mine,
            "theirs": theirs,
            "base_label": f"r{conflict['base_rev']}" if conflict.get("base_rev") else "BASE",
            "mine_label": "\u5de5\u4f5c\u526f\u672c",
            "theirs_label": f"r{conflict['theirs_rev']}" if conflict.get("theirs_rev") else "HEAD",
            "theirs_revision": conflict.get("theirs_rev"),
            # The working copy itself contains "<<<<<<<" markers and cannot be
            # parsed as XML; use the .mine sidecar (the pre-update local copy)
            # as the template for write_merged_xml. The merge result will
            # still be written back to the working-copy path.
            "template_path": conflict["mine_file"],
        }

    base = _get_base_content(fpath)
    if base is None:
        raise ValueError("\u65e0\u6cd5\u83b7\u53d6 SVN BASE \u7248\u672c")
    mine = _read_bytes(fpath)
    if mine is None:
        raise ValueError("\u65e0\u6cd5\u8bfb\u53d6\u5de5\u4f5c\u526f\u672c")
    # Defensive: a previous botched semantic-merge could have frozen
    # `<<<<<<<` / `=======` / `>>>>>>>` conflict markers into the working
    # copy (see svn_helper.smart_update for the historical scenario). We
    # cannot parse that as XML - raise a clearly-worded error instead of
    # letting ElementTree explode with "not well-formed: line N column 1".
    if _has_conflict_markers(mine):
        raise ValueError(
            "\u5de5\u4f5c\u526f\u672c\u6587\u4ef6\u542b\u6709 SVN \u51b2\u7a81\u6807\u8bb0"
            "\uff08`<<<<<<<` / `=======` / `>>>>>>>`\uff09\uff0c\u65e0\u6cd5\u89e3\u6790\u4e3a XML\u3002"
            "\u8bf7\u5728\u5916\u90e8\u7f16\u8f91\u5668\u4e2d\u624b\u52a8\u6e05\u7406\u6807\u8bb0\uff0c"
            "\u6216\u8fd0\u884c `svn revert` \u540e\u91cd\u65b0\u8d70\u8bed\u4e49\u5408\u5e76\u6d41\u7a0b"
        )

    if str(theirs_rev_hint).upper() == "HEAD":
        info = svn_helper.get_svn_info(fpath)
        head = svn_helper.get_remote_head_revision(info["url"]) if info and info.get("url") else None
        if head is None:
            raise ValueError("\u65e0\u6cd5\u83b7\u53d6\u8fdc\u7a0b HEAD \u7248\u672c")
        theirs_rev_int = int(head)
    else:
        try:
            theirs_rev_int = int(theirs_rev_hint)
        except (TypeError, ValueError):
            raise ValueError("Invalid theirs_rev")

    theirs = _get_file_at_revision(fpath, theirs_rev_int)
    if theirs is None:
        raise ValueError(f"\u65e0\u6cd5\u83b7\u53d6 r{theirs_rev_int} \u7248\u672c")

    return {
        "is_conflict": False,
        "base": base,
        "mine": mine,
        "theirs": theirs,
        "base_label": "SVN BASE",
        "mine_label": "\u5de5\u4f5c\u526f\u672c",
        "theirs_label": f"r{theirs_rev_int}",
        "theirs_revision": theirs_rev_int,
        "template_path": fpath,
    }


def _compute_merge_signature(sources: dict, fpath: str) -> str:
    """Build a fingerprint of "what merge inputs preview just resolved", so
    /api/merge/apply can refuse to operate on a working copy whose SVN state
    drifted between the two calls (e.g. user ran ``svn resolve`` on the side).

    Three components:
      - ``is_conflict``: whether the file is in SVN text-conflict state.
        Flips on external ``svn resolve`` or ``svn update`` that introduces
        a conflict.
      - ``theirs_revision``: the remote revision THEIRS was resolved against.
        Catches "remote HEAD moved between preview and apply" (someone else
        committed).
      - ``mine_stat``: working-copy size + nanosecond mtime. Catches "user
        edited the file in another editor between preview and apply", including
        fast saves within the same wall-clock second.

    Returns a colon-joined string for compact JSON transport.
    """
    try:
        st = os.stat(fpath)
        mine_stat = f"{st.st_size},{getattr(st, 'st_mtime_ns', int(st.st_mtime * 1_000_000_000))}"
    except OSError:
        mine_stat = "0,0"
    return "{0}:{1}:{2}".format(
        int(bool(sources.get("is_conflict"))),
        sources.get("theirs_revision") or 0,
        mine_stat,
    )


def _base_dir():
    """Return the base directory — handles both normal and PyInstaller frozen mode."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))

def _resource_dir():
    """Return the resource directory for bundled assets (static/, etc.)."""
    if getattr(sys, "frozen", False):
        return sys._MEIPASS
    return os.path.dirname(os.path.abspath(__file__))

app = Flask(__name__,
            static_folder=os.path.join(_resource_dir(), "static"),
            static_url_path="/static")
app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 0

CONFIG_PATH = os.path.join(_base_dir(), "config.json")
DEFAULT_WORK_DIR = os.path.join(_base_dir(), "workspace")

_config = None
_config_lock = threading.RLock()


def _load_config() -> dict:
    global _config
    with _config_lock:
        if os.path.isfile(CONFIG_PATH):
            try:
                with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                    _config = json.load(f)
            except (json.JSONDecodeError, OSError):
                _config = None
        if not _config or not isinstance(_config.get("workspaces"), list):
            if os.path.isdir(DEFAULT_WORK_DIR):
                _config = {
                    "workspaces": [{"name": os.path.basename(DEFAULT_WORK_DIR), "path": DEFAULT_WORK_DIR}],
                    "active_workspace": 0,
                }
            else:
                _config = {
                    "workspaces": [],
                    "active_workspace": 0,
                }
        if _config["active_workspace"] >= len(_config["workspaces"]):
            _config["active_workspace"] = 0
        try:
            hr = int(_config.get("header_row", 1))
            if hr < 1:
                hr = 1
        except (TypeError, ValueError):
            hr = 1
        _config["header_row"] = hr
        return _config


def _save_config():
    """Persist config. Returns an error message string on failure, else None."""
    with _config_lock:
        try:
            with open(CONFIG_PATH, "w", encoding="utf-8") as f:
                json.dump(_config, f, ensure_ascii=False, indent=2)
            return None
        except OSError as e:
            print(f"[WARN] Failed to save config: {e}", flush=True)
            return str(e)


def _get_work_dir() -> str:
    cfg = _load_config()
    if not cfg["workspaces"]:
        return ""
    idx = min(cfg["active_workspace"], len(cfg["workspaces"]) - 1)
    return cfg["workspaces"][idx]["path"]


def _get_header_row() -> int:
    cfg = _load_config()
    return cfg.get("header_row", 1)


def _safe_workspace_path(filename: str):
    """Resolve a client-supplied relative filename inside the active workspace.

    Returns the absolute path, or None if the workspace is unset or the path
    escapes the workspace (e.g. via '..' or an absolute path).
    """
    wd = _get_work_dir()
    if not wd or not filename:
        return None
    base = os.path.realpath(wd)
    full = os.path.realpath(os.path.join(base, filename))
    if full == base or full.startswith(base + os.sep):
        return full
    return None


def _rel_to_workdir(path: str, work_dir: str) -> str:
    """Normalize SVN local/remote paths to a comparable workspace-relative form."""
    if not path:
        return ""
    norm_path = os.path.normpath(path)
    norm_work = os.path.normpath(work_dir) if work_dir else ""
    if norm_work:
        try:
            if os.path.isabs(norm_path) and os.path.commonpath([norm_work, norm_path]) == norm_work:
                norm_path = os.path.relpath(norm_path, norm_work)
        except (ValueError, OSError):
            pass
    return norm_path.replace("\\", "/").lstrip("./")


def _normalize_svn_item(item: dict, work_dir: str) -> dict:
    """Keep SVN item names aligned with /api/files relative names."""
    out = dict(item)
    rel = _rel_to_workdir(out.get("path", "") or out.get("name", ""), work_dir)
    if rel:
        out["name"] = rel
    return out


@app.route("/")
def index():
    return send_from_directory(os.path.join(_resource_dir(), "static"), "index.html")


@app.route("/log")
def view_log():
    """Render the rotating server log in a dark, auto-refreshing web page.

    Newest lines first, 5s meta-refresh. Replaces the legacy "open file in
    Notepad" path so users running in tray mode can watch the log live in a
    browser tab.
    """
    path = _log_path()
    if not os.path.isfile(path):
        body = ("<!doctype html><meta charset='utf-8'><title>SheetMeld \u65e5\u5fd7</title>"
                f"<body style='background:#0d1117;color:#c9d1d9;font:13px monospace;padding:16px'>"
                f"\u65e5\u5fd7\u6587\u4ef6\u4e0d\u5b58\u5728\uff1a{html.escape(path)}</body>")
        return (body, 200, {"Content-Type": "text/html; charset=utf-8"})
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            lines = f.read().splitlines()
    except OSError as e:
        body = ("<!doctype html><meta charset='utf-8'><title>SheetMeld \u65e5\u5fd7</title>"
                f"<body style='background:#0d1117;color:#c9d1d9;font:13px monospace;padding:16px'>"
                f"\u8bfb\u53d6\u65e5\u5fd7\u5931\u8d25\uff1a{html.escape(str(e))}</body>")
        return (body, 500, {"Content-Type": "text/html; charset=utf-8"})
    lines.reverse()
    rows = "\n".join(html.escape(l) for l in lines)
    size = os.path.getsize(path)
    mtime = datetime.datetime.fromtimestamp(os.path.getmtime(path)).strftime("%Y-%m-%d %H:%M:%S")
    page = (
        "<!doctype html><html lang=\"zh-CN\"><head>"
        "<meta charset=\"utf-8\"><meta http-equiv=\"refresh\" content=\"5\">"
        "<title>SheetMeld \u65e5\u5fd7</title><style>"
        "html,body{margin:0;padding:0;background:#0d1117;color:#c9d1d9;"
        "font:13px/1.5 Consolas,Menlo,\"Cascadia Mono\",monospace;}"
        "header{position:sticky;top:0;background:#161b22;padding:8px 14px;"
        "border-bottom:1px solid #30363d;display:flex;gap:16px;align-items:center;"
        "flex-wrap:wrap;}"
        "header b{color:#fff;}"
        "header .path{color:#8b949e;}"
        "header .hint{color:#6e7681;font-size:11px;margin-left:auto;}"
        "pre{white-space:pre-wrap;word-break:break-all;margin:0;padding:8px 14px;}"
        "</style></head><body>"
        "<header>"
        "<b>SheetMeld \u65e5\u5fd7</b>"
        f"<span class=\"path\">{html.escape(path)}</span>"
        f"<span>{len(lines)} \u884c \u00b7 {size:,} \u5b57\u8282 \u00b7 \u6700\u540e\u4fee\u6539 {html.escape(mtime)}</span>"
        "<span class=\"hint\">\u6700\u65b0\u65f6\u95f4\u5728\u9876 \u00b7 5 \u79d2\u81ea\u52a8\u5237\u65b0</span>"
        "</header>"
        f"<pre>{rows}</pre></body></html>"
    )
    return (page, 200, {"Content-Type": "text/html; charset=utf-8"})


@app.route("/api/config")
def api_config():
    """Return current configuration and SVN availability."""
    svn_avail = svn_helper.is_available()
    cfg = _load_config()
    wd = _get_work_dir()
    return jsonify({
        "version": __version__,
        "work_dir": wd,
        "svn_available": svn_avail and bool(wd) and os.path.isdir(wd),
        "svn_version": svn_helper.get_version() if svn_avail else None,
        "svn_info": svn_helper.get_svn_info(wd) if svn_avail and wd and os.path.isdir(wd) else None,
        "workspaces": cfg["workspaces"],
        "active_workspace": cfg["active_workspace"],
        "header_row": cfg.get("header_row", 1),
    })


# --- In-app update -----------------------------------------------------------

UPDATE_CACHE_TTL = 3600  # avoid hammering the GitHub API (rate limit)
_update_cache = {"result": None, "ts": 0.0}
_update_cache_lock = threading.Lock()


@app.route("/api/update/check")
def api_update_check():
    """Check GitHub Releases for a newer version (cached 1h; ?force=1 bypasses)."""
    force = request.args.get("force") == "1"
    now = time.time()
    with _update_cache_lock:
        cached = _update_cache["result"]
        if not force and cached and now - _update_cache["ts"] < UPDATE_CACHE_TTL:
            return jsonify(dict(cached, cached=True))
    try:
        result = updater.check_update(__version__)
    except Exception as e:
        return jsonify({"error": f"update check failed: {e}"}), 502
    with _update_cache_lock:
        _update_cache["result"] = result
        _update_cache["ts"] = now
    return jsonify(dict(result, cached=False))


@app.route("/api/update/download", methods=["POST"])
def api_update_download():
    """Start downloading the update asset in a background thread."""
    if not updater.is_frozen():
        return jsonify({"error": "source mode: update via git pull"}), 400
    body = request.get_json(silent=True) or {}
    with _update_cache_lock:
        cached = _update_cache["result"] or {}
    asset_url = body.get("asset_url") or cached.get("asset_url")
    if not asset_url:
        return jsonify({"error": "no update asset available; check for updates first"}), 400
    return jsonify(updater.start_download(asset_url))


@app.route("/api/update/progress")
def api_update_progress():
    """Return the current download state."""
    return jsonify(updater.get_progress())


@app.route("/api/update/apply", methods=["POST"])
def api_update_apply():
    """Swap in the downloaded exe and restart (frozen mode only)."""
    result = updater.apply_update()
    if not result.get("ok"):
        return jsonify({"error": result.get("error")}), 400
    return jsonify({"ok": True})


@app.route("/api/workspaces", methods=["GET"])
def api_workspaces():
    """Return workspace list."""
    cfg = _load_config()
    return jsonify({
        "workspaces": cfg["workspaces"],
        "active_workspace": cfg["active_workspace"],
    })


@app.route("/api/workspaces/switch", methods=["POST"])
def api_workspaces_switch():
    """Switch active workspace by index."""
    body = request.get_json(force=True)
    idx = body.get("index")
    cfg = _load_config()
    if idx is None or not isinstance(idx, int) or idx < 0 or idx >= len(cfg["workspaces"]):
        return jsonify({"error": "Invalid workspace index"}), 400
    cfg["active_workspace"] = idx
    save_err = _save_config()
    new_path = cfg["workspaces"][idx]["path"]
    logger.info("Workspace switched: idx=%d path=%s", idx, new_path)
    return jsonify({"ok": True, "work_dir": new_path,
                    "warning": save_err})


@app.route("/api/workspaces/add", methods=["POST"])
def api_workspaces_add():
    """Add a new workspace directory."""
    body = request.get_json(force=True)
    path = body.get("path", "").strip()
    if not path:
        return jsonify({"error": "path required"}), 400
    path = os.path.normpath(path)
    if not os.path.isdir(path):
        return jsonify({"error": f"Directory not found: {path}"}), 404
    cfg = _load_config()
    for ws in cfg["workspaces"]:
        if os.path.normpath(ws["path"]) == path:
            return jsonify({"error": "Workspace already exists"}), 409
    name = body.get("name", "") or os.path.basename(path)
    cfg["workspaces"].append({"name": name, "path": path})
    cfg["active_workspace"] = len(cfg["workspaces"]) - 1
    save_err = _save_config()
    logger.info("Workspace added: name=%s path=%s", name, path)
    return jsonify({"ok": True, "workspaces": cfg["workspaces"],
                    "active_workspace": cfg["active_workspace"], "warning": save_err})


@app.route("/api/workspaces/remove", methods=["POST"])
def api_workspaces_remove():
    """Remove a workspace by index (cannot remove last one)."""
    body = request.get_json(force=True)
    idx = body.get("index")
    cfg = _load_config()
    if idx is None or not isinstance(idx, int) or idx < 0 or idx >= len(cfg["workspaces"]):
        return jsonify({"error": "Invalid workspace index"}), 400
    if len(cfg["workspaces"]) <= 1:
        return jsonify({"error": "Cannot remove the last workspace"}), 400
    removed = cfg["workspaces"].pop(idx)
    if cfg["active_workspace"] >= len(cfg["workspaces"]):
        cfg["active_workspace"] = len(cfg["workspaces"]) - 1
    save_err = _save_config()
    logger.info("Workspace removed: idx=%d path=%s", idx, removed.get("path", ""))
    return jsonify({"ok": True, "workspaces": cfg["workspaces"],
                    "active_workspace": cfg["active_workspace"], "warning": save_err})


@app.route("/api/pick-dir", methods=["POST"])
def api_pick_dir():
    """Open native OS directory picker dialog."""
    try:
        import tkinter as tk
        from tkinter import filedialog
        root = tk.Tk()
        root.withdraw()
        root.attributes('-topmost', True)
        folder = filedialog.askdirectory(title="选择工作区目录")
        root.destroy()
        if not folder:
            return jsonify({"path": None})
        return jsonify({"path": folder})
    except Exception:
        return jsonify({"error": "native_unavailable"}), 501


@app.route("/api/open-dir", methods=["POST"])
def api_open_dir():
    """Open the current workspace directory in the system file manager."""
    wd = _get_work_dir()
    if not os.path.isdir(wd):
        return jsonify({"error": "Directory not found"}), 404
    try:
        if os.name == "nt":
            os.startfile(os.path.normpath(wd))
        else:
            import subprocess as sp
            sp.Popen(["xdg-open", wd])
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/browse-dir")
def api_browse_dir():
    """Browse directories on server filesystem for workspace selection."""
    import string
    path = request.args.get("path", "").strip()
    if not path:
        if os.name == "nt":
            drives = [f"{d}:\\" for d in string.ascii_uppercase if os.path.exists(f"{d}:\\")]
            return jsonify({"path": "", "dirs": drives, "is_root": True})
        else:
            return jsonify({"path": "/", "dirs": ["/"], "is_root": True})
    path = os.path.normpath(path)
    if not os.path.isdir(path):
        return jsonify({"error": "Not a directory"}), 404
    dirs = []
    try:
        for entry in sorted(os.scandir(path), key=lambda e: e.name.lower()):
            if entry.is_dir() and not entry.name.startswith("."):
                try:
                    dirs.append(entry.path)
                except OSError:
                    pass
    except PermissionError:
        pass
    return jsonify({"path": path, "dirs": dirs, "is_root": False})


@app.route("/api/settings", methods=["POST"])
def api_settings():
    """Update application settings (e.g. header_row)."""
    body = request.get_json(force=True)
    cfg = _load_config()
    if "header_row" in body:
        try:
            hr = int(body["header_row"])
            if hr < 1:
                return jsonify({"error": "header_row must be >= 1"}), 400
        except (TypeError, ValueError):
            return jsonify({"error": "header_row must be an integer"}), 400
        cfg["header_row"] = hr
    save_err = _save_config()
    return jsonify({"ok": True, "header_row": cfg.get("header_row", 1),
                    "warning": save_err})


@app.route("/api/files")
def api_files():
    """List supported files in work directory (including subdirectories)."""
    wd = _get_work_dir()
    if not wd or not os.path.isdir(wd):
        return jsonify({"files": [], "message": "Please add a workspace directory"})

    files = []
    for dirpath, _dirnames, filenames in os.walk(wd):
        for f in filenames:
            if f.lower().endswith(SUPPORTED_EXTENSIONS):
                fpath = os.path.join(dirpath, f)
                rel = os.path.relpath(fpath, wd).replace("\\", "/")
                files.append({
                    "name": rel,
                    "size": os.path.getsize(fpath),
                    "modified": os.path.getmtime(fpath),
                })
    files.sort(key=lambda x: x["name"].lower())
    return jsonify({"files": files, "count": len(files)})


@app.route("/api/file-mtime")
def api_file_mtime():
    """Get last modification time of a file (for auto-refresh polling)."""
    filename = request.args.get("file", "")
    if not filename:
        return jsonify({"error": "file parameter required"}), 400
    fpath = _safe_workspace_path(filename)
    if fpath is None:
        return jsonify({"error": "Invalid file path"}), 400
    if not os.path.exists(fpath):
        return jsonify({"mtime": 0})
    return jsonify({"mtime": os.path.getmtime(fpath)})


@app.route("/api/svn/modified")
def api_svn_modified():
    """Get locally modified XML files."""
    if not svn_helper.is_available():
        return jsonify({"error": "SVN not available"}), 503
    wd = _get_work_dir()
    modified = [_normalize_svn_item(item, wd) for item in svn_helper.get_modified_files(wd)]
    return jsonify({"files": modified, "count": len(modified)})


@app.route("/api/svn/modified-classify")
def api_svn_modified_classify():
    """Classify modified files: 'data' (substantive cell changes) or 'meta' (format/structure only)."""
    if not svn_helper.is_available():
        return jsonify({"error": "SVN not available"}), 503
    wd = _get_work_dir()
    modified = [_normalize_svn_item(item, wd) for item in svn_helper.get_modified_files(wd)]
    hr = _get_header_row()
    result = {}
    for item in modified:
        fname = item["name"]
        if item["status"] in ("added", "deleted"):
            result[fname] = item["status"]
            continue
        try:
            base = _get_base_content(item["path"])
            if base is None:
                result[fname] = "data"
                continue
            old_data = _parse_content(base, fname, header_row=hr)
            new_data = _parse_file(item["path"], header_row=hr)
            diff = xml_differ.diff_workbooks(old_data, new_data)
            s = diff.get("summary", {})
            has_data = (s.get("total_modified_cells", 0) +
                        s.get("total_added_rows", 0) +
                        s.get("total_removed_rows", 0)) > 0
            result[fname] = "data" if has_data else "meta"
        except Exception:
            result[fname] = "data"
    return jsonify({"classify": result})


@app.route("/api/svn/log")
def api_svn_log():
    """Get SVN log for a file."""
    filename = request.args.get("file", "")
    limit = int(request.args.get("limit", "20"))
    if not filename:
        return jsonify({"error": "file parameter required"}), 400
    fpath = _safe_workspace_path(filename)
    if fpath is None:
        return jsonify({"error": "Invalid file path"}), 400
    if not os.path.exists(fpath):
        return jsonify({"error": f"File not found: {filename}"}), 404
    entries = svn_helper.get_log(fpath, limit=limit)
    return jsonify({"entries": entries, "count": len(entries)})


@app.route("/api/parse")
def api_parse():
    """Parse an XML file and return its data structure."""
    filename = request.args.get("file", "")
    if not filename:
        return jsonify({"error": "file parameter required"}), 400
    fpath = _safe_workspace_path(filename)
    if fpath is None:
        return jsonify({"error": "Invalid file path"}), 400
    if not os.path.exists(fpath):
        return jsonify({"error": f"File not found: {filename}"}), 404
    try:
        data = _parse_file(fpath, header_row=_get_header_row())
        return jsonify(data)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/diff/local", methods=["POST"])
def api_diff_local():
    """Diff local working copy vs SVN BASE for a file."""
    body = request.get_json(force=True)
    filename = body.get("file", "")
    if not filename:
        return jsonify({"error": "file parameter required"}), 400

    fpath = _safe_workspace_path(filename)
    if fpath is None:
        return jsonify({"error": "Invalid file path"}), 400
    if not os.path.exists(fpath):
        return jsonify({"error": f"File not found: {filename}"}), 404

    if not svn_helper.is_available():
        return jsonify({"error": "SVN not available"}), 503

    base_content = _get_base_content(fpath)
    if base_content is None:
        print(f"[WARN] Failed to get SVN BASE for: {fpath}", flush=True)
        return jsonify({"error": f"Failed to get SVN BASE version for {filename}. "
                        "Please ensure the file is under SVN version control."}), 500

    try:
        hr = _get_header_row()
        old_data = _parse_content(base_content, filename, header_row=hr)
        new_data = _parse_file(fpath, header_row=hr)
        diff = xml_differ.diff_workbooks(old_data, new_data, id_column=body.get("id_column"))
        diff["old_label"] = "SVN BASE"
        diff["new_label"] = "工作副本"
        diff["file"] = filename
        return jsonify(diff)
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@app.route("/api/diff/revisions", methods=["POST"])
def api_diff_revisions():
    """Diff two SVN revisions of a file."""
    body = request.get_json(force=True)
    filename = body.get("file", "")
    rev_old = body.get("rev_old")
    rev_new = body.get("rev_new")

    if not filename:
        return jsonify({"error": "file parameter required"}), 400
    if rev_old is None or rev_new is None:
        return jsonify({"error": "rev_old and rev_new required"}), 400

    fpath = _safe_workspace_path(filename)
    if fpath is None:
        return jsonify({"error": "Invalid file path"}), 400

    if not svn_helper.is_available():
        return jsonify({"error": "SVN not available"}), 503

    old_content = _get_file_at_revision(fpath, int(rev_old))
    if old_content is None:
        return jsonify({"error": f"Failed to get revision {rev_old}"}), 500

    hr = _get_header_row()
    if str(rev_new).upper() == "WORKING":
        if not os.path.exists(fpath):
            return jsonify({"error": f"File not found: {filename}"}), 404
        new_data = _parse_file(fpath, header_row=hr)
        new_label = "工作副本"
    else:
        new_content = _get_file_at_revision(fpath, int(rev_new))
        if new_content is None:
            return jsonify({"error": f"Failed to get revision {rev_new}"}), 500
        new_data = _parse_content(new_content, filename, header_row=hr)
        new_label = f"r{rev_new}"

    try:
        old_data = _parse_content(old_content, filename, header_row=hr)
        diff = xml_differ.diff_workbooks(old_data, new_data, id_column=body.get("id_column"))
        diff["old_label"] = f"r{rev_old}"
        diff["new_label"] = new_label
        diff["file"] = filename
        return jsonify(diff)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/svn/dir-log")
def api_svn_dir_log():
    """Get SVN log for the entire work directory."""
    if not svn_helper.is_available():
        return jsonify({"error": "SVN not available"}), 503
    limit = int(request.args.get("limit", "50"))
    entries = svn_helper.get_dir_log(_get_work_dir(), limit=limit)
    return jsonify({"entries": entries, "count": len(entries)})


@app.route("/api/svn/changed-files")
def api_svn_changed_files():
    """Get list of files changed between two SVN revisions."""
    if not svn_helper.is_available():
        return jsonify({"error": "SVN not available"}), 503
    rev_old = request.args.get("rev_old")
    rev_new = request.args.get("rev_new")
    if not rev_old or not rev_new:
        return jsonify({"error": "rev_old and rev_new required"}), 400
    files = svn_helper.get_changed_files_between_revisions(
        _get_work_dir(), int(rev_old), int(rev_new))
    return jsonify({"files": files, "count": len(files)})


@app.route("/api/diff/overview", methods=["POST"])
def api_diff_overview():
    """Diff all changed XML files between two SVN revisions (version overview)."""
    if not svn_helper.is_available():
        return jsonify({"error": "SVN not available"}), 503
    body = request.get_json(force=True)
    rev_old = body.get("rev_old")
    rev_new = body.get("rev_new")
    if rev_old is None or rev_new is None:
        return jsonify({"error": "rev_old and rev_new required"}), 400

    wd = _get_work_dir()
    changed = svn_helper.get_changed_files_between_revisions(
        wd, int(rev_old), int(rev_new))

    hr = _get_header_row()
    results = []
    for item in changed:
        fname = item["name"]
        fpath = item.get("url") or os.path.join(wd, fname)

        if item["status"] == "deleted":
            results.append({
                "file": fname, "status": "deleted",
                "summary": {"has_changes": True, "total_removed_rows": 0},
            })
            continue

        if item["status"] == "added":
            try:
                new_content = _get_file_at_revision(fpath, int(rev_new))
                if new_content is None and os.path.exists(fpath):
                    new_data = _parse_file(fpath, header_row=hr)
                elif new_content is not None:
                    new_data = _parse_content(new_content, fname, header_row=hr)
                else:
                    results.append({"file": fname, "status": "added",
                                    "summary": {"has_changes": True}})
                    continue
                total_rows = sum(s["row_count"] for s in new_data["sheets"].values())
                results.append({
                    "file": fname, "status": "added",
                    "summary": {"has_changes": True, "total_added_rows": total_rows,
                                "sheets": len(new_data["sheets"])},
                })
            except Exception as e:
                results.append({"file": fname, "status": "error", "error": str(e)})
            continue

        try:
            old_content = _get_file_at_revision(fpath, int(rev_old))
            new_content = _get_file_at_revision(fpath, int(rev_new))
            if old_content is None or new_content is None:
                results.append({"file": fname, "status": "error",
                                "error": "Cannot get revision content"})
                continue
            old_data = _parse_content(old_content, fname, header_row=hr)
            new_data = _parse_content(new_content, fname, header_row=hr)
            diff = xml_differ.diff_workbooks(old_data, new_data)
            results.append({
                "file": fname, "status": item["status"],
                "summary": diff["summary"], "diff": diff,
            })
        except Exception as e:
            results.append({"file": fname, "status": "error", "error": str(e)})

    has_data_changes = sum(1 for r in results
                          if r.get("summary", {}).get("has_changes", False)
                          and r.get("summary", {}).get("total_modified_cells", 0) +
                              r.get("summary", {}).get("total_added_rows", 0) +
                              r.get("summary", {}).get("total_removed_rows", 0) > 0)

    return jsonify({
        "files": results,
        "total_files": len(results),
        "data_changed_files": has_data_changes,
        "rev_old": rev_old,
        "rev_new": rev_new,
    })


@app.route("/api/diff/batch", methods=["POST"])
def api_diff_batch():
    """Batch diff all locally modified XML files."""
    if not svn_helper.is_available():
        return jsonify({"error": "SVN not available"}), 503

    wd = _get_work_dir()
    modified = [_normalize_svn_item(item, wd) for item in svn_helper.get_modified_files(wd)]
    hr = _get_header_row()
    results = []
    for item in modified:
        fpath = item["path"]
        if item["status"] == "deleted":
            results.append({
                "file": item["name"],
                "status": "deleted",
                "summary": {"has_changes": True},
            })
            continue
        if item["status"] == "added":
            try:
                new_data = _parse_file(fpath, header_row=hr)
                total_rows = sum(s["row_count"] for s in new_data["sheets"].values())
                results.append({
                    "file": item["name"],
                    "status": "added",
                    "summary": {
                        "has_changes": True,
                        "total_added_rows": total_rows,
                        "sheets": len(new_data["sheets"]),
                    },
                })
            except Exception as e:
                results.append({"file": item["name"], "status": "error", "error": str(e)})
            continue

        try:
            base_content = _get_base_content(fpath)
            if base_content is None:
                results.append({"file": item["name"], "status": "error", "error": "Cannot get BASE"})
                continue
            old_data = _parse_content(base_content, item["name"], header_row=hr)
            new_data = _parse_file(fpath, header_row=hr)
            diff = xml_differ.diff_workbooks(old_data, new_data)
            results.append({
                "file": item["name"],
                "status": "modified",
                "summary": diff["summary"],
            })
        except Exception as e:
            results.append({"file": item["name"], "status": "error", "error": str(e)})

    return jsonify({"files": results, "count": len(results)})


@app.route("/api/merge/preview", methods=["POST"])
def api_merge_preview():
    """Three-way semantic merge preview: BASE / MINE (working copy) / THEIRS (SVN HEAD or specific revision).

    Body: {"file": "...", "theirs_rev"?: int or "HEAD"}
    XML-only feature; other extensions return 400.
    """
    if not svn_helper.is_available():
        return jsonify({"error": "SVN not available"}), 503
    body = request.get_json(force=True) or {}
    filename = body.get("file", "")
    theirs_rev = body.get("theirs_rev", "HEAD")
    if not filename:
        return jsonify({"error": "file parameter required"}), 400
    if _is_excel_binary(filename) or not filename.lower().endswith(".xml"):
        return jsonify({"error": "\u8bed\u4e49\u5408\u5e76\u4ec5\u652f\u6301 .xml (SpreadsheetML 2003)"}), 400

    fpath = _safe_workspace_path(filename)
    if fpath is None:
        return jsonify({"error": "Invalid file path"}), 400
    if not os.path.exists(fpath):
        return jsonify({"error": f"File not found: {filename}"}), 404

    try:
        sources = _resolve_merge_sources(fpath, theirs_rev_hint=theirs_rev)
    except ValueError as e:
        logger.warning("Merge preview failed (sources): file=%s err=%s", filename, e)
        return jsonify({"error": str(e)}), 500

    try:
        hr = _get_header_row()
        base = xml_parser.parse_string(sources["base"], header_row=hr)
        mine = xml_parser.parse_string(sources["mine"], header_row=hr)
        theirs = xml_parser.parse_string(sources["theirs"], header_row=hr)
        result = xml_merger.three_way_diff(base, mine, theirs,
                                           id_column=body.get("id_column"))
    except xml_merger.UnsupportedSheetChange as e:
        return jsonify({"error": str(e), "error_code": "unsupported_sheet_change", "sheets": e.sheets}), 400
    except Exception as e:
        import traceback
        traceback.print_exc()
        logger.warning("Merge preview failed (diff): file=%s err=%s", filename, e)
        return jsonify({"error": str(e)}), 500

    result["file"] = filename
    result["base_label"] = sources["base_label"]
    result["mine_label"] = sources["mine_label"]
    result["theirs_label"] = sources["theirs_label"]
    result["theirs_revision"] = sources["theirs_revision"]
    result["from_svn_conflict"] = sources["is_conflict"]
    # Fingerprint of the resolved inputs; client must echo this back on
    # /api/merge/apply so the backend can detect external SVN drift.
    result["merge_signature"] = _compute_merge_signature(sources, fpath)
    summary = result.get("summary", {}) or {}
    logger.info("Merge preview: file=%s from_svn_conflict=%s auto=%d conflicts=%d sig=%s",
                filename, sources["is_conflict"],
                int(summary.get("auto_resolved", 0)),
                int(summary.get("conflicts", 0)),
                result["merge_signature"])
    return jsonify(result)


@app.route("/api/merge/apply", methods=["POST"])
def api_merge_apply():
    """Apply user resolutions and write the merged file back to the working copy.

    Body: {"file", "resolutions": [...], "theirs_rev"?, "mark_resolved"?}
    Re-runs three_way_diff so the merge plan is computed fresh from current
    on-disk state (so the merge is robust against concurrent edits).
    """
    if not svn_helper.is_available():
        return jsonify({"error": "SVN not available"}), 503
    body = request.get_json(force=True) or {}
    filename = body.get("file", "")
    resolutions = body.get("resolutions", [])
    theirs_rev = body.get("theirs_rev", "HEAD")
    mark_resolved = bool(body.get("mark_resolved", False))
    if not filename:
        return jsonify({"error": "file parameter required"}), 400
    if _is_excel_binary(filename) or not filename.lower().endswith(".xml"):
        return jsonify({"error": "\u8bed\u4e49\u5408\u5e76\u4ec5\u652f\u6301 .xml"}), 400

    fpath = _safe_workspace_path(filename)
    if fpath is None:
        return jsonify({"error": "Invalid file path"}), 400
    if not os.path.exists(fpath):
        return jsonify({"error": f"File not found: {filename}"}), 404

    try:
        sources = _resolve_merge_sources(fpath, theirs_rev_hint=theirs_rev)
    except ValueError as e:
        logger.warning("Merge apply failed (sources): file=%s err=%s", filename, e)
        return jsonify({"error": str(e)}), 500

    # Refuse to apply if SVN state drifted since preview (e.g. user ran
    # `svn resolve` / external editor saved the file in another window).
    # Sending no signature (legacy / curl) is allowed for backward compat.
    client_sig = body.get("merge_signature")
    if client_sig:
        current_sig = _compute_merge_signature(sources, fpath)
        if client_sig != current_sig:
            logger.warning("Merge apply stale signature: file=%s client=%s current=%s",
                           filename, client_sig, current_sig)
            return jsonify({
                "error": "\u6587\u4ef6\u72b6\u6001\u5728\u9884\u89c8\u4e4b\u540e\u88ab\u5916\u90e8\u6539\u53d8"
                         "\uff08\u5982\u547d\u4ee4\u884c svn resolve / update / \u7f16\u8f91\u5668\u4fdd\u5b58\uff09\uff0c"
                         "\u8bf7\u91cd\u65b0\u9884\u89c8\u540e\u518d\u5408\u5e76\u3002",
                "stale": True,
                "client_signature": client_sig,
                "current_signature": current_sig,
            }), 409

    try:
        hr = _get_header_row()
        base = xml_parser.parse_string(sources["base"], header_row=hr)
        mine = xml_parser.parse_string(sources["mine"], header_row=hr)
        theirs = xml_parser.parse_string(sources["theirs"], header_row=hr)
        result = xml_merger.three_way_diff(base, mine, theirs,
                                           id_column=body.get("id_column"))
        applied = xml_merger.apply_resolutions(result, resolutions)
        if not applied["ok"]:
            logger.warning("Merge apply rejected (unresolved): file=%s unresolved=%d",
                           filename, len(applied.get("unresolved", []) or []))
            return jsonify({
                "error": "\u5b58\u5728\u672a\u51b3\u8bae\u7684\u51b2\u7a81",
                "unresolved": applied["unresolved"],
            }), 400

        # Count what is actually being written to disk: that includes both
        # user-supplied resolutions and auto-merged decisions (e.g. THEIRS
        # added/modified rows that the user never had to click on). Without
        # this count the UI would say "0 resolutions applied" whenever every
        # change was auto-mergeable, even though the file was correctly
        # merged on disk.
        total_changes = 0
        for sheet_result in result.get("sheets", {}).values():
            ops = xml_merger._compute_sheet_operations(sheet_result)
            total_changes += (len(ops["update_rows"])
                              + len(ops["insert_rows"])
                              + len(ops["remove_rows"]))

        xml_merger.write_merged_xml(sources["template_path"], result, fpath)
    except xml_merger.UnsupportedSheetChange as e:
        return jsonify({"error": str(e), "error_code": "unsupported_sheet_change", "sheets": e.sheets}), 400
    except Exception as e:
        import traceback
        traceback.print_exc()
        logger.warning("Merge apply failed (write): file=%s err=%s", filename, e)
        return jsonify({"error": str(e)}), 500

    # Auto-resolve SVN conflict when the merge originated from one, since the
    # working-copy file now holds the fully-merged content (no markers).
    svn_resolved = False
    if mark_resolved or sources["is_conflict"]:
        rc, _out, _err = svn_helper._run("resolve", "--accept", "working", fpath)
        svn_resolved = (rc == 0)
        if not svn_resolved:
            logger.warning("svn resolve failed: file=%s rc=%d err=%s",
                           filename, rc, (_err or "").strip()[:200])

    logger.info("Merge apply: file=%s resolutions=%d total_changes=%d svn_resolved=%s "
                "from_svn_conflict=%s",
                filename, int(applied.get("applied", 0)), total_changes,
                svn_resolved, sources["is_conflict"])
    return jsonify({
        "ok": True,
        "file": filename,
        "applied": applied["applied"],
        "total_changes": total_changes,
        "svn_resolved": svn_resolved,
        "from_svn_conflict": sources["is_conflict"],
        "theirs_revision": sources["theirs_revision"],
    })


@app.route("/api/merge/svn-mark-resolved", methods=["POST"])
def api_merge_svn_mark_resolved():
    """Mark a file as resolved in SVN's conflict state (svn resolve --accept working)."""
    if not svn_helper.is_available():
        return jsonify({"error": "SVN not available"}), 503
    body = request.get_json(force=True) or {}
    filename = body.get("file", "")
    if not filename:
        return jsonify({"error": "file parameter required"}), 400
    fpath = _safe_workspace_path(filename)
    if fpath is None:
        return jsonify({"error": "Invalid file path"}), 400
    if not os.path.exists(fpath):
        return jsonify({"error": f"File not found: {filename}"}), 404
    rc, _out, err = svn_helper._run("resolve", "--accept", "working", fpath)
    if rc != 0:
        logger.warning("SVN mark resolved failed: file=%s rc=%d err=%s",
                       filename, rc, (err or "").strip()[:200])
        return jsonify({"error": err or "svn resolve failed"}), 500
    logger.info("SVN mark resolved: file=%s", filename)
    return jsonify({"ok": True, "file": filename})


@app.route("/api/svn/remote-revision")
def api_svn_remote_revision():
    """Get remote HEAD revision and local BASE revision for comparison."""
    if not svn_helper.is_available():
        return jsonify({"error": "SVN not available"}), 503
    wd = _get_work_dir()
    local_info = svn_helper.get_svn_info(wd)
    if not local_info or not local_info.get("url"):
        return jsonify({"error": "Not an SVN working copy"}), 400
    status = svn_helper.get_remote_update_status(wd)
    if status is None:
        return jsonify({"error": "Cannot check remote SVN status"}), 502
    remote_rev = status["remote_revision"]
    local_rev = int(local_info.get("revision", 0)) if local_info.get("revision", "").isdigit() else 0
    if status["local_revision"] is not None:
        local_rev = min(local_rev, status["local_revision"])
    return jsonify({
        "remote_revision": remote_rev,
        "local_revision": local_rev,
        "has_update": status["has_update"],
    })


@app.route("/api/svn/conflicted")
def api_svn_conflicted():
    """List files currently in SVN conflicted state (workspace-relative)."""
    if not svn_helper.is_available():
        return jsonify({"error": "SVN not available"}), 503
    wd = _get_work_dir()
    if not wd:
        return jsonify({"files": [], "count": 0})
    rels = sorted({
        _rel_to_workdir(p, wd)
        for p in svn_helper.get_conflicted_files(wd) if p
    })
    return jsonify({"files": rels, "count": len(rels)})


@app.route("/api/svn/update", methods=["POST"])
def api_svn_update():
    """Smart SVN update with conflict detection."""
    if not svn_helper.is_available():
        return jsonify({"error": "SVN not available"}), 503
    wd = _get_work_dir()
    body = request.get_json(force=True) or {}

    if body.get("check_only"):
        local_modified = svn_helper.get_modified_files(wd)
        local_mod_names = {
            _rel_to_workdir(f.get("path", ""), wd)
            for f in local_modified
            if f.get("path")
        }
        remote_changed = [
            _rel_to_workdir(f, wd)
            for f in svn_helper.get_remote_changed_files(wd)
            if f
        ]
        conflicts = [f for f in remote_changed if f in local_mod_names]
        safe = [f for f in remote_changed if f not in local_mod_names]
        return jsonify({
            "conflicts": conflicts,
            "safe_updates": len(safe),
            "local_only": [f for f in sorted(local_mod_names) if f not in set(remote_changed)],
        })

    skip_files = body.get("skip_files", [])
    theirs_files = body.get("theirs_files", [])
    mine_files = body.get("mine_files", [])
    semantic_files = body.get("semantic_files", [])
    logger.info("SVN update start: skip=%d theirs=%d mine=%d semantic=%d",
                len(skip_files), len(theirs_files), len(mine_files), len(semantic_files))
    result = svn_helper.smart_update(wd, skip_files, theirs_files,
                                     mine_files, semantic_files)
    logger.info("SVN update done: updated=%d skipped=%d theirs=%d mine=%d semantic=%d errors=%d",
                int(result.get("updated", 0) or 0),
                len(result.get("skipped", []) or []),
                len(result.get("theirs", []) or []),
                len(result.get("mine", []) or []),
                len(result.get("semantic", []) or []),
                len(result.get("errors", []) or []))
    return jsonify(result)


def open_browser(port):
    """Open browser after a short delay to let server start."""
    import time
    time.sleep(1)
    webbrowser.open(f"http://localhost:{port}")


def kill_existing_on_port(port):
    """Kill any existing process listening on exactly the given port.

    Parses the netstat local-address column and matches the port exactly,
    so e.g. port 55660 is never mistaken for 5566.
    """
    try:
        import subprocess
        # In tray (pythonw) mode, child processes would otherwise pop a
        # short-lived console window.
        no_window = 0x08000000 if os.name == "nt" else 0
        hide = {"creationflags": no_window} if no_window else {}
        result = subprocess.run(
            ["netstat", "-ano"], capture_output=True, text=True, timeout=5,
            **hide)
        for line in result.stdout.splitlines():
            parts = line.split()
            # Expected: proto, local_addr, foreign_addr, state, pid
            if len(parts) < 5 or parts[3].upper() != "LISTENING":
                continue
            local_addr = parts[1]
            if not local_addr.endswith(f":{port}"):
                continue
            pid = parts[4]
            if pid.isdigit() and int(pid) != os.getpid():
                subprocess.run(["taskkill", "/PID", pid, "/F"],
                               capture_output=True, timeout=5, **hide)
    except Exception:
        pass


def _log_path() -> str:
    """Resolve the log file path next to the executable / source script."""
    logs_dir = os.path.join(_base_dir(), "logs")
    try:
        os.makedirs(logs_dir, exist_ok=True)
    except OSError:
        logs_dir = _base_dir()
    return os.path.join(logs_dir, "server.log")


class _StreamToLogger:
    """File-like object that forwards writes to a logger (for stdout/stderr).

    Accepts both ``str`` and ``bytes`` payloads because some libraries (click,
    werkzeug) write bytes directly when the stream's ``buffer`` attribute is
    absent. Mimics text-mode stdout so callers that probe ``encoding`` /
    ``isatty()`` keep working.
    """

    encoding = "utf-8"
    errors = "replace"

    def __init__(self, logger, level=logging.INFO):
        self._logger = logger
        self._level = level
        self._buf = ""

    def write(self, msg):
        if not msg:
            return
        if isinstance(msg, (bytes, bytearray)):
            try:
                msg = bytes(msg).decode(self.encoding, errors=self.errors)
            except Exception:
                msg = str(msg)
        elif not isinstance(msg, str):
            msg = str(msg)
        self._buf += msg
        while "\n" in self._buf:
            line, self._buf = self._buf.split("\n", 1)
            if line.strip():
                self._logger.log(self._level, line)

    def writelines(self, lines):
        for line in lines:
            self.write(line)

    def flush(self):
        if self._buf.strip():
            self._logger.log(self._level, self._buf.strip())
        self._buf = ""

    def isatty(self):
        return False

    def fileno(self):
        raise OSError("no fileno (logger stream)")


def _configure_file_logging(log_path: str, redirect_stdio: bool):
    """Wire root logger + (optionally) stdout/stderr into a rotating file."""
    handler = RotatingFileHandler(log_path, maxBytes=1_000_000,
                                  backupCount=3, encoding="utf-8")
    handler.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(handler)
    logging.getLogger("werkzeug").setLevel(logging.WARNING)

    if redirect_stdio:
        logger = logging.getLogger("sheetmeld")
        sys.stdout = _StreamToLogger(logger, logging.INFO)
        sys.stderr = _StreamToLogger(logger, logging.ERROR)


def _run_flask(port: int):
    """Run Flask. Disables the reloader so it can live in a background thread."""
    app.run(host="127.0.0.1", port=port, debug=False, use_reloader=False)


def _main():
    port = int(os.environ.get("PORT", 5566))
    kill_existing_on_port(port)

    # --console keeps the legacy console behaviour for debugging.
    use_tray = _HAS_TRAY and ("--console" not in sys.argv)
    log_path = _log_path()
    _configure_file_logging(log_path, redirect_stdio=use_tray)

    print(f"SheetMeld starting on http://localhost:{port}")
    print(f"Work directory: {_get_work_dir()}")
    print(f"SVN: {'available' if svn_helper.is_available() else 'not found'}")
    print(f"Tray: {'enabled' if use_tray else 'disabled (console mode)'}")
    print(f"Log file: {log_path}")

    if use_tray:
        threading.Thread(target=_run_flask, args=(port,), daemon=True).start()
        threading.Thread(target=open_browser, args=(port,), daemon=True).start()
        tray_module.start_tray(
            port=port,
            log_path=log_path,
            workspace_resolver=_get_work_dir,
            shutdown_fn=lambda: None,
            icon_path=os.path.join(_resource_dir(), "static", "img", "miku.svg"),
        )
        # When the tray loop returns without quitting (e.g. unsupported env),
        # fall through to the blocking Flask call.
        os._exit(0)

    threading.Thread(target=open_browser, args=(port,), daemon=True).start()
    _run_flask(port)


if __name__ == "__main__":
    _main()

"""
SVN integration helper.
Auto-detects svn CLI, retrieves version history, file contents at revisions,
and local modification status.
"""
import subprocess
import xml.etree.ElementTree as ET
import os
import re
import urllib.parse
from typing import Optional


# Hide the transient console window each subprocess would otherwise pop up
# when SheetMeld runs in tray (pythonw / --noconsole) mode. Harmless on POSIX.
_NO_WINDOW = 0x08000000 if os.name == "nt" else 0  # subprocess.CREATE_NO_WINDOW


def _hidden_kwargs() -> dict:
    return {"creationflags": _NO_WINDOW} if _NO_WINDOW else {}


def _is_url(target: str) -> bool:
    return target.startswith(("http://", "https://", "svn://", "svn+ssh://", "file://"))


_svn_path: Optional[str] = None
_svn_checked = False
_svn_announced = False


def _find_svn() -> Optional[str]:
    """Auto-detect svn CLI path."""
    global _svn_path, _svn_checked
    if _svn_checked:
        return _svn_path
    _svn_checked = True

    for candidate in ["svn"]:
        try:
            r = subprocess.run(
                [candidate, "--version", "--quiet"],
                capture_output=True, timeout=5, **_hidden_kwargs()
            )
            if r.returncode == 0:
                _svn_path = candidate
                return _svn_path
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass

    tortoise_paths = [
        r"C:\Program Files\TortoiseSVN\bin\svn.exe",
        r"C:\Program Files (x86)\TortoiseSVN\bin\svn.exe",
    ]
    for tp in tortoise_paths:
        if os.path.isfile(tp):
            try:
                r = subprocess.run([tp, "--version", "--quiet"],
                                   capture_output=True, timeout=5,
                                   **_hidden_kwargs())
                if r.returncode == 0:
                    _svn_path = tp
                    return _svn_path
            except (FileNotFoundError, subprocess.TimeoutExpired):
                pass

    return None


def is_available() -> bool:
    global _svn_announced
    svn = _find_svn()
    if svn and not _svn_announced:
        print(f"[SVN] Using: {svn}", flush=True)
        _svn_announced = True
    return svn is not None


def _decode_output(data: bytes) -> str:
    """Decode subprocess output, trying UTF-8 first, then system encoding (GBK on Chinese Windows)."""
    if not data:
        return ""
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        pass
    try:
        return data.decode("gbk")
    except UnicodeDecodeError:
        pass
    return data.decode("utf-8", errors="replace")


def _run(*args, cwd=None, timeout=30) -> tuple:
    """Run svn command, return (returncode, stdout_str, stderr_str)."""
    svn = _find_svn()
    if not svn:
        return (-1, "", "svn not found")
    cmd = [svn] + list(args)
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=timeout, cwd=cwd,
                           **_hidden_kwargs())
        stdout = _decode_output(r.stdout)
        stderr = _decode_output(r.stderr)
        return (r.returncode, stdout, stderr)
    except subprocess.TimeoutExpired:
        return (-1, "", "timeout")
    except Exception as e:
        return (-1, "", str(e))


def _run_raw(*args, cwd=None, timeout=30) -> tuple:
    """Run svn command, return (returncode, stdout_bytes, stderr_str).
    Unlike _run, stdout is kept as raw bytes for binary file content.
    """
    svn = _find_svn()
    if not svn:
        return (-1, b"", "svn not found")
    cmd = [svn] + list(args)
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=timeout, cwd=cwd,
                           **_hidden_kwargs())
        stderr = _decode_output(r.stderr)
        return (r.returncode, r.stdout, stderr)
    except subprocess.TimeoutExpired:
        return (-1, b"", "timeout")
    except Exception as e:
        return (-1, b"", str(e))


def get_svn_info(path: str) -> Optional[dict]:
    """Get svn info for a path."""
    rc, out, err = _run("info", "--xml", path)
    if rc != 0:
        return None
    try:
        root = ET.fromstring(out)
        entry = root.find(".//entry")
        if entry is None:
            return None
        return {
            "url": entry.findtext("url", ""),
            "root": entry.findtext("repository/root", ""),
            "revision": entry.get("revision", ""),
            "last_changed_rev": entry.find("commit").get("revision", "")
                if entry.find("commit") is not None else "",
            "last_changed_author": entry.findtext("commit/author", ""),
            "last_changed_date": entry.findtext("commit/date", ""),
        }
    except ET.ParseError:
        return None


def get_log(path: str, limit: int = 20) -> list:
    """Get SVN log entries for a file or directory. Uses remote URL for latest history."""
    info = get_svn_info(path)
    target = info["url"] if info and info.get("url") else path
    rc, out, err = _run("log", "-l", str(limit), "--xml", "-v",
                        "--stop-on-copy", target, timeout=60)
    if rc != 0 and target != path:
        rc, out, err = _run("log", "-l", str(limit), "--xml", "-v",
                            "--stop-on-copy", path, timeout=60)
    if rc != 0:
        return []

    target_svn_path = ""
    if info and info.get("url") and info.get("root"):
        # svn info returns percent-encoded URLs while log --xml <path> entries
        # are decoded repo paths; decode so non-ASCII names still match.
        target_svn_path = urllib.parse.unquote(info["url"][len(info["root"]):])

    try:
        root = ET.fromstring(out)
        entries = []
        for entry in root.iter("logentry"):
            rev = entry.get("revision", "")
            author = entry.findtext("author", "")
            date_str = entry.findtext("date", "")
            msg = (entry.findtext("msg", "") or "").strip()
            paths_changed = []
            paths_el = entry.find("paths")
            if paths_el is not None:
                for p in paths_el.findall("path"):
                    paths_changed.append({
                        "path": p.text or "",
                        "action": p.get("action", ""),
                    })

            if target_svn_path and paths_changed:
                if not any(cp["path"] == target_svn_path or
                           cp["path"].startswith(target_svn_path + "/") or
                           target_svn_path.startswith(cp["path"] + "/")
                           for cp in paths_changed):
                    continue

            entries.append({
                "revision": int(rev) if rev.isdigit() else rev,
                "author": author,
                "date": date_str,
                "message": msg,
                "paths": paths_changed,
            })
        return entries
    except ET.ParseError:
        return []


def get_file_at_revision(filepath: str, revision: int) -> Optional[str]:
    """Get file content at a specific SVN revision. Uses remote URL for reliability.
    Accepts either a local working-copy path or a direct repository URL.
    """
    if _is_url(filepath):
        rc, out, _ = _run("cat", "-r", str(revision), filepath, timeout=60)
        return out if rc == 0 else None
    info = get_svn_info(filepath)
    target = info["url"] if info and info.get("url") else filepath
    rc, out, err = _run("cat", "-r", str(revision), target, timeout=60)
    if rc == 0:
        return out
    if target != filepath:
        rc2, out2, _ = _run("cat", "-r", str(revision), filepath, timeout=60)
        if rc2 == 0:
            return out2
    return None


def get_base_content(filepath: str) -> Optional[str]:
    """Get the BASE (pristine) version of a working copy file.
    Tries -r BASE first (local, no network), falls back to plain svn cat (network).
    """
    rc, out, err = _run("cat", "-r", "BASE", filepath, timeout=60)
    if rc == 0:
        return out
    print(f"[SVN] cat -r BASE failed (rc={rc}): {err.strip()}", flush=True)
    rc2, out2, err2 = _run("cat", filepath, timeout=60)
    if rc2 == 0:
        print(f"[SVN] Fallback svn cat succeeded for: {filepath}", flush=True)
        return out2
    print(f"[SVN] Fallback svn cat also failed (rc={rc2}): {err2.strip()}", flush=True)
    return None


def get_file_at_revision_raw(filepath: str, revision: int) -> Optional[bytes]:
    """Get file content as raw bytes at a specific SVN revision (for binary files like .xlsx).
    Accepts either a local working-copy path or a direct repository URL.
    """
    if _is_url(filepath):
        rc, out, _ = _run_raw("cat", "-r", str(revision), filepath, timeout=60)
        return out if rc == 0 else None
    info = get_svn_info(filepath)
    target = info["url"] if info and info.get("url") else filepath
    rc, out, err = _run_raw("cat", "-r", str(revision), target, timeout=60)
    if rc == 0:
        return out
    if target != filepath:
        rc2, out2, _ = _run_raw("cat", "-r", str(revision), filepath, timeout=60)
        if rc2 == 0:
            return out2
    return None


def get_base_content_raw(filepath: str) -> Optional[bytes]:
    """Get the BASE version of a working copy file as raw bytes (for binary files like .xlsx)."""
    rc, out, err = _run_raw("cat", "-r", "BASE", filepath, timeout=60)
    if rc == 0:
        return out
    rc2, out2, err2 = _run_raw("cat", filepath, timeout=60)
    if rc2 == 0:
        return out2
    return None


def get_modified_files(working_dir: str, extensions: tuple = (".xml", ".xlsx", ".xls")) -> list:
    """Get list of locally modified files in the working directory."""
    rc, out, err = _run("status", "--xml", working_dir, timeout=60)
    if rc != 0:
        rc2, out2, err2 = _run("status", working_dir, timeout=60)
        if rc2 != 0:
            return []
        result = []
        for line in out2.splitlines():
            if not line or len(line) < 8:
                continue
            status_char = line[0]
            fpath = line[7:].strip()
            if status_char in ("M", "A", "D", "R", "C") and any(fpath.lower().endswith(e) for e in extensions):
                result.append({
                    "path": fpath,
                    "status": {"M": "modified", "A": "added", "D": "deleted", "R": "replaced", "C": "conflicted"}.get(status_char, status_char),
                    "name": os.path.basename(fpath),
                })
        return result

    try:
        root = ET.fromstring(out)
        result = []
        for entry in root.iter("entry"):
            path = entry.get("path", "")
            if not any(path.lower().endswith(e) for e in extensions):
                continue
            wc_status = entry.find("wc-status")
            if wc_status is None:
                continue
            item_status = wc_status.get("item", "")
            tree_conflicted = wc_status.get("tree-conflicted") == "true"
            if item_status == "conflicted" or tree_conflicted:
                effective_status = "conflicted"
            elif item_status in ("modified", "added", "deleted", "replaced"):
                effective_status = item_status
            else:
                continue
            result.append({
                "path": path,
                "status": effective_status,
                "name": os.path.basename(path),
                "revision": wc_status.get("revision", ""),
            })
        return result
    except ET.ParseError:
        return []


def get_changed_files_between_revisions(path: str, rev_old: int, rev_new: int,
                                        extensions: tuple = (".xml", ".xlsx", ".xls")) -> list:
    """Get list of changed files between two SVN revisions using 'svn diff --summarize'.
    Uses remote URL for reliability, falls back to local path.
    """
    info = get_svn_info(path)
    target = info["url"] if info and info.get("url") else path
    use_url = (target != path)

    rc, out, err = _run("diff", "--summarize", "-r",
                        f"{rev_old}:{rev_new}", target, timeout=120)
    if rc != 0:
        if use_url:
            rc, out, err = _run("diff", "--summarize", "-r",
                                f"{rev_old}:{rev_new}", path, timeout=120)
            if rc != 0:
                return []
            use_url = False
        else:
            return []

    result = []
    if use_url:
        base_url = target.rstrip("/")
        for line in out.splitlines():
            line = line.strip()
            if not line:
                continue
            status_char = line[0]
            fpath = line[1:].strip()
            if not fpath:
                continue
            if not any(fpath.lower().endswith(e) for e in extensions):
                continue
            if fpath.startswith(base_url + "/"):
                rel = fpath[len(base_url) + 1:]
            elif fpath.startswith(base_url):
                rel = fpath[len(base_url):]
            else:
                continue
            # SVN URLs percent-encode non-ASCII (e.g. Chinese) names; decode for
            # local paths and display, keep an encoded-safe URL for direct cat.
            rel_decoded = urllib.parse.unquote(rel)
            file_url = base_url + "/" + rel if rel else fpath
            result.append({
                "path": os.path.join(path, rel_decoded) if rel_decoded else fpath,
                "name": rel_decoded.replace("\\", "/") if rel_decoded else os.path.basename(fpath),
                "url": file_url,
                "status": {"M": "modified", "A": "added", "D": "deleted"}.get(status_char, "modified"),
            })
    else:
        norm_base = os.path.normcase(os.path.normpath(path))
        for line in out.splitlines():
            line = line.strip()
            if not line:
                continue
            status_char = line[0]
            fpath = line[1:].strip()
            if not fpath:
                continue
            if not any(fpath.lower().endswith(e) for e in extensions):
                continue
            norm_fp = os.path.normcase(os.path.normpath(fpath))
            if not norm_fp.startswith(norm_base + os.sep) and norm_fp != norm_base:
                continue
            result.append({
                "path": fpath,
                "name": os.path.basename(fpath),
                "status": {"M": "modified", "A": "added", "D": "deleted"}.get(status_char, "modified"),
            })
    return result


def get_dir_log(path: str, limit: int = 50) -> list:
    """Get SVN log for a specific directory only.
    Uses remote URL for latest history, falls back to local path.
    """
    info = get_svn_info(path)
    target = info["url"] if info and info.get("url") else path
    rc, out, err = _run("log", "-l", str(limit), "--xml",
                        "--stop-on-copy", target, timeout=120)
    if rc != 0 and target != path:
        rc, out, err = _run("log", "-l", str(limit), "--xml",
                            "--stop-on-copy", path, timeout=120)
    if rc != 0:
        return []
    try:
        root = ET.fromstring(out)
        entries = []
        for entry in root.iter("logentry"):
            rev = entry.get("revision", "")
            author = entry.findtext("author", "")
            date_str = entry.findtext("date", "")
            msg = (entry.findtext("msg", "") or "").strip()
            entries.append({
                "revision": int(rev) if rev.isdigit() else rev,
                "author": author,
                "date": date_str,
                "message": msg,
            })
        return entries
    except ET.ParseError:
        return []


def get_remote_head_revision(url: str) -> Optional[int]:
    """Get the latest revision number from remote repository."""
    rc, out, err = _run("info", "--xml", url, timeout=30)
    if rc != 0:
        return None
    try:
        root = ET.fromstring(out)
        commit_el = root.find(".//commit")
        if commit_el is not None:
            rev = commit_el.get("revision")
            return int(rev) if rev else None
        entry_el = root.find(".//entry")
        if entry_el is not None:
            rev = entry_el.get("revision")
            return int(rev) if rev else None
        return None
    except (ET.ParseError, AttributeError):
        return None


def get_remote_update_status(path: str, extensions: tuple = (".xml", ".xlsx", ".xls")) -> Optional[dict]:
    """Inspect per-file BASE revisions, including files skipped by prior updates."""
    rc, out, _err = _run("status", "--xml", "--show-updates", path, timeout=120)
    if rc != 0:
        return None
    try:
        root = ET.fromstring(out)
        against = root.find(".//against")
        remote_revision = int(against.get("revision")) if against is not None else None
        files = []
        revisions = []
        has_update = False
        for entry in root.iter("entry"):
            repos = entry.find("repos-status")
            if repos is None or (repos.get("item", "none") in ("none", "normal")
                                 and repos.get("props", "none") in ("none", "normal")):
                continue
            has_update = True
            wc = entry.find("wc-status")
            revision = wc.get("revision", "") if wc is not None else ""
            if revision.isdigit():
                revisions.append(int(revision))
            filename = entry.get("path", "")
            if filename.lower().endswith(extensions):
                files.append(os.path.relpath(filename, path).replace("\\", "/"))
        return {"files": files, "has_update": has_update, "remote_revision": remote_revision,
                "local_revision": min(revisions) if revisions else None}
    except (ET.ParseError, ValueError, TypeError):
        return None


def get_remote_changed_files(path: str, extensions: tuple = (".xml", ".xlsx", ".xls")) -> list:
    status = get_remote_update_status(path, extensions)
    return status["files"] if status else []


def get_conflicted_files(working_dir: str, strict: bool = False) -> list:
    """List paths currently in conflicted state (text or tree conflicts)."""
    rc, out, _err = _run("status", "--xml", working_dir, timeout=60)
    if rc != 0:
        if strict:
            raise RuntimeError(_err or "Cannot inspect SVN conflict state")
        return []
    try:
        root = ET.fromstring(out)
    except ET.ParseError:
        if strict:
            raise
        return []
    result = []
    for entry in root.iter("entry"):
        wc = entry.find("wc-status")
        if wc is None:
            continue
        if (wc.get("item") == "conflicted" or wc.get("props") == "conflicted"
                or wc.get("tree-conflicted") == "true"):
            result.append(entry.get("path", ""))
    return result


def get_conflict_info(filepath: str) -> Optional[dict]:
    """Inspect SVN conflict metadata for a working-copy file.

    For files currently in text-conflict state, SVN keeps three sidecar files:
      <name>.r<oldRev>   -- the BASE (common ancestor) at update time
      <name>.mine        -- the local edits before update
      <name>.r<newRev>   -- the version fetched from the repository
    `svn info --xml <file>` exposes the three paths under <conflict>. Returns
    ``None`` when the file is not in a text conflict state.
    """
    rc, out, _err = _run("info", "--xml", filepath, timeout=30)
    if rc != 0:
        return None
    try:
        root = ET.fromstring(out)
    except ET.ParseError:
        return None
    entry = root.find(".//entry")
    if entry is None:
        return None
    conflict = entry.find("conflict")
    if conflict is None:
        return None
    if (conflict.get("type") or "").lower() != "text":
        return None

    base_file = _resolve_conflict_sidecar(filepath, (conflict.findtext("prev-base-file") or "").strip())
    mine_file = _resolve_conflict_sidecar(filepath, (conflict.findtext("prev-wc-file") or "").strip())
    theirs_file = _resolve_conflict_sidecar(filepath, (conflict.findtext("cur-base-file") or "").strip())
    if not (base_file and mine_file and theirs_file):
        return None

    base_rev = None
    theirs_rev = None
    for ver in conflict.findall("version"):
        side = ver.get("side")
        rev = ver.get("revision")
        try:
            rev_int = int(rev) if rev else None
        except ValueError:
            rev_int = None
        if side == "source-left":
            base_rev = rev_int
        elif side == "source-right":
            theirs_rev = rev_int

    return {
        "is_text_conflict": True,
        "base_file": base_file,
        "mine_file": mine_file,
        "theirs_file": theirs_file,
        "base_rev": base_rev,
        "theirs_rev": theirs_rev,
    }


def _resolve_conflict_sidecar(filepath: str, sidecar: str) -> str:
    """Resolve SVN conflict sidecar paths relative to the conflicted file.

    SVN may report sidecars as bare relative names such as ``items.xml.mine``.
    SheetMeld's process cwd is the app directory, not necessarily the file's
    folder, so resolve relative names beside the conflicted working-copy file.
    """
    if not sidecar or os.path.isabs(sidecar):
        return sidecar
    file_dir = os.path.dirname(os.path.abspath(filepath))
    beside_file = os.path.normpath(os.path.join(file_dir, sidecar))
    if os.path.exists(beside_file):
        return beside_file
    cwd_path = os.path.abspath(sidecar)
    if os.path.exists(cwd_path):
        return cwd_path
    return beside_file


def _semantic_file_entry(item) -> tuple:
    """Return (workspace-relative file, target revision) for semantic update."""
    if isinstance(item, dict):
        name = item.get("file") or item.get("name") or item.get("path") or ""
        rev = item.get("theirs_revision") or item.get("revision")
    else:
        name = str(item or "")
        rev = None
    try:
        rev_int = int(rev) if rev not in (None, "", "HEAD") else None
    except (TypeError, ValueError):
        rev_int = None
    return name, rev_int


# Update output item lines look like "U    path" / "A    path" / "UU   path".
# Excludes locale-independent noise such as "Updating '.':" / "At revision N.".
_UPDATE_ITEM_RE = re.compile(r"^[ADUCGER][ ADUCGEB]?\s{2,}\S")


def _update_path(workspace: str, name: str) -> str:
    """Validate update targets before any SVN command can modify files."""
    root = os.path.normcase(os.path.realpath(workspace))
    if not isinstance(name, str) or not name or os.path.isabs(name):
        raise ValueError("Invalid workspace-relative update path")
    target = os.path.realpath(os.path.join(workspace, name))
    if os.path.commonpath([root, os.path.normcase(target)]) != root or os.path.normcase(target) == root:
        raise ValueError("Update path escapes workspace: " + name)
    return target


def _write_bytes_atomic(path: str, content: bytes):
    import tempfile
    fd, temporary = tempfile.mkstemp(dir=os.path.dirname(path), suffix=".sheetmeld.tmp")
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(content)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.remove(temporary)


def _checked_run(*args, **kwargs) -> str:
    rc, out, err = _run(*args, **kwargs)
    if rc != 0:
        raise RuntimeError((err or out or "svn command failed").strip())
    return out


def _resolve_clean_text(path: str):
    """Only resolve known text conflicts, after clean bytes have been restored."""
    if not get_conflicted_files(path, strict=True):
        return
    if not get_conflict_info(path):
        raise RuntimeError("Tree/property conflict requires manual resolution: " + path)
    _checked_run("resolve", "--accept", "working", path)
    if get_conflicted_files(path, strict=True):
        raise RuntimeError("SVN conflict remains unresolved: " + path)


def _update_excluding(path: str, revision: int, excluded: set):
    """Update siblings while keeping excluded files and their BASE untouched.

    Only ancestors of excluded targets need shallow updates. Read both local
    and remote children so additions and deletions elsewhere still get applied.
    """
    key = os.path.normcase(os.path.realpath(path))
    if key in excluded:
        return
    if not any(p.startswith(key + os.sep) for p in excluded):
        yield _checked_run("update", "-r", str(revision), "--accept", "postpone",
                           *(["--ignore-externals"] if excluded else []), path, timeout=300)
        return

    info = get_svn_info(path)
    if not info or not info.get("url"):
        raise RuntimeError("Cannot inspect update directory: " + path)
    local = ET.fromstring(_checked_run("info", "--xml", "--depth", "immediates", path))
    local_names = {os.path.basename(e.get("path", "")) for e in local.findall("entry")
                   if os.path.normcase(os.path.abspath(e.get("path", ""))) != key}
    remote = ET.fromstring(_checked_run("list", "--xml", "-r", str(revision),
                                       info["url"], timeout=60))
    remote_names = {e.findtext("name") for e in remote.findall(".//entry")}
    yield _checked_run("update", "-r", str(revision), "--depth", "empty",
                       "--accept", "postpone", "--ignore-externals", path, timeout=300)
    for name in sorted((local_names | remote_names) - {None, ""}):
        child = os.path.join(path, name)
        child_key = os.path.normcase(os.path.realpath(child))
        if child_key not in excluded and name not in remote_names and any(
                p.startswith(child_key + os.sep) for p in excluded):
            raise RuntimeError("Remote directory deletion contains a protected file: " + child)
        yield from _update_excluding(child, revision, excluded)


def smart_update(path: str, skip_files: list, theirs_files: list,
                 mine_files: list, semantic_files: list = None) -> dict:
    """Apply explicit file policies and update only unprotected paths.

    Semantic results and mine-full choices survive SVN's textual merge byte
    for byte. Skipped files are never update targets, including their BASE.
    All repository reads use a fixed revision; semantic files keep the revision
    that was actually reviewed, even if HEAD has advanced in the meantime.
    """
    import tempfile
    results = {"updated": 0, "skipped": [], "theirs": [],
               "mine": [], "semantic": [], "errors": []}
    output = []
    plans = []
    excluded = set()
    try:
        if not path or not os.path.isdir(path):
            raise ValueError("Workspace directory is not configured")
        for policy, items in (("skipped", skip_files), ("theirs", theirs_files),
                              ("mine", mine_files), ("semantic", semantic_files or [])):
            for item in items:
                name, revision = _semantic_file_entry(item) if policy == "semantic" else (item, None)
                if policy == "semantic" and (revision is None or revision < 1):
                    raise ValueError(str(name) + ": missing semantic merge target revision; please preview again")
                target = _update_path(path, name)
                key = os.path.normcase(target)
                if any(key == other or key.startswith(other + os.sep)
                       or other.startswith(key + os.sep) for other in excluded):
                    raise ValueError("Conflicting update choices for: " + str(name))
                excluded.add(key)
                plans.append((policy, name, target, revision))

        info = get_svn_info(path)
        remote = get_svn_info(info["url"]) if info and info.get("url") else None
        if not remote or not str(remote.get("revision", "")).isdigit():
            raise RuntimeError("Cannot get remote revision for update")
        head = int(remote["revision"])

        for policy, name, target, revision in plans:
            if policy == "skipped":
                results[policy].append(name)
                continue
            revision = revision or head
            conflict = get_conflict_info(target)
            source = conflict["mine_file"] if conflict and policy != "semantic" else target
            original = None
            if os.path.isfile(source):
                with open(source, "rb") as stream:
                    original = stream.read()
            if policy in ("semantic", "mine") and original is None:
                raise RuntimeError("Cannot preserve missing working file: " + name)
            if policy == "semantic":
                ET.fromstring(original)  # Never preserve already-poisoned merge results.

            # Keep a recovery copy until both update and restore/resolve succeed.
            backup = None
            if original is not None:
                fd, backup = tempfile.mkstemp(prefix="sheetmeld-update-", suffix=".bak")
                with os.fdopen(fd, "wb") as stream:
                    stream.write(original)
            try:
                if policy == "theirs":
                    # resolve --accept theirs-full is a no-op after an automatic
                    # merge. Revert first, then update to the chosen revision.
                    _checked_run("revert", "--depth", "empty", target)
                    output.append(_checked_run("update", "-r", str(revision), "--accept",
                                               "postpone", "--ignore-externals", target, timeout=60))
                    if get_conflicted_files(target, strict=True):
                        raise RuntimeError("SVN conflict remains unresolved: " + name)
                else:
                    _write_bytes_atomic(target, original)
                    _resolve_clean_text(target)
                    try:
                        output.append(_checked_run("update", "-r", str(revision), "--accept",
                                                   "postpone", "--ignore-externals", target, timeout=60))
                    finally:
                        # --accept working preserves the *post-merge* file,
                        # which can contain conflict markers. Restore our copy.
                        _write_bytes_atomic(target, original)
                    _resolve_clean_text(target)
                if policy != "theirs" or os.path.exists(target):
                    updated_info = get_svn_info(target)
                    if not updated_info or str(updated_info.get("revision")) != str(revision):
                        raise RuntimeError("SVN did not advance file BASE to r" + str(revision))
                results[policy].append(name)
            except Exception as exc:
                if original is not None:
                    try:
                        _write_bytes_atomic(target, original)
                    except OSError:
                        pass
                if backup:
                    results.setdefault("backups", []).append(backup)
                raise RuntimeError(name + ": " + str(exc)) from exc
            else:
                if backup:
                    os.remove(backup)

        output.extend(_update_excluding(path, head, excluded))
        remaining = [p for p in get_conflicted_files(path, strict=True)
                     if os.path.normcase(os.path.realpath(p)) not in excluded]
        if remaining:
            results["errors"].append("SVN conflicts require resolution: " + ", ".join(remaining))
    except (OSError, ValueError, RuntimeError, ET.ParseError) as exc:
        results["errors"].append(str(exc))
    results["updated"] = sum(1 for out in output for line in out.splitlines()
                             if _UPDATE_ITEM_RE.match(line))
    return results


def get_version() -> Optional[str]:
    """Get SVN client version string."""
    rc, out, err = _run("--version", "--quiet")
    if rc == 0:
        return out.strip()
    return None

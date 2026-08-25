"""
In-app auto-update for SheetMeld.

Checks GitHub Releases for a newer version, downloads the SheetMeld.exe
asset and self-replaces the running executable (PyInstaller frozen mode).

Network access uses only the standard library (urllib). If a direct
connection to GitHub fails, requests are retried through the acceleration
proxy (PROXY_PREFIX + original URL). The working channel is remembered for
the rest of the session so downloads reuse it.
"""
import os
import re
import sys
import json
import time
import threading
import subprocess
import urllib.request
import urllib.error

GITHUB_REPO = "noahsarkcc/sheetmeld"
PROXY_PREFIX = "https://github.2436666.xyz/"
LATEST_API_URL = "https://api.github.com/repos/%s/releases/latest" % GITHUB_REPO
RELEASES_PAGE_URL = "https://github.com/%s/releases" % GITHUB_REPO
ASSET_NAME = "SheetMeld.exe"
USER_AGENT = "SheetMeld-Updater"
FETCH_TIMEOUT = 8
DOWNLOAD_TIMEOUT = 30
CHUNK_SIZE = 64 * 1024

# Set to True once a direct request fails and the proxy works, so later
# requests (notably the download) go straight through the proxy.
_use_proxy = False


def is_frozen() -> bool:
    """True when running as a PyInstaller-built executable."""
    return bool(getattr(sys, "frozen", False))


def parse_version(tag) -> tuple:
    """'v1.3.7' / '1.4.0' -> (1, 3, 7) / (1, 4, 0). Non-numeric parts -> 0."""
    if not tag:
        return (0,)
    parts = str(tag).strip().lstrip("vV").split(".")
    nums = []
    for p in parts:
        m = re.match(r"\d+", p.strip())
        nums.append(int(m.group()) if m else 0)
    return tuple(nums) if nums else (0,)


def is_newer(latest_tag, current_version) -> bool:
    """True if latest_tag represents a strictly newer version."""
    a = parse_version(latest_tag)
    b = parse_version(current_version)
    # Pad to equal length so (1, 4) == (1, 4, 0)
    n = max(len(a), len(b))
    a += (0,) * (n - len(a))
    b += (0,) * (n - len(b))
    return a > b


def proxied(url: str) -> str:
    """Return the proxy-prefixed form of a URL."""
    return PROXY_PREFIX + url


def _open(url: str, timeout: int):
    req = urllib.request.Request(url, headers={
        "User-Agent": USER_AGENT,
        "Accept": "application/vnd.github+json",
    })
    return urllib.request.urlopen(req, timeout=timeout)


def _fetch(url: str, timeout: int = FETCH_TIMEOUT) -> bytes:
    """GET a URL, falling back to the proxy when the direct request fails."""
    global _use_proxy
    if not _use_proxy:
        try:
            with _open(url, timeout) as resp:
                return resp.read()
        except Exception:
            pass
    data = None
    with _open(proxied(url), timeout) as resp:
        data = resp.read()
    _use_proxy = True
    return data


def check_update(current_version: str) -> dict:
    """Query the latest release and compare with current_version.

    Returns a dict:
      has_update, current, latest, notes, html_url, asset_url, asset_size,
      is_frozen, proxy_used
    Raises on network/parse failure (caller turns it into an API error).
    """
    raw = _fetch(LATEST_API_URL)
    info = json.loads(raw.decode("utf-8"))
    latest_tag = info.get("tag_name") or ""
    asset_url = None
    asset_size = 0
    for asset in info.get("assets") or []:
        if asset.get("name") == ASSET_NAME:
            asset_url = asset.get("browser_download_url")
            asset_size = asset.get("size") or 0
            break
    return {
        "has_update": is_newer(latest_tag, current_version),
        "current": current_version,
        "latest": latest_tag.lstrip("vV"),
        "notes": info.get("body") or "",
        "html_url": info.get("html_url") or RELEASES_PAGE_URL,
        "proxy_page_url": proxied(info.get("html_url") or RELEASES_PAGE_URL),
        "asset_url": asset_url,
        "asset_size": asset_size,
        "is_frozen": is_frozen(),
        "proxy_used": _use_proxy,
    }


# ---------------------------------------------------------------------------
# Download state machine (module-level singleton)
# ---------------------------------------------------------------------------

_dl_lock = threading.Lock()
_dl_state = {
    "status": "idle",       # idle | downloading | ready | error
    "percent": 0,
    "downloaded": 0,
    "total": 0,
    "error": None,
    "path": None,            # path of the downloaded .new file when ready
}


def get_progress() -> dict:
    with _dl_lock:
        return dict(_dl_state)


def _set_progress(**kw):
    with _dl_lock:
        _dl_state.update(kw)


def _download_worker(asset_url: str, dest: str):
    part = dest + ".part"
    try:
        url = proxied(asset_url) if _use_proxy else asset_url
        try:
            resp = _open(url, DOWNLOAD_TIMEOUT)
        except Exception:
            if _use_proxy:
                raise
            resp = _open(proxied(asset_url), DOWNLOAD_TIMEOUT)
        with resp:
            total = int(resp.headers.get("Content-Length") or 0)
            _set_progress(total=total)
            downloaded = 0
            with open(part, "wb") as f:
                while True:
                    chunk = resp.read(CHUNK_SIZE)
                    if not chunk:
                        break
                    f.write(chunk)
                    downloaded += len(chunk)
                    pct = int(downloaded * 100 / total) if total else 0
                    _set_progress(downloaded=downloaded, percent=pct)
        os.replace(part, dest)
        _set_progress(status="ready", percent=100, path=dest)
    except Exception as e:
        try:
            if os.path.isfile(part):
                os.remove(part)
        except OSError:
            pass
        _set_progress(status="error", error=str(e))


def start_download(asset_url: str, dest_dir: str = None) -> dict:
    """Start the background download thread. Returns the initial state.

    No-op (returns current state) if a download is already running or done.
    """
    if not is_frozen():
        return {"status": "error", "error": "source mode: update via git pull",
                "percent": 0, "downloaded": 0, "total": 0, "path": None}
    with _dl_lock:
        if _dl_state["status"] in ("downloading", "ready"):
            return dict(_dl_state)
        _dl_state.update(status="downloading", percent=0, downloaded=0,
                         total=0, error=None, path=None)
    if dest_dir is None:
        dest_dir = os.path.dirname(sys.executable)
    dest = os.path.join(dest_dir, ASSET_NAME + ".new")
    t = threading.Thread(target=_download_worker, args=(asset_url, dest), daemon=True)
    t.start()
    return get_progress()


# ---------------------------------------------------------------------------
# Self-replace and restart (frozen mode only)
# ---------------------------------------------------------------------------

_UPDATE_BAT = r"""@echo off
rem SheetMeld self-update helper (auto-generated, self-deleting)
rem NOTE: the 1s delay uses ping because timeout.exe exits immediately
rem ("input redirection is not supported") when launched with stdin
rem redirected to NUL, which is how apply_update() starts this script.
set TRIES=0
:wait
ping -n 2 127.0.0.1 >nul
del "{exe}" >nul 2>&1
if not exist "{exe}" goto swap
set /a TRIES+=1
if %TRIES% LSS {tries_max} goto wait
rem Give up: old exe still locked after {tries_max}s, just relaunch it.
start "" /d "{cwd}" "{exe}"
goto done
:swap
move /y "{new}" "{exe}" >nul
start "" /d "{cwd}" "{exe}"
:done
del "%~f0"
"""


def apply_update(exit_delay: float = 1.5) -> dict:
    """Write the swap script, launch it detached, then exit the process.

    Returns {ok: True} (the HTTP response is sent before the delayed exit)
    or {ok: False, error: ...} when not applicable.
    """
    if not is_frozen():
        return {"ok": False, "error": "source mode: update via git pull"}
    state = get_progress()
    if state["status"] != "ready" or not state["path"] or not os.path.isfile(state["path"]):
        return {"ok": False, "error": "no downloaded update available"}

    exe = sys.executable
    exe_dir = os.path.dirname(exe)
    bat_path = os.path.join(exe_dir, "sheetmeld_update.bat")
    script = _UPDATE_BAT.format(exe=exe, new=state["path"], cwd=exe_dir, tries_max=60)
    with open(bat_path, "w", encoding="gbk", errors="replace") as f:
        f.write(script)

    # Strip PyInstaller bootloader variables from the helper's environment.
    # The bat (and the exe it relaunches) would otherwise inherit them, and
    # the new bootloader would mistake itself for the already-extracted child
    # stage, point at the dying process's _MEIxxxx temp dir and crash on
    # startup -- the update would swap the exe but never restart it.
    env = {k: v for k, v in os.environ.items()
           if not k.startswith("_PYI_") and k not in ("_MEIPASS2", "_MEIPASS")}

    DETACHED_PROCESS = 0x00000008
    CREATE_NEW_PROCESS_GROUP = 0x00000200
    CREATE_NO_WINDOW = 0x08000000
    subprocess.Popen(
        ["cmd", "/c", bat_path],
        cwd=exe_dir,
        env=env,
        creationflags=DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW,
        close_fds=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    def _die():
        time.sleep(exit_delay)
        os._exit(0)

    threading.Thread(target=_die, daemon=True).start()
    return {"ok": True}

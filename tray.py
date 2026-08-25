"""
System tray integration for SheetMeld.

Hides the console window and provides a tray icon with a menu:
- Open browser
- Show log file
- Open workspace directory
- Quit

`pystray` runs its event loop on the calling (main) thread, so server.py
starts Flask in a background thread and hands control here. When `pystray`
or `Pillow` are missing, `start_tray()` returns False so the caller can
fall back to the legacy console mode.
"""
import os
import re
import sys
import webbrowser

try:
    import pystray
    from pystray import MenuItem as Item, Menu
    from PIL import Image, ImageDraw
    AVAILABLE = True
except Exception:
    AVAILABLE = False


_RECT_RE = re.compile(
    r'<rect\s+x="(\d+)"\s+y="(\d+)"\s+width="(\d+)"\s+height="(\d+)"\s+fill="(#[0-9a-fA-F]+)"',
)


def _hex_to_rgba(value: str) -> tuple:
    """Parse #rgb / #rrggbb into an opaque RGBA tuple."""
    h = value.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
    return (r, g, b, 255)


def _render_miku_icon(svg_path: str, size: int = 64, face_max_y: int = 8):
    """Render the tray icon from the pixel-art Miku SVG.

    The SVG is a flat list of 1px-tall `<rect>` blocks on a 16x16 grid. We keep
    only the head/face region (rows y <= face_max_y), crop to its bounding box,
    and draw it scaled with hard edges onto a transparent square canvas. Returns
    None on any failure so the caller can fall back to the placeholder icon.
    """
    try:
        if not svg_path or not os.path.isfile(svg_path):
            return None
        with open(svg_path, "r", encoding="utf-8") as fh:
            svg = fh.read()
        rects = []
        for m in _RECT_RE.finditer(svg):
            x, y, w, h, fill = m.groups()
            x, y, w, h = int(x), int(y), int(w), int(h)
            if y > face_max_y:
                continue
            rects.append((x, y, w, h, _hex_to_rgba(fill)))
        if not rects:
            return None

        min_x = min(r[0] for r in rects)
        min_y = min(r[1] for r in rects)
        max_x = max(r[0] + r[2] for r in rects)
        max_y = max(r[1] + r[3] for r in rects)
        grid_w = max_x - min_x
        grid_h = max_y - min_y
        if grid_w <= 0 or grid_h <= 0:
            return None

        # Scale to *cover* the square canvas (not just fit) so the face fills the
        # icon; the shorter grid dimension drives the scale and the wider sides
        # (twin-tail tips) overflow and get clipped. Centered on the canvas.
        scale = max(1, -(-size // min(grid_w, grid_h)))  # ceil(size / min)
        content_w = grid_w * scale
        content_h = grid_h * scale
        off_x = (size - content_w) // 2
        off_y = (size - content_h) // 2

        img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        for x, y, w, h, color in rects:
            x0 = off_x + (x - min_x) * scale
            y0 = off_y + (y - min_y) * scale
            draw.rectangle([x0, y0, x0 + w * scale - 1, y0 + h * scale - 1], fill=color)
        return img
    except Exception:
        return None


def _make_icon_image(size: int = 64) -> "Image.Image":
    """Generate a simple SheetMeld icon (rounded square with diff bars)."""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    pad = max(2, size // 16)
    radius = max(4, size // 8)
    draw.rounded_rectangle(
        [pad, pad, size - pad, size - pad],
        radius=radius,
        fill=(35, 134, 54, 255),       # GitHub green
        outline=(255, 255, 255, 255),
        width=max(1, size // 32),
    )
    # Two thin diff bars (one short, one long)
    bar_h = max(2, size // 14)
    left = size // 4
    right = size - size // 4
    mid_y = size // 2
    draw.rectangle(
        [left, mid_y - bar_h * 2, left + (right - left) // 2, mid_y - bar_h],
        fill=(255, 255, 255, 230),
    )
    draw.rectangle(
        [left, mid_y + bar_h, right, mid_y + bar_h * 2],
        fill=(255, 255, 255, 230),
    )
    return img


def start_tray(port: int, log_path: str, workspace_resolver, shutdown_fn,
               app_name: str = "SheetMeld", icon_path: str = "") -> bool:
    """Launch the tray icon. Blocks until the user quits.

    - port: Flask server port (for the "Open browser" menu item).
    - log_path: absolute path to the server log file.
    - workspace_resolver: callable returning the current workspace directory,
      or empty string when none is configured.
    - shutdown_fn: callable invoked when user picks "Quit".
    - icon_path: absolute path to the Miku pixel-art SVG; falls back to the
      built-in placeholder when missing or unparseable.
    """
    if not AVAILABLE:
        return False

    def _open_browser(_icon=None, _item=None):
        try:
            webbrowser.open(f"http://localhost:{port}")
        except Exception:
            pass

    def _open_log(_icon=None, _item=None):
        # Prefer the in-app /log viewer: newest-first, auto-refresh, dark.
        # Fall back to the raw file (Notepad / xdg-open) only when the
        # browser cannot be launched.
        try:
            webbrowser.open(f"http://localhost:{port}/log")
            return
        except Exception:
            pass
        try:
            if log_path and os.path.isfile(log_path):
                if os.name == "nt":
                    os.startfile(log_path)
                else:
                    import subprocess
                    subprocess.Popen(["xdg-open", log_path])
        except Exception:
            pass

    def _open_workspace(_icon=None, _item=None):
        try:
            wd = workspace_resolver() if callable(workspace_resolver) else ""
            if wd and os.path.isdir(wd):
                if os.name == "nt":
                    os.startfile(os.path.normpath(wd))
                else:
                    import subprocess
                    subprocess.Popen(["xdg-open", wd])
        except Exception:
            pass

    def _quit(icon, _item=None):
        try:
            if callable(shutdown_fn):
                shutdown_fn()
        finally:
            try:
                icon.stop()
            except Exception:
                pass
            # Hard-exit because Flask's dev server has no clean shutdown when
            # bound to 127.0.0.1 without the Werkzeug shutdown hook.
            os._exit(0)

    menu = Menu(
        Item("打开浏览器 / Open browser", _open_browser, default=True),
        Item("显示日志 / Show log", _open_log),
        Item("打开工作目录 / Open workspace", _open_workspace),
        Menu.SEPARATOR,
        Item("退出 / Quit", _quit),
    )

    icon_image = _render_miku_icon(icon_path, 64) or _make_icon_image(64)
    icon = pystray.Icon(app_name, icon_image, app_name, menu)
    icon.run()
    return True

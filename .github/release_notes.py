"""Generate the GitHub release body from CHANGELOG.md (English only).

Usage:
  python .github/release_notes.py <tag> [prev_tag] > release_notes.md
  python .github/release_notes.py --title <tag>     # "vX.Y.Z - <summary>"

- Extracts the "## vX.Y.Z" section of CHANGELOG.md for <tag> and turns it
  into compact release highlights instead of dumping the full changelog.
- Versions between prev_tag (exclusive) and <tag> that never got their own
  release are summarized as one "Also includes vX.Y.Z: <intro line>" each.
- Always ends with a "See CHANGELOG for details." link, matching the older
  release pages.
"""
import re
import sys

REPO = "noahsarkcc/sheetmeld"
CHANGELOG = "CHANGELOG.md"

# Bold subsection titles that are developer/infra detail, not user-facing.
BLACKLIST = re.compile(r"tests?|api|infrastructure|internal|\bci\b", re.I)
MAX_RELEASE_BULLETS = 5
MAX_BULLETS_PER_SECTION = 2
MAX_BULLET_CHARS = 480

SECTION_RE = re.compile(r"^## v(\d+(?:\.\d+)*)([^\n]*)\n(.*?)(?=^## v|\Z)",
                        re.M | re.S)
BOLD_TITLE_RE = re.compile(r"^\*\*(.+?)\*\*\s*$")


def parse_version(s) -> tuple:
    nums = []
    for p in str(s).strip().lstrip("vV").split("."):
        m = re.match(r"\d+", p.strip())
        nums.append(int(m.group()) if m else 0)
    return tuple(nums) if nums else (0,)


def load_sections():
    """Return [(version_tuple, heading_line, body)] in file order (newest first)."""
    try:
        with open(CHANGELOG, encoding="utf-8") as f:
            text = f.read()
    except OSError:
        return []
    out = []
    for m in SECTION_RE.finditer(text):
        heading = f"## v{m.group(1)}{m.group(2)}".rstrip()
        out.append((parse_version(m.group(1)), heading, m.group(3)))
    return out


def filter_technical(body: str) -> str:
    """Drop blacklisted bold-titled subsections from a section body."""
    lines = []
    skipping = False
    for line in body.splitlines():
        m = BOLD_TITLE_RE.match(line.strip())
        if m:
            skipping = bool(BLACKLIST.search(m.group(1)))
        if not skipping:
            lines.append(line.rstrip())
    text = "\n".join(lines)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def compact_bullet(line: str) -> str:
    """Keep one readable sentence per changelog bullet for release pages."""
    text = line.strip()[2:].strip()
    first_sentence = re.split(r"(?<=[.!?])\s+", text, maxsplit=1)[0].strip()
    if len(first_sentence) > MAX_BULLET_CHARS:
        first_sentence = first_sentence[:MAX_BULLET_CHARS].rsplit(" ", 1)[0].rstrip() + "..."
    return f"- {first_sentence}"


def compact_release_body(body: str) -> str:
    """Build a short, GitHub-release-friendly body from a changelog section."""
    intro = intro_line(body)
    bullets = []
    skipping = False
    section_count = 0

    for line in body.splitlines():
        stripped = line.strip()
        title = BOLD_TITLE_RE.match(stripped)
        if title:
            skipping = bool(BLACKLIST.search(title.group(1)))
            section_count = 0
            continue
        if skipping or not stripped.startswith("- "):
            continue
        if section_count >= MAX_BULLETS_PER_SECTION:
            section_count += 1
            continue
        bullets.append(compact_bullet(stripped))
        section_count += 1
        if len(bullets) >= MAX_RELEASE_BULLETS:
            break

    parts = [intro] if intro else []
    if bullets:
        parts.append("**Highlights**\n" + "\n".join(bullets))
    return "\n\n".join(parts).strip() or filter_technical(body)


def intro_line(body: str) -> str:
    """First plain text line of a section body (the one-liner summary)."""
    for line in body.splitlines():
        s = line.strip()
        if s and not BOLD_TITLE_RE.match(s) and not s.startswith("-"):
            return s
    return ""


def main():
    args = sys.argv[1:]
    title_mode = "--title" in args
    if title_mode:
        args.remove("--title")
    if not args:
        print("usage: release_notes.py [--title] <tag> [prev_tag]", file=sys.stderr)
        return 1
    cur = parse_version(args[0])
    prev = parse_version(args[1]) if len(args) > 1 and args[1] else None

    sections = load_sections()

    if title_mode:
        cur_tag = "v" + ".".join(str(n) for n in cur)
        current = next((s for s in sections if s[0] == cur), None)
        summary = intro_line(current[2]).rstrip(".").strip() if current else ""
        title = f"{cur_tag} - {summary}" if summary else cur_tag
        sys.stdout.buffer.write((title + "\n").encode("utf-8"))
        return 0
    parts = []

    current = next((s for s in sections if s[0] == cur), None)
    if current:
        parts.append(f"{current[1]}\n\n{compact_release_body(current[2])}")

    if prev is not None:
        between = [s for s in sections if prev < s[0] < cur]
        for ver, _heading, body in sorted(between, reverse=True):
            tag = "v" + ".".join(str(n) for n in ver)
            summary = intro_line(body)
            parts.append(f"Also includes **{tag}**: {summary}" if summary
                         else f"Also includes **{tag}** (see CHANGELOG).")

    parts.append(f"See [CHANGELOG](https://github.com/{REPO}/blob/main/CHANGELOG.md) "
                 f"for details.")

    sys.stdout.buffer.write(("\n\n".join(parts) + "\n").encode("utf-8"))
    return 0


if __name__ == "__main__":
    sys.exit(main())

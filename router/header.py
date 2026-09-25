"""Source path header for managed copies.

When enabled, imported files get a gray comment line as their first line:

    %% obsidian-intake source: /original/absolute/path %%

In Obsidian, %%text%% renders as a (gray) comment in live preview.
"""

import re
from pathlib import Path

MARK = "obsidian-intake source:"
HEADER_RE = re.compile(r"^%%\s*obsidian-intake source:\s*(.*?)\s*%%\s*$")


def header_line(source_path):
    return f"%% {MARK} {source_path} %%"


def read_header(path):
    """Return the source path if the file starts with a header, else None."""
    try:
        first = Path(path).read_text(encoding="utf-8").split("\n", 1)[0]
        m = HEADER_RE.match(first)
        return m.group(1) if m else None
    except (OSError, UnicodeDecodeError):
        return None


def add_header(path, source_path):
    """Prepend the header line unless one already exists."""
    p = Path(path)
    try:
        text = p.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return False
    if HEADER_RE.match(text.split("\n", 1)[0]):
        return False
    p.write_text(header_line(source_path) + "\n" + text, encoding="utf-8")
    return True


def remove_header(path):
    """Drop the header line if present. Returns (removed, source_path)."""
    p = Path(path)
    try:
        text = p.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return False, None
    first, _, rest = text.partition("\n")
    m = HEADER_RE.match(first)
    if not m:
        return False, None
    p.write_text(rest, encoding="utf-8")
    return True, m.group(1)


def body_sha256(path):
    """SHA-256 of the file content WITHOUT the source-path header line.

    Returns None if the file cannot be read.
    """
    import hashlib

    try:
        data = Path(path).read_bytes()
    except OSError:
        return None
    prefix = ("%% " + MARK).encode("utf-8")
    if data.startswith(prefix):
        nl = data.find(b"\n")
        if nl != -1:
            data = data[nl + 1:]
    return hashlib.sha256(data).hexdigest()

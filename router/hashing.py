"""SHA-256 based file identity engine."""

import hashlib

CHUNK = 65536


def sha256(path):
    """Return hex digest of file content. Raises on unreadable files."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            block = f.read(CHUNK)
            if not block:
                break
            h.update(block)
    return h.hexdigest()


def safe_sha256(path):
    """sha256 that returns None instead of raising."""
    try:
        return sha256(path)
    except OSError:
        return None

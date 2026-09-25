"""Sync engine: one-way synchronization between external file and vault copy.

Direction rules:
- external changed, vault untouched  -> pull external into vault (auto)
- vault changed, external untouched  -> keep vault copy as authoritative
- both changed                       -> conflict (user decides)
"""

import shutil
from pathlib import Path

import database as db
import header as headermod
from hashing import safe_sha256

VAULT_CHANGED = "vault_changed"
EXTERNAL_CHANGED = "external_changed"
BOTH_CHANGED = "both_changed"
IN_SYNC = "in_sync"


def compare(rec, vault_hash, external_hash):
    """Classify change state of a managed record."""
    vault_known = rec["current_hash"] or rec["original_hash"]
    external_known = rec["external_hash"] or rec["original_hash"]

    vault_changed = vault_known is not None and vault_hash != vault_known
    external_changed = external_known is not None and external_hash != external_known

    if vault_changed and external_changed:
        return BOTH_CHANGED
    if vault_changed:
        return VAULT_CHANGED
    if external_changed:
        return EXTERNAL_CHANGED
    return IN_SYNC


def pull_external_to_vault(conn, rec, external_path, vault_path):
    """Copy external file over the vault copy (external is authoritative).

    vault_path: absolute path of the vault copy.
    A previously present source-path header is preserved.
    """
    vault_path = Path(vault_path)
    vault_path.parent.mkdir(parents=True, exist_ok=True)

    import header as headermod

    prior_header = headermod.read_header(vault_path)

    shutil.copy2(external_path, vault_path)
    if prior_header:
        headermod.add_header(vault_path, prior_header)

    new_hash = headermod.body_sha256(vault_path)
    db.update_hash(conn, rec["id"], new_hash)
    db.update_external_hash(conn, rec["id"], new_hash)
    db.add_event(conn, rec["id"], "synced-from-external",
                 rec["current_hash"], new_hash)
    return new_hash


def push_vault_to_external(conn, rec, external_path, vault_path):
    """Copy vault copy over the external file (vault is authoritative).

    vault_path: absolute path of the vault copy.
    The source-path header (if any) is stripped from the external copy.
    """
    vault_path = Path(vault_path)
    external_path = Path(external_path)

    import header as headermod

    had_header, _src = headermod.remove_header(vault_path)
    if had_header:
        external_path.write_text(
            vault_path.read_text(encoding="utf-8"), encoding="utf-8"
        )
        headermod.add_header(vault_path, _src)
    else:
        shutil.copy2(vault_path, external_path)

    vault_hash = headermod.body_sha256(vault_path)
    db.update_external_hash(conn, rec["id"], vault_hash)
    db.add_event(conn, rec["id"], "synced-to-external",
                 rec["external_hash"], vault_hash)
    return vault_hash

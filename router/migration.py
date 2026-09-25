"""Migration engine: permanent ownership transfer and conflict resolution."""

from pathlib import Path

import database as db
import header as headermod
from database import STATUS_CONFLICT, STATUS_MANAGED, STATUS_PERMANENT
from hashing import safe_sha256


class MigrationError(Exception):
    pass


def _vault_file(vault_root, rel):
    """Resolve a vault-relative DB path to an absolute path."""
    p = Path(rel)
    if p.is_absolute():
        return p
    return Path(vault_root) / p


def move_permanent(conn, file_id, vault_root):
    """Verify vault copy, update DB, delete external source, mark permanent.

    vault_root: absolute path of the vault (to resolve relative vault_path).
    """
    rec = db.get(conn, file_id)
    if rec is None:
        raise MigrationError("file id not found: %s" % file_id)
    if rec["status"] == STATUS_PERMANENT:
        raise MigrationError("file is already permanent")

    vault_path = _vault_file(vault_root, rec["vault_path"])
    if not vault_path.exists():
        raise MigrationError("vault copy is missing: %s" % vault_path)

    vault_hash = headermod.body_sha256(vault_path)
    external_path = Path(rec["original_path"]) if rec["original_path"] else None

    if external_path is not None and external_path.exists():
        external_hash = safe_sha256(external_path)
        if external_hash != vault_hash:
            raise MigrationError(
                "external and vault copies differ; resolve conflict first"
            )
        external_path.unlink()
        db.add_event(conn, file_id, "external-deleted",
                     str(external_path), None)

    db.update_hash(conn, file_id, vault_hash)
    db.set_status(conn, file_id, STATUS_PERMANENT)
    db.add_event(conn, file_id, "migrated-permanent", STATUS_MANAGED,
                 STATUS_PERMANENT)
    return db.row_to_dict(db.get(conn, file_id))


def resolve_conflict(conn, file_id, keep, vault_root):
    """Resolve a conflict: keep='vault' or keep='external'."""
    rec = db.get(conn, file_id)
    if rec is None:
        raise MigrationError("file id not found: %s" % file_id)
    if keep not in ("vault", "external"):
        raise MigrationError("keep must be 'vault' or 'external'")

    vault_path = _vault_file(vault_root, rec["vault_path"])
    external_path = Path(rec["original_path"]) if rec["original_path"] else None
    if not vault_path.exists():
        raise MigrationError("vault copy is missing: %s" % vault_path)

    if keep == "vault":
        if external_path is not None and external_path.exists():
            import shutil

            import header as headermod

            # vault copy -> external: strip the source-path header
            had_header, _src = headermod.remove_header(vault_path)
            if had_header:
                external_path.write_text(
                    vault_path.read_text(encoding="utf-8"), encoding="utf-8"
                )
                headermod.add_header(vault_path, _src)
            else:
                shutil.copy2(vault_path, external_path)
            db.add_event(conn, file_id, "conflict-resolved",
                         "external", "vault")
        winner_hash = headermod.body_sha256(vault_path)
        db.update_hash(conn, file_id, winner_hash)
        if external_path is not None:
            db.update_external_hash(conn, file_id, winner_hash)
    else:  # keep external
        if external_path is None or not external_path.exists():
            raise MigrationError("external file is missing: %s" % external_path)
        import shutil

        import header as headermod

        prior_header = headermod.read_header(vault_path)
        shutil.copy2(external_path, vault_path)
        if prior_header:
            headermod.add_header(vault_path, prior_header)
        external_hash = safe_sha256(external_path)
        db.update_hash(conn, file_id, external_hash)
        db.update_external_hash(conn, file_id, external_hash)
        db.add_event(conn, file_id, "conflict-resolved", "vault", "external")

    db.set_status(conn, file_id, STATUS_MANAGED)
    return db.row_to_dict(db.get(conn, file_id))

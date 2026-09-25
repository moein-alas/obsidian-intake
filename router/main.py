#!/usr/bin/env python3
"""Obsidian Intake - router.

Subcommands:
    <path>                          route a file (default workflow)
    list                            JSON list of managed files
    check <vault-relative-path>     JSON status of one managed file
    history <id>                    JSON audit history of one file
    history-all [limit]             JSON global event timeline
    move <id> <folder>              move vault copy to another vault folder
    delete-external <id>            delete external file, keep vault copy
    cleanup-deleted                 purge Deleted-Notes files past retention
    list-deleted                    JSON Deleted-Notes files + remaining time
    mark-deleted <path>             start Deleted-Notes countdown for a file
    undelete <path>                 cancel the Deleted-Notes countdown
    set-always-linked <id> <0|1>    update the always-linked flag
    set-delete-protected <id> <0|1> protect/unprotect a Deleted-Notes copy
    toggle-delete-protected <id>     toggle retention protection
    set-temp-suppressed <id> <0|1>   persist restored-copy temporary-read opt-out
    restore-deleted <id>             restore copy to the managed folder
    recovery-diff <id>               line diff for a pending recovery
    resolve-recovery <id> <vault|external> resolve pending edited-copy recovery
    vault-renamed <old> <new>        sync a Vault-side rename/move into the DB
    migrate <id>                    move file permanently into the vault
    conflict <id> <vault|external>  resolve a conflict
    sync-push <id>                  copy vault copy over external file
    status                          JSON summary of the database
    open <vault-relative-path>      open a vault file in Obsidian
"""

import difflib
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import config as cfgmod  # noqa: E402
import database as db  # noqa: E402
import header as headermod  # noqa: E402
import sync as syncmod  # noqa: E402
from database import STATUS_CONFLICT, STATUS_MANAGED, STATUS_PERMANENT  # noqa: E402
from hashing import safe_sha256, sha256  # noqa: E402


def _emit(obj):
    print(json.dumps(obj, indent=2))


def open_obsidian(path):
    if os.environ.get("MD_INTAKE_NO_OPEN"):
        return
    uri = "obsidian://open?path=" + urllib.parse.quote(str(path))
    subprocess.Popen(["xdg-open", uri])


def warn_no_vault(target):
    """Desktop notification when no vault is configured (clear diagnosis
    instead of a confusing Obsidian 'Vault not found' dialog)."""
    msg = ("Obsidian Intake: no Vault configured. "
           "Run installer/install.sh /path/to/Vault, then retry.")
    _notify_os("Obsidian Intake", msg, state_key="no-vault", cooldown=3600)


def _notify_os(app_name, msg, state_key=None, cooldown=0):
    """Send a desktop notification, at most once per cooldown for the
    same state_key (cooldown 0 = always send)."""
    import hashlib

    if state_key and cooldown > 0:
        OPEN_STATE_DIR.mkdir(parents=True, exist_ok=True)
        h = hashlib.sha1(state_key.encode("utf-8")).hexdigest()[:16]
        stamp = OPEN_STATE_DIR / ("notify-%s.json" % h)
        now = time.time()
        try:
            if now - json.loads(stamp.read_text())["last"] < cooldown:
                return
        except Exception:
            pass
        stamp.write_text(json.dumps({"last": now}))
    try:
        subprocess.Popen(
            ["notify-send", "-u", "critical", "-a", app_name,
             app_name, msg],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    except (OSError, FileNotFoundError):
        print("WARNING: " + msg, file=sys.stderr)


def _open_state_file(target):
    import hashlib

    OPEN_STATE_DIR.mkdir(parents=True, exist_ok=True)
    h = hashlib.sha1(str(target).encode("utf-8")).hexdigest()[:16]
    return OPEN_STATE_DIR / ("open-%s.json" % h)


def open_obsidian_limited(target, native):
    """Open a file in Obsidian with attempt limiting for formats Obsidian
    may not support. At most cfg.MAX_OPEN_ATTEMPTS within the window;
    after that, stop opening and show the OS message once per cooldown.

    Returns True when xdg-open was invoked.
    """
    import config as cfgmod

    if native or os.environ.get("MD_INTAKE_NO_OPEN"):
        open_obsidian(target)
        return True

    stamp = _open_state_file(target)
    now = time.time()
    try:
        data = json.loads(stamp.read_text())
    except Exception:
        data = {"attempts": [], "last_notify": 0}
    attempts = [t for t in data.get("attempts", [])
                if now - t < cfgmod.OPEN_ATTEMPT_WINDOW]

    if len(attempts) >= cfgmod.MAX_OPEN_ATTEMPTS:
        if now - data.get("last_notify", 0) > cfgmod.OPEN_NOTIFY_COOLDOWN:
            data["last_notify"] = now
            stamp.write_text(json.dumps(data))
            _notify_os(
                "Obsidian Intake",
                "Obsidian does not support opening this format (%s). "
                "Install the required plugin if needed."
                % Path(target).suffix.lstrip("."),
                state_key=None, cooldown=0,
            )
        return False

    attempts.append(now)
    data["attempts"] = attempts
    stamp.write_text(json.dumps(data))
    open_obsidian(target)
    return True


def _notify_not_in_list(target):
    """Inform that the format is outside the plugin settings list."""
    import config as cfgmod

    _notify_os(
        "Obsidian Intake",
        '"%s" is not in the Obsidian Intake settings list. To add '
        "support, open the Obsidian Intake plugin settings."
        % Path(target).name,
        state_key="not-in-list:%s" % target,
        cooldown=cfgmod.OPEN_NOTIFY_COOLDOWN,
    )


def _load_managed_extensions(conf):
    try:
        return conf.managed_extensions()
    except Exception:
        return cfgmod.DEFAULT_EXTENSIONS


def _inside_vault(conf, path):
    if conf.vault is None:
        return False
    try:
        path.relative_to(conf.vault)
        return True
    except ValueError:
        return False


def _to_vault_relative(conf, path):
    try:
        return path.relative_to(conf.vault).as_posix()
    except ValueError:
        return path.as_posix()


def _from_vault_arg(conf, arg):
    p = Path(arg).expanduser()
    if not p.is_absolute():
        p = conf.vault / p
    return p.resolve()


def _inside_dir(path, directory):
    try:
        Path(path).resolve().relative_to(Path(directory).resolve())
        return True
    except ValueError:
        return False


def _unique_destination(folder, filename):
    """Return a non-existing path in folder while keeping the extension."""
    folder = Path(folder)
    dest = folder / filename
    if not dest.exists():
        return dest
    stem = Path(filename).stem
    suffix = Path(filename).suffix
    i = 1
    while True:
        candidate = folder / ("%s-%d%s" % (stem, i, suffix))
        if not candidate.exists():
            return candidate
        i += 1


def _restore_deleted_record(conn, conf, rec):
    """Move a Deleted-Notes copy back to the managed folder.

    If the retained copy differs from the current external original, keep the
    edited copy intact and create a recovery_pending state. The extension then
    asks the user whether to save those edits back to the main file or discard
    them in favour of the external original.
    """
    vault_file = conf.vault / rec["vault_path"]
    if not vault_file.exists():
        raise FileNotFoundError("vault copy is missing")

    dest_dir = conf.managed_dir()
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = _unique_destination(dest_dir, vault_file.name)

    external_path = Path(rec["original_path"]) if rec["original_path"] else None
    vault_hash = headermod.body_sha256(vault_file)
    external_hash = safe_sha256(external_path) if external_path else None
    recovery_pending = bool(
        external_hash is not None and vault_hash is not None
        and external_hash != vault_hash
    )

    old_rel = rec["vault_path"]
    shutil.move(str(vault_file), str(dest))
    rel = _to_vault_relative(conf, dest)
    db.set_vault_path(conn, rec["id"], rel)
    db.set_entered_deleted(conn, rec["id"], None)
    db.set_delete_protected(conn, rec["id"], False)
    db.set_recovery_pending(
        conn, rec["id"], recovery_pending,
        external_hash if recovery_pending else None,
    )
    db.set_temporary_read_suppressed(conn, rec["id"], True)
    if vault_hash is not None:
        db.update_hash(conn, rec["id"], vault_hash)
    if external_hash is not None:
        db.update_external_hash(conn, rec["id"], external_hash)
    db.set_status(conn, rec["id"], STATUS_MANAGED)
    db.add_event(conn, rec["id"], "restored-from-deleted", old_rel, rel)
    if recovery_pending:
        db.add_event(conn, rec["id"], "recovery-pending",
                     external_hash, vault_hash)
    return db.get(conn, rec["id"])


def _read_body_text(path):
    """Read a UTF-8 managed file without the optional intake header."""
    text = Path(path).read_text(encoding="utf-8")
    first, sep, rest = text.partition("\n")
    if headermod.HEADER_RE.match(first):
        return rest if sep else ""
    return text


def _open_in_vault(conn, conf, path):
    """Open a vault file; refresh its hash in DB if it changed while inside."""
    import header as headermod

    rel = _to_vault_relative(conf, path)
    rec = db.get_by_vault_path(conn, rel)
    if rec is None:
        rec = db.get_by_vault_path(conn, str(path))
    if rec is not None and rec["status"] != STATUS_CONFLICT:
        h = headermod.body_sha256(path)
        if h and h != rec["current_hash"]:
            db.add_event(conn, rec["id"], "modified-in-vault",
                         rec["current_hash"], h)
            db.update_hash(conn, rec["id"], h)
    open_obsidian(path)


def _import_external(conn, conf, src):
    """Copy an external file into the vault managed folder and register it."""
    folder = conf.managed_dir()
    folder.mkdir(parents=True, exist_ok=True)

    dest = folder / src.name
    i = 0
    stem, suffix = src.stem, src.suffix
    while dest.exists():
        i += 1
        dest = folder / ("%s-%d%s" % (stem, i, suffix))

    src_hash = sha256(src)
    shutil.copy2(src, dest)

    # Optional gray source-path header (plugin setting writeSourceHeader)
    if conf.write_source_header() and src.suffix.lower() in (".md", ".markdown"):
        import header as headermod

        headermod.add_header(dest, str(src))

    # DB stores content hashes without the header line
    import header as headermod

    vault_hash = headermod.body_sha256(dest)

    rel = _to_vault_relative(conf, dest)
    rehomed = _rehome_dangling_record(
        conn, conf, src, dest, src_hash, vault_hash
    )
    if rehomed is not None:
        return dest, rehomed

    file_id = db.add_file(
        conn, str(src), rel, src_hash, vault_hash, src_hash,
        dest.stat().st_size, STATUS_MANAGED,
    )
    db.add_event(conn, file_id, "import", str(src), rel)
    return dest, file_id


def _rehome_dangling_record(conn, conf, src, dest, src_hash, vault_hash):
    """If a previously known file left the vault and is imported again
    (e.g. from Deleted-Notes via Not-Indexed), re-home its record instead
    of creating a duplicate. Returns the record id or None."""
    dangling = db.get_by_original_path(conn, str(src))
    if dangling is None:
        return None
    old_file = conf.vault / dangling["vault_path"]
    if old_file.exists():
        return None
    rel = _to_vault_relative(conf, dest)
    db.set_vault_path(conn, dangling["id"], rel)
    db.update_hash(conn, dangling["id"], vault_hash)
    db.update_external_hash(conn, dangling["id"], src_hash)
    db.set_file_size(conn, dangling["id"], dest.stat().st_size)
    db.set_status(conn, dangling["id"], STATUS_MANAGED)
    db.set_entered_deleted(conn, dangling["id"], None)
    db.set_delete_protected(conn, dangling["id"], False)
    db.set_recovery_pending(conn, dangling["id"], False)
    db.set_temporary_read_suppressed(conn, dangling["id"], False)
    db.add_event(conn, dangling["id"], "re-imported",
                 dangling["vault_path"], rel)
    return dangling["id"]


def route(conn, conf, target):
    """Main routing workflow from the status document."""
    if conf.vault is None:
        # No vault configured: warn clearly, then legacy behaviour.
        exts = _load_managed_extensions(conf)
        if target.suffix.lstrip(".").lower() in exts:
            warn_no_vault(target)
        open_obsidian(target)
        return

    if not target.exists():
        open_obsidian(target)
        return

    # 1) File inside the vault -> open directly.
    if _inside_vault(conf, target):
        _open_in_vault(conn, conf, target)
        return

    # 2) Not a managed extension -> pass through (do not block opening),
    #    but inform the user it is outside the plugin settings list.
    exts = _load_managed_extensions(conf)
    if target.suffix.lstrip(".").lower() not in exts:
        open_obsidian(target)
        _notify_not_in_list(target)
        return

    # 3) File outside the vault -> import / manage.
    rec = db.get_by_original_path(conn, str(target))
    src_hash = sha256(target)

    if rec is not None:
        vault_path = conf.vault / rec["vault_path"]

        # Re-opening the external original while its managed copy is in
        # Deleted-Notes restores that copy to Not-Indexed first. Any content
        # difference becomes an explicit recovery decision in the document UI.
        if vault_path.exists() and (
            rec["entered_deleted_at"] or _inside_dir(vault_path, conf.deleted_dir())
        ):
            try:
                restored = _restore_deleted_record(conn, conf, rec)
            except (OSError, FileNotFoundError) as exc:
                _emit({"ok": False, "error": str(exc)})
                return
            restored_path = conf.vault / restored["vault_path"]
            open_obsidian_limited(
                restored_path,
                conf.is_obsidian_native(target.suffix.lstrip(".").lower()),
            )
            return

        # Opted out of the managed-copy link: open the external file
        # with its default OS application (no Obsidian routing).
        if not rec["always_linked"] and vault_path.exists():
            subprocess.Popen(["xdg-open", str(target)])
            return

        if not vault_path.exists():
            # The file left the vault earlier. Restore it at its previous
            # location so links from other documents work again.
            try:
                inside_deleted = (
                    vault_path.resolve()
                    .relative_to(conf.deleted_dir().resolve())
                    is not None
                )
            except ValueError:
                inside_deleted = False
            if not inside_deleted:
                vault_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(target, vault_path)
                if conf.write_source_header() and target.suffix.lower() in (".md", ".markdown"):
                    headermod.add_header(vault_path, str(target))
                body = headermod.body_sha256(vault_path)
                db.update_hash(conn, rec["id"], body)
                db.update_external_hash(conn, rec["id"], src_hash)
                db.set_file_size(conn, rec["id"], vault_path.stat().st_size)
                db.set_entered_deleted(conn, rec["id"], None)
                db.set_status(conn, rec["id"], STATUS_MANAGED)
                db.add_event(conn, rec["id"], "restored",
                             rec["vault_path"], str(target))
                open_obsidian_limited(
                    vault_path, conf.is_obsidian_native(
                        target.suffix.lstrip(".").lower())
                )
                return

        if rec["status"] == STATUS_PERMANENT:
            if vault_path.exists():
                vault_hash = headermod.body_sha256(vault_path)
                if src_hash == vault_hash:
                    db.add_event(conn, rec["id"], "duplicate-seen",
                                 str(target), "vault")
                else:
                    db.add_event(conn, rec["id"], "external-reappeared",
                                 None, src_hash)
                open_obsidian(vault_path)
                return
        elif vault_path.exists():
            native = conf.is_obsidian_native(
                target.suffix.lstrip(".").lower())
            vault_hash = headermod.body_sha256(vault_path)
            state = syncmod.compare(rec, vault_hash, src_hash)
            if state == syncmod.IN_SYNC:
                db.update_external_hash(conn, rec["id"], src_hash)
                open_obsidian_limited(vault_path, native)
                return
            if state == syncmod.EXTERNAL_CHANGED:
                # Vault copy untouched -> auto pull external into vault.
                syncmod.pull_external_to_vault(conn, rec, target, vault_path)
                open_obsidian_limited(vault_path, native)
                return
            if state == syncmod.VAULT_CHANGED:
                db.update_hash(conn, rec["id"], vault_hash)
                db.update_external_hash(conn, rec["id"], src_hash)
                open_obsidian_limited(vault_path, native)
                return
            # BOTH_CHANGED -> conflict, user decides in dashboard.
            db.update_hash(conn, rec["id"], vault_hash)
            db.update_external_hash(conn, rec["id"], src_hash)
            db.set_status(conn, rec["id"], STATUS_CONFLICT)
            db.add_event(conn, rec["id"], "conflict-detected",
                         vault_hash, src_hash)
            open_obsidian_limited(vault_path, native)
            return

    # 4) Duplicate detection by content hash (avoid copies).
    dup = db.get_by_hash(conn, src_hash)
    if dup is not None:
        dup_path = conf.vault / dup["vault_path"]
        if dup_path.exists():
            db.add_event(conn, dup["id"], "duplicate-detected",
                         str(target), dup["vault_path"])
            open_obsidian(dup_path)
            return

    # 5) New external file -> import into managed folder.
    dest, _fid = _import_external(conn, conf, target)
    open_obsidian(dest)



# ------------------------------------------------------------------ CLI

def cmd_list(conn, conf):
    items = []
    for row in db.list_files(conn):
        d = db.row_to_dict(row)
        vault_path = conf.vault / d["vault_path"] if conf.vault else None
        d["external_exists"] = (
            bool(d["original_path"]) and Path(d["original_path"]).exists()
        )
        d["vault_exists"] = bool(vault_path and vault_path.exists())
        d.pop("original_hash", None)
        items.append(d)
    _emit(items)


def cmd_check(conn, conf, arg):
    path = _from_vault_arg(conf, arg)
    rel = _to_vault_relative(conf, path)
    rec = db.get_by_vault_path(conn, rel)
    if rec is None:
        _emit({"found": False, "path": rel})
        return
    d = db.row_to_dict(rec)
    d.pop("original_hash", None)
    vault_hash = headermod.body_sha256(path)
    external_path = Path(d["original_path"]) if d["original_path"] else None
    external_hash = safe_sha256(external_path) if external_path else None
    d["found"] = True
    d["conflict"] = d["status"] == STATUS_CONFLICT
    d["vault_exists"] = path.exists()
    d["external_exists"] = bool(external_path and external_path.exists())
    d["in_sync"] = (
        vault_hash is not None and external_hash is not None
        and vault_hash == external_hash
    )
    d["always_linked"] = bool(rec["always_linked"])
    d["entered_deleted_at"] = rec["entered_deleted_at"]
    if rec["entered_deleted_at"]:
        try:
            entered = datetime.fromisoformat(
                rec["entered_deleted_at"]).timestamp()
            retention = conf.deleted_retention_seconds()
            remaining = max(0.0, retention - (time.time() - entered))
            d["deleted_remaining_seconds"] = round(remaining, 1)
            d["expiring_soon"] = remaining < retention / 5.0
        except (TypeError, ValueError):
            pass
    _emit(d)


def cmd_history(conn, file_id):
    _emit([db.row_to_dict(r) for r in db.history(conn, int(file_id))])


def cmd_history_all(conn, limit=100):
    _emit([db.row_to_dict(r) for r in db.history_all(conn, int(limit))])


def cmd_move(conn, conf, file_id, target_dir):
    """Move the vault copy to another vault folder; DB follows the file."""
    rec = db.get(conn, int(file_id))
    if rec is None:
        _emit({"ok": False, "error": "file id not found"})
        return 1
    vault_file = conf.vault / rec["vault_path"]
    if not vault_file.exists():
        _emit({"ok": False, "error": "vault copy is missing"})
        return 1

    dest_dir = (conf.vault / target_dir).resolve()
    vault_root = Path(conf.vault).resolve()
    if dest_dir == vault_root or vault_root not in dest_dir.parents:
        _emit({"ok": False, "error": "target folder is outside the vault"})
        return 1

    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / vault_file.name
    if dest.exists():
        _emit({"ok": False, "error": "target file already exists: %s" % dest})
        return 1

    shutil.move(str(vault_file), str(dest))
    rel = _to_vault_relative(conf, dest)
    db.set_vault_path(conn, rec["id"], rel)
    db.add_event(conn, rec["id"], "moved", rec["vault_path"], rel)

    # Deleted-Notes countdown bookkeeping
    deleted_dir = conf.deleted_dir().resolve()
    was_deleted = rec["entered_deleted_at"] is not None
    try:
        dest_inside = (dest.resolve().relative_to(deleted_dir) is not None)
    except ValueError:
        dest_inside = False
    if dest_inside and not was_deleted:
        db.set_entered_deleted(
            conn, rec["id"], datetime.now(timezone.utc)
            .isoformat(timespec="seconds")
        )
        db.set_delete_protected(conn, rec["id"], False)
        db.set_recovery_pending(conn, rec["id"], False)
        db.set_temporary_read_suppressed(conn, rec["id"], False)
        db.add_event(conn, rec["id"], "entered-deleted", None,
                     conf.deleted_folder())
    elif not dest_inside and was_deleted:
        db.set_entered_deleted(conn, rec["id"], None)
        db.set_delete_protected(conn, rec["id"], False)
        db.add_event(conn, rec["id"], "left-deleted", None,
                     conf.deleted_folder())

    _emit({"ok": True, "file": db.row_to_dict(db.get(conn, rec["id"]))})


def cmd_purge(conn, conf, file_id):
    """Immediately delete the vault copy of a Deleted-Notes file."""
    rec = db.get(conn, int(file_id))
    if rec is None:
        _emit({"ok": False, "error": "file id not found"})
        return 1
    f = conf.vault / rec["vault_path"]
    if f.exists():
        f.unlink()
        db.add_event(conn, rec["id"], "purged", rec["vault_path"], None)
    _emit({"ok": True})


def cmd_cleanup_deleted(conn, conf):
    """Delete files whose Deleted-Notes retention has expired."""
    retention = conf.deleted_retention_seconds()
    now = time.time()
    removed = 0
    for row in db.list_files(conn):
        ts = row["entered_deleted_at"]
        if not ts or row["delete_protected"]:
            continue
        try:
            entered = datetime.fromisoformat(ts).timestamp()
        except (TypeError, ValueError):
            continue
        if now - entered < retention:
            continue
        f = conf.vault / row["vault_path"]
        if f.exists():
            f.unlink()
        db.add_event(conn, row["id"], "auto-purged",
                     row["vault_path"], None)
        removed += 1
    _emit({"ok": True, "removed": removed})


def cmd_list_deleted(conn, conf):
    """Files currently in Deleted-Notes with remaining lifetime."""
    retention = conf.deleted_retention_seconds()
    now = time.time()
    items = []
    for row in db.list_files(conn):
        if not row["entered_deleted_at"]:
            continue
        f = conf.vault / row["vault_path"]
        if not f.exists():
            continue
        try:
            entered = datetime.fromisoformat(
                row["entered_deleted_at"]).timestamp()
        except (TypeError, ValueError):
            continue
        remaining = max(0.0, retention - (now - entered))
        d = db.row_to_dict(row)
        d["retention_seconds"] = retention
        d["remaining_seconds"] = round(remaining, 1)
        d["delete_protected"] = bool(row["delete_protected"])
        d["expiring_soon"] = (
            not d["delete_protected"] and remaining < retention / 5.0
        )
        items.append(d)
    _emit(items)


def cmd_mark_deleted(conn, conf, vault_arg):
    """Record that a file was (manually) moved into Deleted-Notes."""
    rec = db.get_by_vault_path(conn, vault_arg)
    if rec is None:
        _emit({"ok": False, "error": "file not found: %s" % vault_arg})
        return 1
    db.set_entered_deleted(
        conn, rec["id"], datetime.now(timezone.utc)
        .isoformat(timespec="seconds")
    )
    db.set_delete_protected(conn, rec["id"], False)
    db.set_recovery_pending(conn, rec["id"], False)
    db.set_temporary_read_suppressed(conn, rec["id"], False)
    db.add_event(conn, rec["id"], "entered-deleted", None,
                 conf.deleted_folder())
    _emit({"ok": True})


def cmd_undelete(conn, conf, vault_arg):
    """Cancel the Deleted-Notes countdown (file moved out manually)."""
    rec = db.get_by_vault_path(conn, vault_arg)
    if rec is None:
        _emit({"ok": False, "error": "file not found: %s" % vault_arg})
        return 1
    db.set_entered_deleted(conn, rec["id"], None)
    db.set_delete_protected(conn, rec["id"], False)
    db.add_event(conn, rec["id"], "left-deleted", None,
                 conf.deleted_folder())
    _emit({"ok": True})


def cmd_set_temp_suppressed(conn, file_id, value):
    rec = db.get(conn, int(file_id))
    if rec is None:
        _emit({"ok": False, "error": "file id not found"})
        return 1
    flag = str(value) in ("1", "true", "True", "yes", "on")
    db.set_temporary_read_suppressed(conn, rec["id"], flag)
    _emit({"ok": True, "temporary_read_suppressed": flag})


def cmd_set_delete_protected(conn, file_id, value):
    rec = db.get(conn, int(file_id))
    if rec is None:
        _emit({"ok": False, "error": "file id not found"})
        return 1
    if not rec["entered_deleted_at"]:
        _emit({"ok": False, "error": "file is not in Deleted-Notes"})
        return 1
    flag = str(value) in ("1", "true", "True", "yes", "on")
    db.set_delete_protected(conn, rec["id"], flag)
    db.add_event(conn, rec["id"], "delete-protection-changed",
                 rec["delete_protected"], 1 if flag else 0)
    _emit({"ok": True, "delete_protected": flag,
           "file": db.row_to_dict(db.get(conn, rec["id"]))})


def cmd_toggle_delete_protected(conn, file_id):
    rec = db.get(conn, int(file_id))
    if rec is None:
        _emit({"ok": False, "error": "file id not found"})
        return 1
    if not rec["entered_deleted_at"]:
        _emit({"ok": False, "error": "file is not in Deleted-Notes"})
        return 1
    return cmd_set_delete_protected(
        conn, rec["id"], "0" if rec["delete_protected"] else "1"
    )


def cmd_restore_deleted(conn, conf, file_id):
    rec = db.get(conn, int(file_id))
    if rec is None:
        _emit({"ok": False, "error": "file id not found"})
        return 1
    if not rec["entered_deleted_at"] and not _inside_dir(
        conf.vault / rec["vault_path"], conf.deleted_dir()
    ):
        _emit({"ok": False, "error": "file is not in Deleted-Notes"})
        return 1
    try:
        restored = _restore_deleted_record(conn, conf, rec)
    except (OSError, FileNotFoundError) as exc:
        _emit({"ok": False, "error": str(exc)})
        return 1
    _emit({"ok": True, "file": db.row_to_dict(restored)})


def cmd_recovery_diff(conn, conf, file_id):
    rec = db.get(conn, int(file_id))
    if rec is None:
        _emit({"ok": False, "error": "file id not found"})
        return 1
    external = Path(rec["original_path"]) if rec["original_path"] else None
    vault_file = conf.vault / rec["vault_path"]
    if external is None or not external.exists() or not vault_file.exists():
        _emit({"ok": False, "error": "one side of the comparison is missing"})
        return 1
    try:
        main_text = external.read_text(encoding="utf-8")
        copy_text = _read_body_text(vault_file)
    except UnicodeDecodeError:
        _emit({
            "ok": True,
            "text_diff_available": False,
            "message": "Binary/non-UTF-8 difference; text highlighting is unavailable.",
        })
        return
    diff = list(difflib.unified_diff(
        main_text.splitlines(), copy_text.splitlines(),
        fromfile="main file", tofile="retained copy", lineterm="", n=2,
    ))
    limit = 500
    _emit({
        "ok": True,
        "text_diff_available": True,
        "different": main_text != copy_text,
        "truncated": len(diff) > limit,
        "lines": diff[:limit],
    })


def cmd_resolve_recovery(conn, conf, file_id, keep):
    rec = db.get(conn, int(file_id))
    if rec is None:
        _emit({"ok": False, "error": "file id not found"})
        return 1
    if not rec["recovery_pending"]:
        _emit({"ok": True, "already_resolved": True})
        return
    external = Path(rec["original_path"]) if rec["original_path"] else None
    vault_file = conf.vault / rec["vault_path"]
    if external is None or not external.exists() or not vault_file.exists():
        _emit({"ok": False, "error": "one side of the recovery is missing"})
        return 1

    current_external_hash = safe_sha256(external)
    baseline = rec["recovery_external_hash"]
    if baseline and current_external_hash != baseline:
        _emit({
            "ok": False,
            "error": "The main file changed again after the recovery prompt was created. Re-open it before deciding.",
            "external_changed": True,
        })
        return 1

    if keep in ("vault", "copy"):
        new_hash = syncmod.push_vault_to_external(
            conn, rec, external, vault_file
        )
        db.update_hash(conn, rec["id"], new_hash)
        resolution = "kept-copy-and-saved"
    elif keep in ("external", "main"):
        syncmod.pull_external_to_vault(
            conn, rec, external, vault_file
        )
        resolution = "discarded-copy-edits"
    else:
        _emit({"ok": False, "error": "keep must be vault|external"})
        return 1

    db.set_recovery_pending(conn, rec["id"], False)
    db.set_status(conn, rec["id"], STATUS_MANAGED)
    db.add_event(conn, rec["id"], "recovery-resolved", None, resolution)
    _emit({"ok": True, "resolution": resolution,
           "file": db.row_to_dict(db.get(conn, rec["id"]))})


def cmd_vault_renamed(conn, conf, old_path, new_path):
    """Synchronize a manual/observed Vault rename with DB lifecycle state.

    Router-originated moves are already recorded before Obsidian sees them;
    in that case this command is intentionally idempotent.
    """
    rec = db.get_by_vault_path(conn, new_path)
    if rec is None:
        rec = db.get_by_vault_path(conn, old_path)
    if rec is None:
        _emit({"ok": False, "error": "tracked file not found"})
        return 1

    if rec["vault_path"] != new_path:
        db.set_vault_path(conn, rec["id"], new_path)
        db.add_event(conn, rec["id"], "vault-renamed", old_path, new_path)
        rec = db.get(conn, rec["id"])

    old_deleted = old_path == conf.deleted_folder() or old_path.startswith(
        conf.deleted_folder() + "/"
    )
    new_deleted = new_path == conf.deleted_folder() or new_path.startswith(
        conf.deleted_folder() + "/"
    )
    if new_deleted and not rec["entered_deleted_at"]:
        db.set_entered_deleted(
            conn, rec["id"], datetime.now(timezone.utc)
            .isoformat(timespec="seconds")
        )
        db.set_delete_protected(conn, rec["id"], False)
        db.set_recovery_pending(conn, rec["id"], False)
        db.set_temporary_read_suppressed(conn, rec["id"], False)
        db.add_event(conn, rec["id"], "entered-deleted", None,
                     conf.deleted_folder())
    elif old_deleted and not new_deleted and rec["entered_deleted_at"]:
        db.set_entered_deleted(conn, rec["id"], None)
        db.set_delete_protected(conn, rec["id"], False)
        db.add_event(conn, rec["id"], "left-deleted", None,
                     conf.deleted_folder())
    _emit({"ok": True, "file": db.row_to_dict(db.get(conn, rec["id"]))})


def cmd_set_always_linked(conn, file_id, value):
    """Update the 'always linked to main file' flag (banner checkbox)."""
    rec = db.get(conn, int(file_id))
    if rec is None:
        _emit({"ok": False, "error": "file id not found"})
        return 1
    flag = 1 if str(value) in ("1", "true", "True", "yes") else 0
    db.set_always_linked(conn, rec["id"], flag)
    db.add_event(conn, rec["id"], "always-linked-changed",
                 rec["always_linked"], flag)
    _emit({"ok": True, "file": db.row_to_dict(db.get(conn, rec["id"]))})


def cmd_delete_external(conn, conf, file_id):
    """Delete the external file; only the vault copy remains (permanent)."""
    rec = db.get(conn, int(file_id))
    if rec is None:
        _emit({"ok": False, "error": "file id not found"})
        return 1
    vault_file = conf.vault / rec["vault_path"]
    if not vault_file.exists():
        _emit({"ok": False, "error": "vault copy is missing"})
        return 1
    external_path = Path(rec["original_path"]) if rec["original_path"] else None
    if external_path is not None and external_path.exists():
        external_path.unlink()
        db.add_event(conn, rec["id"], "external-deleted",
                     str(external_path), None)
    db.set_status(conn, rec["id"], STATUS_PERMANENT)
    _emit({"ok": True, "file": db.row_to_dict(db.get(conn, rec["id"]))})

def cmd_migrate(conn, conf, file_id):
    import migration

    try:
        rec = migration.move_permanent(conn, int(file_id), conf.vault)
    except migration.MigrationError as e:
        _emit({"ok": False, "error": str(e)})
        return 1
    rec.pop("original_hash", None)
    _emit({"ok": True, "file": rec})


def cmd_conflict(conn, conf, file_id, keep):
    import migration

    try:
        rec = migration.resolve_conflict(conn, int(file_id), keep, conf.vault)
    except migration.MigrationError as e:
        _emit({"ok": False, "error": str(e)})
        return 1
    rec.pop("original_hash", None)
    _emit({"ok": True, "file": rec})


def cmd_sync_push(conn, conf, file_id):
    rec = db.get(conn, int(file_id))
    if rec is None:
        _emit({"ok": False, "error": "file id not found"})
        return 1
    external_path = Path(rec["original_path"]) if rec["original_path"] else None
    if external_path is None or not external_path.exists():
        _emit({"ok": False, "error": "external file is missing"})
        return 1
    vault_file = conf.vault / rec["vault_path"]
    new_hash = syncmod.push_vault_to_external(
        conn, rec, external_path, vault_file
    )
    db.update_hash(conn, rec["id"], new_hash)
    db.set_recovery_pending(conn, rec["id"], False)
    db.set_status(conn, rec["id"], STATUS_MANAGED)
    _emit({"ok": True})


def cmd_status(conn):
    files = db.list_files(conn)
    summary = {
        "total": len(files),
        "managed": sum(1 for f in files if f["status"] == STATUS_MANAGED),
        "permanent": sum(1 for f in files if f["status"] == STATUS_PERMANENT),
        "conflict": sum(1 for f in files if f["status"] == STATUS_CONFLICT),
    }
    _emit(summary)


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    conf = cfgmod.Config()
    conf.ensure_dirs()
    conn = db.connect()

    try:
        if not argv:
            return 1
        cmd = argv[0]

        if cmd == "list":
            cmd_list(conn, conf)
        elif cmd == "check" and len(argv) >= 2:
            cmd_check(conn, conf, argv[1])
        elif cmd == "history" and len(argv) >= 2:
            cmd_history(conn, argv[1])
        elif cmd == "history-all":
            cmd_history_all(conn, argv[1] if len(argv) >= 2 else 100)
        elif cmd == "move" and len(argv) >= 3:
            return cmd_move(conn, conf, argv[1], argv[2])
        elif cmd == "delete-external" and len(argv) >= 2:
            return cmd_delete_external(conn, conf, argv[1])
        elif cmd == "cleanup-deleted":
            cmd_cleanup_deleted(conn, conf)
        elif cmd == "purge" and len(argv) >= 2:
            return cmd_purge(conn, conf, argv[1])
        elif cmd == "list-deleted":
            cmd_list_deleted(conn, conf)
        elif cmd == "mark-deleted" and len(argv) >= 2:
            return cmd_mark_deleted(conn, conf, argv[1])
        elif cmd == "undelete" and len(argv) >= 2:
            return cmd_undelete(conn, conf, argv[1])
        elif cmd == "set-always-linked" and len(argv) >= 3:
            return cmd_set_always_linked(conn, argv[1], argv[2])
        elif cmd == "set-delete-protected" and len(argv) >= 3:
            return cmd_set_delete_protected(conn, argv[1], argv[2])
        elif cmd == "set-temp-suppressed" and len(argv) >= 3:
            return cmd_set_temp_suppressed(conn, argv[1], argv[2])
        elif cmd == "toggle-delete-protected" and len(argv) >= 2:
            return cmd_toggle_delete_protected(conn, argv[1])
        elif cmd == "restore-deleted" and len(argv) >= 2:
            return cmd_restore_deleted(conn, conf, argv[1])
        elif cmd == "recovery-diff" and len(argv) >= 2:
            return cmd_recovery_diff(conn, conf, argv[1])
        elif cmd == "resolve-recovery" and len(argv) >= 3:
            return cmd_resolve_recovery(conn, conf, argv[1], argv[2])
        elif cmd == "vault-renamed" and len(argv) >= 3:
            return cmd_vault_renamed(conn, conf, argv[1], argv[2])
        elif cmd == "migrate" and len(argv) >= 2:
            return cmd_migrate(conn, conf, argv[1])
        elif cmd == "conflict" and len(argv) >= 3:
            return cmd_conflict(conn, conf, argv[1], argv[2])
        elif cmd == "sync-push" and len(argv) >= 2:
            return cmd_sync_push(conn, conf, argv[1])
        elif cmd == "status":
            cmd_status(conn)
        elif cmd == "open" and len(argv) >= 2 and conf.vault:
            open_obsidian(_from_vault_arg(conf, argv[1]))
        else:
            # Default: route a file path.
            route(conn, conf, Path(argv[0]).expanduser().resolve())
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())


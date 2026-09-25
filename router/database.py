"""SQLite database engine: files and events (audit history)."""

import sqlite3
from datetime import datetime, timezone

from config import CONFIG_DIR, DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS files (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    original_path  TEXT,
    vault_path     TEXT UNIQUE,
    original_hash  TEXT,
    current_hash   TEXT,
    external_hash  TEXT,
    file_size      INTEGER,
    status         TEXT NOT NULL DEFAULT 'managed',
    entered_deleted_at TEXT,
    delete_protected INTEGER NOT NULL DEFAULT 0,
    recovery_pending INTEGER NOT NULL DEFAULT 0,
    recovery_external_hash TEXT,
    temporary_read_suppressed INTEGER NOT NULL DEFAULT 0,
    always_linked  INTEGER NOT NULL DEFAULT 1,
    created_at     TEXT NOT NULL,
    updated_at     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS events (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    file_id    INTEGER NOT NULL REFERENCES files(id),
    event      TEXT NOT NULL,
    old_value  TEXT,
    new_value  TEXT,
    timestamp  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_files_hash ON files(current_hash);
CREATE INDEX IF NOT EXISTS idx_files_original ON files(original_path);
CREATE INDEX IF NOT EXISTS idx_events_file ON events(file_id);
"""

# Columns added after v1.3; added idempotently for existing databases.
MIGRATIONS = [
    ("files", "entered_deleted_at", "TEXT"),
    ("files", "delete_protected", "INTEGER NOT NULL DEFAULT 0"),
    ("files", "recovery_pending", "INTEGER NOT NULL DEFAULT 0"),
    ("files", "recovery_external_hash", "TEXT"),
    ("files", "temporary_read_suppressed", "INTEGER NOT NULL DEFAULT 0"),
    ("files", "always_linked", "INTEGER NOT NULL DEFAULT 1"),
]


def _migrate(conn):
    for table, column, coltype in MIGRATIONS:
        cols = [r[1] for r in conn.execute(
            "PRAGMA table_info(%s)" % table)]
        if column not in cols:
            conn.execute(
                "ALTER TABLE %s ADD COLUMN %s %s" % (table, column, coltype)
            )
    conn.commit()

STATUS_MANAGED = "managed"
STATUS_PERMANENT = "permanent"
STATUS_CONFLICT = "conflict"


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect(db_path=None):
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path or DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    _migrate(conn)
    return conn


def add_event(conn, file_id, event, old_value=None, new_value=None):
    conn.execute(
        "INSERT INTO events (file_id, event, old_value, new_value, timestamp)"
        " VALUES (?,?,?,?,?)",
        (file_id, event, old_value, new_value, _now()),
    )
    conn.commit()


def add_file(conn, original_path, vault_path, original_hash, current_hash,
             external_hash, file_size, status=STATUS_MANAGED):
    now = _now()
    cur = conn.execute(
        "INSERT INTO files (original_path, vault_path, original_hash,"
        " current_hash, external_hash, file_size, status, created_at, updated_at)"
        " VALUES (?,?,?,?,?,?,?,?,?)",
        (original_path, vault_path, original_hash, current_hash,
         external_hash, file_size, status, now, now),
    )
    conn.commit()
    return cur.lastrowid


def get(conn, file_id):
    return conn.execute(
        "SELECT * FROM files WHERE id = ?", (file_id,)
    ).fetchone()


def get_by_vault_path(conn, vault_path):
    return conn.execute(
        "SELECT * FROM files WHERE vault_path = ?", (str(vault_path),)
    ).fetchone()


def get_by_original_path(conn, original_path):
    return conn.execute(
        "SELECT * FROM files WHERE original_path = ? ORDER BY id DESC LIMIT 1",
        (str(original_path),),
    ).fetchone()


def get_by_hash(conn, content_hash):
    if not content_hash:
        return None
    return conn.execute(
        "SELECT * FROM files WHERE current_hash = ? OR original_hash = ?"
        " ORDER BY id LIMIT 1",
        (content_hash, content_hash),
    ).fetchone()


def update_hash(conn, file_id, current_hash):
    conn.execute(
        "UPDATE files SET current_hash = ?, updated_at = ? WHERE id = ?",
        (current_hash, _now(), file_id),
    )
    conn.commit()


def update_external_hash(conn, file_id, external_hash):
    conn.execute(
        "UPDATE files SET external_hash = ?, updated_at = ? WHERE id = ?",
        (external_hash, _now(), file_id),
    )
    conn.commit()


def set_status(conn, file_id, status):
    conn.execute(
        "UPDATE files SET status = ?, updated_at = ? WHERE id = ?",
        (status, _now(), file_id),
    )
    conn.commit()


def set_vault_path(conn, file_id, vault_path):
    conn.execute(
        "UPDATE files SET vault_path = ?, updated_at = ? WHERE id = ?",
        (str(vault_path), _now(), file_id),
    )
    conn.commit()


def set_entered_deleted(conn, file_id, timestamp):
    conn.execute(
        "UPDATE files SET entered_deleted_at = ?, updated_at = ?"
        " WHERE id = ?",
        (timestamp, _now(), file_id),
    )
    conn.commit()


def set_always_linked(conn, file_id, value):
    conn.execute(
        "UPDATE files SET always_linked = ?, updated_at = ? WHERE id = ?",
        (1 if value else 0, _now(), file_id),
    )
    conn.commit()


def set_delete_protected(conn, file_id, value):
    conn.execute(
        "UPDATE files SET delete_protected = ?, updated_at = ? WHERE id = ?",
        (1 if value else 0, _now(), file_id),
    )
    conn.commit()


def set_recovery_pending(conn, file_id, value, external_hash=None):
    conn.execute(
        "UPDATE files SET recovery_pending = ?, recovery_external_hash = ?, "
        "updated_at = ? WHERE id = ?",
        (1 if value else 0, external_hash if value else None, _now(), file_id),
    )
    conn.commit()


def set_temporary_read_suppressed(conn, file_id, value):
    conn.execute(
        "UPDATE files SET temporary_read_suppressed = ?, updated_at = ? "
        "WHERE id = ?",
        (1 if value else 0, _now(), file_id),
    )
    conn.commit()


def set_file_size(conn, file_id, size):
    conn.execute(
        "UPDATE files SET file_size = ?, updated_at = ? WHERE id = ?",
        (size, _now(), file_id),
    )
    conn.commit()


def list_files(conn):
    return conn.execute("SELECT * FROM files ORDER BY updated_at DESC").fetchall()


def history(conn, file_id, limit=50):
    return conn.execute(
        "SELECT * FROM events WHERE file_id = ? ORDER BY id DESC LIMIT ?",
        (file_id, limit),
    ).fetchall()


def history_all(conn, limit=100):
    """Global event timeline across all files."""
    return conn.execute(
        "SELECT e.id, e.file_id, e.event, e.old_value, e.new_value,"
        " e.timestamp, f.vault_path"
        " FROM events e LEFT JOIN files f ON e.file_id = f.id"
        " ORDER BY e.id DESC LIMIT ?",
        (limit,),
    ).fetchall()


def row_to_dict(row):
    return dict(row) if row is not None else None

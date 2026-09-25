-- Reference schema for the Obsidian Intake database (v1.4.2).
-- The router creates/migrates this schema automatically (router/database.py).

CREATE TABLE IF NOT EXISTS files (
    id                     INTEGER PRIMARY KEY AUTOINCREMENT,
    original_path          TEXT,               -- absolute path of the external file
    vault_path             TEXT UNIQUE,        -- vault-relative path of managed copy
    original_hash          TEXT,               -- SHA-256 at import time
    current_hash           TEXT,               -- SHA-256 of the vault copy body
    external_hash          TEXT,               -- last seen SHA-256 of external file
    file_size              INTEGER,
    status                 TEXT NOT NULL DEFAULT 'managed',
    entered_deleted_at     TEXT,               -- UTC timestamp; starts Deleted-Notes countdown
    delete_protected       INTEGER NOT NULL DEFAULT 0,
    recovery_pending       INTEGER NOT NULL DEFAULT 0,
    recovery_external_hash TEXT,               -- external hash captured when recovery prompt is created
    temporary_read_suppressed INTEGER NOT NULL DEFAULT 0, -- restored copies stay non-temporary until user opts in
    always_linked          INTEGER NOT NULL DEFAULT 1,
    created_at             TEXT NOT NULL,
    updated_at             TEXT NOT NULL
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

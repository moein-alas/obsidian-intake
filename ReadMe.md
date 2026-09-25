Obsidian Intake lets you open and manage files located **outside** your Obsidian Vault directly from within Obsidian, without polluting the Vault's core structure. External files are pulled into a controlled, isolated workflow that handles their entire lifecycle: intake, tracking, temporary reads, deletion, retention, recovery and synchronization. The goal is to give the user **full control over external files** while keeping the Vault clean, organized and safe.

The project is designed for users who work with large collections of Markdown notes, documents, code files and external knowledge sources, and who need a reliable bridge between their file system and their Obsidian Vault.

---
## 🧭 Why This Project Exists

Obsidian is built around a single folder (the Vault). It expects every file to be part of that folder. But real work happens elsewhere: downloads, project directories, code repositories, temporary documents, PDFs, logs, and one-off notes. Copying all of that into the Vault permanently is messy. Ignoring it entirely means losing the benefits of Obsidian.

Obsidian Intake was created around three ideas:

1. **The Vault is a managed space, not a dumpster.** External files should not permanently clutter it. They enter a staging area (`not-indexed`), and from there they either become a permanent part of the Vault or leave cleanly.
2. **Files have identity, not just content.** Every managed file has a stable record: original path, vault path, hashes, size, lifecycle state and a complete event history. Two files with the same content are the same file — no accidental duplicates.
3. **Nothing destructive happens silently.** Deleting an external source, entering Deleted-Notes, or overwriting one side of a file are all reversible, logged and protected by hash verification.

### The Core Lifecycle

Every file that enters the system follows this path:

```
External file (outside the Vault)
        │
        │  first managed open
        ▼
not-indexed/                 ← managed copy inside the Vault
        │
        │  "Temporary read" checked + last view closes
        ▼
Deleted-Notes/               ← countdown to auto-deletion starts
        │
        ├── Restore / external re-open  ──►  back to not-indexed/
        │
        └── Retention expires           ──►  purged automatically
```


At any point the user can:

- Move the file to another Vault folder.
- Migrate it permanently (delete the external source, keep only the Vault copy).
- Resolve a conflict when both copies changed.
- Recover an edited Deleted-Notes copy back to the main file.
- Protect a Deleted-Notes copy from automatic deletion.

Every step is recorded in a local SQLite database, so the file's identity and history survive moves, renames and restarts.

### Customization

Almost everything about the workflow is configurable **from the plugin's own settings panel inside Obsidian** — no code edits, no manual SQL, no command-line flags. The settings panel exposes:

- Managed folder and Deleted-Notes folder names
- Managed file extensions (checkbox list + free-text custom extensions)
- Deleted-Notes retention (value + unit: days / months / years)
- Temporary-read default behavior
- Source-path header on/off (Markdown only)
- Graph exclusion on/off
- Top/bottom scroll buttons on/off
- Notification auto-dismiss delay
- Router executable path
- A live "Test" button that verifies the router is reachable

The router reads these settings directly from the plugin's `data.json`, so changes take effect immediately.

---
## 📦 Installation

Obsidian Intake has two parts, and both are installed by a single command:

1. **A Router** — a small Python CLI that performs the actual file-system work. It physically copies files, computes hashes, moves them between folders, syncs changes, and manages the SQLite database. It is the "hands" of the project.
    
2. **An Obsidian Extension** — the plugin you see inside Obsidian. It draws the document banner, the dashboard, the File Explorer badges, the Deleted-Notes context menu, the notifications and the settings panel. It talks to the router over a simple JSON CLI.

### Step 1 — Clone the repository

```bash
git clone https://github.com/moein-alas/obsidian-intake.git
cd obsidian-intake
```

### Step 2 — Run the installer

```bash
./installer/install.sh /path/to/YourVault
```

This will:

- Copy the router to `~/.local/share/obsidian-intake/router/` and symlink it to `~/.local/bin/obsidian-intake-router`.
- Write the Vault path to `~/.config/obsidian-intake/vault.conf`.
- Install the extension into `<Vault>/.obsidian/plugins/obsidian-intake/`.
- Register the desktop entry and MIME types so external files open through Obsidian Intake.
- Create the `not-indexed/` folder inside your Vault.

### Step 3 — Enable the extension inside Obsidian

The installer copies the extension into your Vault, but Obsidian does **not** enable community plugins automatically. You must do this manually:

1. Open Obsidian.
2. Go to **Settings → Community plugins**.
3. If "Restricted mode" is on, turn it off.
4. Click **Reload plugins** (or restart Obsidian).
5. Find **Obsidian Intake** in the installed plugins list and toggle it **on**.

Only after this step will the banner, dashboard, settings panel and Deleted-Notes features become active.

### Updating

```bash
./installer/update.sh /path/to/YourVault
```

The database and your plugin settings are preserved. Restart Obsidian after updating.

### Uninstalling

```bash
./installer/uninstall.sh /path/to/YourVault
```

The database, configuration and managed copies inside the Vault are intentionally kept, so you can reinstall later without losing data.

---
## 🚀 Features in Detail

### 📥 External File Intake

- Imports files from outside the Vault into the managed `not-indexed/` folder.
- Keeps a durable link between the original external path and the Vault copy.
- Only files whose extensions are in the managed list are intercepted. All other files open normally through their default OS application — the plugin never blocks them, but it **notifies** the user that the format is outside the managed list.
- Supports any extension the user adds, including `.org`, `.py`, `.ipynb`, `.rmd` and similar — though non-native formats still need an Obsidian plugin capable of opening them.

### 🆔 Duplicate Detection & File Identity

- Every file is identified by its **SHA-256 hash** (computed over the file body, excluding the optional source header).
- Re-importing a file whose content already exists in the Vault opens the existing copy instead of creating a second one.
- The database stores `original_hash`, `current_hash` and `external_hash` separately, so the Vault copy and the external original can change independently and still be tracked correctly.

### 🔄 File Lifecycle Management

- Every managed file has a status: `managed`, `permanent` or `conflict`.
- Additional per-file state flags: `always_linked`, `delete_protected`, `recovery_pending`, `temporary_read_suppressed`, `entered_deleted_at`.
- All state transitions are logged with timestamps in the `events` table.

### 🗑 Deleted-Notes Management

- Deleted files are not removed immediately — they move to a dedicated `Deleted-Notes/` folder with a countdown.
- Retention is configurable in **value + unit** (days / months / years), default **20 days**.
- Per-file protection: a file can be marked as "Do not delete after the retention limit" (shown as `∞` in the File Explorer badge). Protected files are skipped by the auto-cleanup.
- File Explorer shows a **days-left badge** next to each Deleted-Notes file, turning red when the file is in the last fifth of its retention.
- Right-click context menu on a Deleted-Notes file provides **Restore** and a toggle for automatic-deletion protection.
- Purge any Deleted-Notes file immediately from the dashboard.

### 🛟 Advanced Recovery System

When an external file is re-opened while its managed copy is sitting in Deleted-Notes:

1. The copy is restored back to `not-indexed/`.
2. If the retained copy and the external original **differ**, a `recovery_pending` state is created instead of silently overwriting either side.
3. A highlighted **unified line diff** is shown in the document banner (green for additions, red for removals), using Python's `difflib`.
4. The user decides:
    - **"Yes — recover & save"** → the edited copy is pushed to the external main file.
    - **"No — discard copy edits"** → the main file overwrites the copy.
5. Before either action runs, the router verifies that the external hash still matches the hash captured when the prompt was created. If the external file changed again in the meantime, the recovery is rejected and the user is asked to re-open it.

### ⏱ Temporary Read Mode

- Pre-checkable per document, visible directly in the **collapsed banner summary** for one-click access.
- When enabled, the managed copy is moved to `Deleted-Notes/` as soon as the last Obsidian view of the file closes.
- Close detection walks **all workspace file views**, not only Markdown leaves. This means `.txt`, `.py` and other custom formats work as long as Obsidian has a view capable of opening them.
- Restored files have temporary read **persistently suppressed** until the user explicitly re-checks it.

### 🧩 Obsidian Integration

- **Floating document banner** on every managed file — collapsible `<details>` element with a summary that keeps the Temporary-read checkbox always visible.
- **Dashboard modal** with per-file actions: Open, Move Permanently, Move to…, Overwrite External, Delete External, History, conflict resolution buttons.
- **Deleted-Notes section** in the dashboard with per-file Restore / Keep indefinitely / Purge now.
- **Folder suggest modal** for "Move to…" that fuzzy-searches all Vault folders.
- **Confirmation modal** for destructive actions (delete external, purge now).
- **Status bar item** showing the tracked file count and live conflict count.
- **Graph exclusion** — automatically adds `not-indexed/` to Obsidian's excluded-files list so managed copies do not pollute the graph or search.
- **Scroll-to-top / scroll-to-bottom** floating buttons that target the real scroll container of the active view (works across themes and view types).
    

### 🔔 Notifications

- Custom in-app notifications with an **OK** button and an **"Auto dismiss"** checkbox.
- The auto-dismiss preference is remembered **per notification type** — ticking it once only affects that type of notice.
- Configurable auto-dismiss delay (default 7 seconds).
- A "Reset auto dismiss memory" button clears all remembered preferences.
- OS-level desktop notifications for router-side events (e.g. "no Vault configured", "format not in settings list", "open attempts exhausted").

### 🚦 Open-Attempt Limiting

Some formats (non-native extensions) may not be openable by Obsidian at all. To avoid launching an unopenable app repeatedly:

- At most `MAX_OPEN_ATTEMPTS` (default 2) attempts are allowed within `OPEN_ATTEMPT_WINDOW` (default 15 seconds).
- After that, opening is skipped and a single OS-level warning is shown per `OPEN_NOTIFY_COOLDOWN` (default 60 seconds).
- Native Obsidian formats (md, images, audio, video, PDF) are always opened without restriction.

### 📊 History & Timeline Tracking

- Every action is recorded as an event: import, move, delete, restore, recovery, sync, hash change, protection change, vault-rename, conflict.
- Per-file history modal from the dashboard.
- Global **timeline modal** showing the entire event stream across all files with timestamps and paths.

### 💾 Local SQLite Database

- Stored at `~/.config/obsidian-intake/obsidian-intake.sqlite3`.
- Fully local — nothing is sent anywhere.
- Schema migrations are automatic and additive; upgrading from an older version does not require deleting the database.
- Tables: `files` (one row per managed file) and `events` (immutable audit log).

### 🔄 Vault Rename Support

- Manual renames and moves inside the Vault are detected by Obsidian and synchronized back to the database through the `vault-renamed` command.
- Router-originated moves are recorded before Obsidian sees them, so the operation is idempotent.
- Moving a file **into** `Deleted-Notes/` starts the countdown; moving it **out** clears it.

### 📝 Markdown Source Tracking

- Imported Markdown files can receive a gray source-path header:
    text
    %% obsidian-intake source: /original/absolute/path %%
- This header is **only** written to `.md` / `.markdown` files. Non-Markdown files (`.txt`, `.py`, `.json`, …) are preserved byte-for-byte so no foreign syntax leaks into them.
- When syncing to the external file, the header is stripped automatically so the external original stays clean.
- The header can be disabled entirely from the plugin settings.

### ⚙️ Configuration Options

All available in **Settings → Obsidian Intake**:
- Managed folder name (default: `not-indexed`)
- Deleted-Notes folder name (default: `Deleted-Notes`)
- Managed extensions (checkbox list + free-text custom extensions)
- Deleted-Notes retention value + unit
- Temporary read by default
- Write source path on import
- Exclude managed folder from graph
- Show top/bottom scroll buttons
- Auto dismiss delay (seconds)
- Reset auto dismiss memory
- Router path + live connection test

---
## 🏗 Architecture

```text
Obsidian-Intake
│
├── extension/
│   ├── main.js                  Obsidian plugin (banner, dashboard, badges, settings)
│   └── manifest.json
│
├── router/
│   ├── main.py                  CLI entry point + routing workflow
│   ├── config.py                Vault / folder / settings resolution
│   ├── database.py              SQLite engine + schema migrations
│   ├── hashing.py               SHA-256 identity
│   ├── header.py                Optional Markdown source-path header
│   ├── sync.py                  External ↔ Vault synchronization engine
│   └── migration.py             Permanent move + conflict resolution
│
├── database/
│   └── schema.sql               Reference schema
│
├── installer/
│   ├── install.sh
│   ├── update.sh
│   └── uninstall.sh
│
└── docs/
    └── Architecture.md
```
### How the two halves talk

The extension never touches the file system directly. It shells out to the router and parses JSON from stdout:

```text
extension  ──spawn──►  obsidian-intake-router <command> [args]
                            │
                            ▼
                       JSON on stdout
                            │
                            ▼
                       extension parses & renders
```
This keeps all mutation logic in one place, makes the workflow testable from a terminal, and prevents the plugin from holding long-lived file handles.

---
## 🛠 Technologies

- **JavaScript** — Obsidian Plugin API
- **Python 3** — router, hashing, sync, migration, recovery
- **SQLite** — local database for file state and event audit
- **Bash** — installer, updater, uninstaller
- **SHA-256** — file identity
- **difflib** — unified line diffs for the recovery UI

---
## 🔧 Router Commands

> ⚠️ **Work in progress.** The command set below is functional but still being expanded and refined. New commands may be added, existing names or arguments may change, and JSON output shapes are not yet frozen. Treat this section as a current snapshot rather than a stable API contract.

Available commands (invoked as `obsidian-intake-router <command> [args]`):

```bash
# Query & inspect
list                          list all managed files as JSON
check <vault-path>            status of one managed file
history <id>                  per-file event history
history-all [limit]           global event timeline
status                        summary counts
# Routing
<path>                        route an external file (default workflow)
open <vault-path>             open a file inside Obsidian
# Lifecycle
move <id> <folder>            move the Vault copy to another folder
migrate <id>                  transfer ownership permanently to the Vault
conflict <id> vault|external  resolve a conflict
sync-push <id>                copy Vault copy over external file
delete-external <id>          delete external file, keep Vault copy
# Deleted-Notes
mark-deleted <path>           start the retention countdown
undelete <path>               cancel the countdown
cleanup-deleted               purge files past retention
list-deleted                  files currently in Deleted-Notes
purge <id>                    delete a Vault copy immediately
restore-deleted <id>          restore copy to the managed folder
# Protection & flags
set-always-linked <id> <0|1>
set-delete-protected <id> <0|1>
toggle-delete-protected <id>
set-temp-suppressed <id> <0|1>
# Recovery
recovery-diff <id>            unified diff for a pending recovery
resolve-recovery <id> vault|external
# Synchronization helpers
vault-renamed <old> <new>     sync a manual Vault rename into the database
```

---
## 🎯 Design Philosophy

Obsidian Intake follows three principles:

### Local First

All metadata stays on your machine. No accounts, no network calls, no telemetry. The database lives in `~/.config/obsidian-intake/` and the router is a plain Python CLI.

### Data Safety

Files are protected through hash verification, recovery checks, non-destructive deletion, and an immutable audit log. The router refuses ambiguous operations (like resolving a conflict when one side is missing) rather than guessing.

### User Control

Every important action is reviewable, restorable, or reversible from inside Obsidian. Settings live in the plugin panel, not in config files. Notifications are per-type dismissible. Deleted-Notes retention is per-file protectable.

---
## 🗺 Roadmap

Potential future improvements:

- Advanced file conflict visualization (side-by-side viewer)
- Additional import sources (watched folders, clipboard)
- Extended synchronization options (bidirectional watch)
- Additional Obsidian integrations (Bases, Canvas, Bookmarks)
- Signed release artifacts and automated CI

---
## ⚖️ Disclaimer

**Obsidian Intake is an independent project and has no affiliation, endorsement, or support from Obsidian (Dynalist Inc.).** "Obsidian" is a trademark of Dynalist Inc. This plugin is a third-party community extension developed and maintained independently.

---
## 📄 License

See the `LICENSE` file in the repository for the full text.
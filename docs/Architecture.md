# Obsidian Intake - Architecture (v1.4.2)

## Components

    Obsidian Intake
    ├── Router (main.py, config.py)         routing + lifecycle CLI
    ├── Database (database.py, schema.sql)  SQLite file state + audit events
    ├── Hash Engine (hashing.py)            SHA-256 identity
    ├── Header (header.py)                  optional Markdown source-path header
    ├── Migration Engine (migration.py)     permanent move + conflicts
    ├── Sync Engine (sync.py)               external <-> vault synchronization
    ├── Obsidian Extension (main.js)        banners, dashboard, explorer badges,
    │                                       context menu, navigation controls
    └── Installer (install/update/uninstall)

## Managed-file lifecycle

    external file
        |
        | first managed open
        v
    Not-Indexed/  [managed copy]
        |
        | Temporary read checked + last view closes
        v
    Deleted-Notes/  [countdown starts]
        | \
        |  \-- "Do not delete..." enabled --> protected; cleanup skips it
        |
        | original external file opened again OR Restore selected
        v
    Not-Indexed/  [temporary-read suppressed]
        |
        +-- copy == external --> normal managed state
        |
        +-- copy != external --> recovery_pending
                                  |
                                  +-- Yes: keep copy + save to main
                                  +-- No : discard copy edits + pull main

A restored Deleted-Notes copy has `temporary_read_suppressed=1`. This keeps
Temporary read unchecked even when the global "Temporary read by default"
setting is enabled. The suppression is cleared only when the user explicitly
checks Temporary read again (or the file re-enters Deleted-Notes).

## Deleted-Notes state

Deleted-Notes is durable DB state, not only a folder name:

- `entered_deleted_at`: UTC timestamp that starts the retention countdown.
- `delete_protected`: when true, timed cleanup skips the copy.
- `recovery_pending`: the restored copy differs from the current main file and
  requires a user decision.
- `recovery_external_hash`: guards the recovery decision against a second main
  file change after the prompt was created.
- `temporary_read_suppressed`: prevents a restored file from immediately
  returning to Deleted-Notes.

The extension exposes this state in three places: the Deleted document banner,
the File Explorer countdown badge, and the Deleted-Notes right-click menu.

## Routing decision flow

    input: external path
      |
      +-- vault not configured ------------------> open directly
      +-- missing path ---------------------------> open directly
      +-- path already inside vault --------------> open + refresh tracked hash
      +-- extension not managed ------------------> pass through + notice
      +-- known record in Deleted-Notes ----------> restore to Not-Indexed
      |                                              compare copy vs main
      |                                              set recovery_pending if diff
      +-- known normal record --------------------> compare hashes
      |       +-- in sync ------------------------> open copy
      |       +-- external changed ---------------> pull external -> copy
      |       +-- vault changed ------------------> keep copy authoritative
      |       +-- both changed -------------------> conflict state
      +-- duplicate content ----------------------> open existing copy
      +-- otherwise ------------------------------> import to Not-Indexed

## Non-Markdown views

v1.4.2 no longer determines "open" state by `getLeavesOfType("markdown")`.
It walks all workspace leaves and reads `view.file` or `view.getState().file`.
This lets Temporary read close detection work for `.txt`, `.py`, and other
configured formats when an Obsidian view/plugin can open them.

The document banner is also attached to a generic active file view rather than
requiring `MarkdownView`.

Source-path headers use Obsidian's `%% ... %%` Markdown comment syntax, so
v1.4.2 writes that header only to `.md`/`.markdown` copies. Non-Markdown files
are preserved byte-for-byte on import apart from user edits/synchronization.

## Recovery diff

When a Deleted copy is restored and differs from its external original, the
router does not overwrite either side. `recovery-diff` builds a UTF-8 unified
line diff using Python `difflib`; the extension renders additions and removals
with different highlighted backgrounds above the document.

`resolve-recovery <id> vault` keeps the retained copy and writes it to the
external main file. `resolve-recovery <id> external` discards retained edits
and pulls the main file into the managed copy. Before either operation, the
router verifies that the external hash still matches the hash captured when
recovery was created.

## Extension <-> Router interface

The plugin executes the router and parses JSON stdout:

    status / list / check / history / history-all
    migrate / conflict / sync-push / delete-external / move / open
    cleanup-deleted / list-deleted / mark-deleted / undelete / purge
    set-always-linked
    set-delete-protected / toggle-delete-protected
    restore-deleted
    recovery-diff / resolve-recovery
    set-temp-suppressed
    vault-renamed

`vault-renamed` makes filesystem/Obsidian rename events idempotent with
router-originated moves and keeps `vault_path` synchronized for manual moves.

## Scroll navigation

The floating ↑/↓ controls locate the actual scrollable element of the active
view (`.cm-scroller`, CodeMirror scroll container, reading/preview container,
or generic view-content) and scroll that element. An Editor API fallback is
used when a scroll container is not directly exposed.

Setting location:

    Settings → Obsidian Intake → Interface & navigation
             → Show top/bottom scroll buttons

## Hashing rule

Database hashes describe file content without the optional Markdown source
header. Recovery/sync/duplicate decisions therefore compare the actual body.

## Storage locations

    ~/.local/bin/obsidian-intake-router
    ~/.local/share/obsidian-intake/router/
    ~/.config/obsidian-intake/vault.conf
    ~/.config/obsidian-intake/obsidian-intake.sqlite3
    <Vault>/.obsidian/plugins/obsidian-intake/

Database schema migration is automatic and additive; upgrading from v1.4 does
not require deleting the existing database.


## Settings/About presentation (1.4.2)

Public project links are centralized in `extension/main.js` under `ABOUT_INFO`. The settings UI reads the plugin version from `this.plugin.manifest.version`, so About does not require a second hard-coded version. Deleted-Notes explorer decoration reuses Obsidian's extension-tag DOM element and assigns the right-side auto margin to that tag, keeping the extension and countdown badges adjacent without wrapping core explorer nodes.

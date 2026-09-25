"""Central configuration for Obsidian Intake router."""

import os
from pathlib import Path

HOME = Path.home()

# Allow tests to redirect config/db/vault locations.
CONFIG_DIR = Path(
    os.environ.get("MD_INTAKE_CONFIG", str(HOME / ".config/obsidian-intake"))
)
DB_PATH = CONFIG_DIR / "obsidian-intake.sqlite3"
VAULT_CONF = CONFIG_DIR / "vault.conf"

DEFAULT_MANAGED_FOLDER = "Not-Indexed"
DEFAULT_DELETED_FOLDER = "Deleted-Notes"

DEFAULT_EXTENSIONS = ["md", "txt", "yaml", "json", "csv", "log"]

# Extensions Obsidian opens natively without any extra plugin.
OBSIDIAN_NATIVE_EXTS = [
    "md",
    "png", "jpg", "jpeg", "gif", "bmp", "svg", "webp",
    "mp3", "ogg", "wav", "flac", "m4a",
    "mp4", "webm", "mov",
    "pdf",
]

# Open-attempt limiting for formats Obsidian may not support:
# at most MAX_OPEN_ATTEMPTS tries within OPEN_ATTEMPT_WINDOW seconds,
# then stop opening and show the OS-level message once per cooldown.
MAX_OPEN_ATTEMPTS = 2
OPEN_ATTEMPT_WINDOW = 15          # seconds
OPEN_NOTIFY_COOLDOWN = 60         # seconds between repeat warnings
OPEN_STATE_DIR = CONFIG_DIR / "open-state"


class Config:
    """Runtime configuration: vault location and managed folder name."""

    def __init__(self):
        self.vault = self._load_vault()
        self.managed_folder = self._load_managed_folder()

    def _load_vault(self):
        # 1) explicit env override (used by tests)
        env = os.environ.get("MD_INTAKE_VAULT")
        if env:
            return Path(env).expanduser().resolve()

        # 2) vault.conf written by installer: first non-empty non-comment line
        if VAULT_CONF.exists():
            for line in VAULT_CONF.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line and not line.startswith("#"):
                    return Path(line).expanduser().resolve()
        return None

    def _load_managed_folder(self):
        if self.vault is None:
            return DEFAULT_MANAGED_FOLDER
        data = self.plugin_data()
        if data:
            folder = data.get("managedFolder")
            if isinstance(folder, str) and folder.strip():
                return folder.strip().strip("/")
        return DEFAULT_MANAGED_FOLDER

    def plugin_data(self):
        """Read plugin settings (extensions, managedFolder) from the vault."""
        if self.vault is None:
            return None
        p = (
            self.vault
            / ".obsidian"
            / "plugins"
            / "obsidian-intake"
            / "data.json"
        )
        try:
            import json

            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return None

    def managed_extensions(self):
        """Core extensions (checkbox list) + user custom extensions merged."""
        data = self.plugin_data()
        exts = None
        if data:
            raw = data.get("extensions")
            if isinstance(raw, list) and raw:
                exts = raw
        if not exts:
            exts = DEFAULT_EXTENSIONS

        # custom extensions typed by the user (free text box)
        if data:
            custom = data.get("customExtensions")
            if isinstance(custom, str):
                custom = custom.split(",")
            if isinstance(custom, list):
                exts = list(exts) + [
                    str(c) for c in custom if str(c).strip()
                ]

        return [
            e.strip().lstrip(".").lower()
            for e in exts if str(e).strip()
        ]

    def write_source_header(self):
        """Whether imported copies get the gray source-path header line."""
        data = self.plugin_data()
        if data is None:
            return True  # plugin default
        return bool(data.get("writeSourceHeader", True))

    def managed_dir(self):
        return self.vault / self.managed_folder

    def deleted_folder(self):
        data = self.plugin_data()
        if data:
            folder = data.get("deletedFolder")
            if isinstance(folder, str) and folder.strip():
                return folder.strip().strip("/")
        return DEFAULT_DELETED_FOLDER

    def deleted_dir(self):
        return self.vault / self.deleted_folder()

    def deleted_retention_seconds(self):
        """Retention for the Deleted-Notes folder, from plugin settings."""
        data = self.plugin_data() or {}
        value = data.get("deletedRetentionValue", 20)
        unit = data.get("deletedRetentionUnit", "days")
        try:
            value = float(value)
        except (TypeError, ValueError):
            value = 20.0
        if value <= 0:
            value = 20.0
        factors = {"days": 86400.0, "months": 2629800.0, "years": 31557600.0}
        return value * factors.get(unit, 86400.0)

    def ensure_dirs(self):
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        if self.vault is not None:
            self.managed_dir().mkdir(parents=True, exist_ok=True)
            self.deleted_dir().mkdir(parents=True, exist_ok=True)

    def is_obsidian_native(self, ext):
        return ext in OBSIDIAN_NATIVE_EXTS

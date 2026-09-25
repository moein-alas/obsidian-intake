#!/bin/bash
# Obsidian Intake v1.4.2 installer
set -e

VAULT="$1"
BASE="$(cd "$(dirname "$0")/.." && pwd)"

if [ -z "$VAULT" ]; then
 echo "Usage: install.sh /path/to/Vault"
 exit 1
fi

VAULT="$(cd "$VAULT" && pwd)"

BIN="$HOME/.local/bin"
APP_DIR="$HOME/.local/share/obsidian-intake"
CONF_DIR="$HOME/.config/obsidian-intake"
PLUGIN_DIR="$VAULT/.obsidian/plugins/obsidian-intake"

mkdir -p "$BIN" "$APP_DIR/router" "$CONF_DIR"
mkdir -p "$HOME/.local/share/applications"
mkdir -p "$HOME/.local/share/icons/hicolor/256x256/apps"
mkdir -p "$VAULT/Not-Indexed"
mkdir -p "$PLUGIN_DIR"

# --- Router engine (Python modules + entry point) ---
cp "$BASE/router/"*.py "$APP_DIR/router/"
cp "$BASE/router/obsidian-intake-router" "$APP_DIR/router/"
chmod +x "$APP_DIR/router/obsidian-intake-router"
ln -sf "$APP_DIR/router/obsidian-intake-router" "$BIN/obsidian-intake-router"

# --- Vault configuration (read by router/config.py) ---
printf '%s\n' "$VAULT" > "$CONF_DIR/vault.conf"

# --- Obsidian extension ---
cp "$BASE/extension/main.js" "$PLUGIN_DIR/"
cp "$BASE/extension/manifest.json" "$PLUGIN_DIR/"

# --- Desktop integration ---
cp "$BASE/icons/obsidian-intake.svg" \
"$HOME/.local/share/icons/hicolor/256x256/apps/obsidian-intake.svg"

sed "s|__HOME__|$HOME|g" "$BASE/installer/obsidian-intake.desktop" \
> "$HOME/.local/share/applications/obsidian-intake.desktop"

update-desktop-database "$HOME/.local/share/applications" || true
gtk-update-icon-cache "$HOME/.local/share/icons/hicolor" || true

xdg-mime default obsidian-intake.desktop text/markdown
xdg-mime default obsidian-intake.desktop text/x-markdown
xdg-mime default obsidian-intake.desktop text/plain

"$BIN/obsidian-intake-router" status || true

echo "Installed Obsidian Intake v1.4.2"
echo "Vault: $VAULT"

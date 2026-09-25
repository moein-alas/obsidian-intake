#!/bin/bash
# Obsidian Intake uninstaller.
# NOTE: the intake database and the managed copies inside the vault are KEPT.
set -e

VAULT="${1:-}"
CONF_DIR="$HOME/.config/obsidian-intake"

if [ -z "$VAULT" ] && [ -f "$CONF_DIR/vault.conf" ]; then
 VAULT="$(head -n1 "$CONF_DIR/vault.conf")"
fi

rm -f "$HOME/.local/bin/obsidian-intake-router"
rm -rf "$HOME/.local/share/obsidian-intake"
rm -f "$HOME/.local/share/applications/obsidian-intake.desktop"
rm -f "$HOME/.local/share/icons/hicolor/256x256/apps/obsidian-intake.svg"

if [ -n "$VAULT" ] && [ -d "$VAULT/.obsidian/plugins/obsidian-intake" ]; then
 rm -rf "$VAULT/.obsidian/plugins/obsidian-intake"
fi

update-desktop-database "$HOME/.local/share/applications" || true

echo "Uninstalled Obsidian Intake."
echo "Kept: $CONF_DIR (config + database) and vault managed copies."
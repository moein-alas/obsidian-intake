#!/bin/bash
# Obsidian Intake update: refresh router + extension, keep DB and config
set -e

VAULT="${1:-}"
BASE="$(cd "$(dirname "$0")/.." && pwd)"
CONF_DIR="$HOME/.config/obsidian-intake"

if [ -z "$VAULT" ]; then
 if [ -f "$CONF_DIR/vault.conf" ]; then
  VAULT="$(head -n1 "$CONF_DIR/vault.conf")"
 else
  echo "Usage: update.sh /path/to/Vault"
  exit 1
 fi
fi

echo "Updating with vault: $VAULT"
"$BASE/installer/install.sh" "$VAULT"
echo "Update complete (database and settings preserved)."
#!/usr/bin/env bash
# Wiki Inspi uninstaller. Removes the launchd agents, the `inspi` command and the Keychain token.
# Keeps your vault, your config and the logs unless you pass --purge-config / --purge-logs.
set -euo pipefail
CONF_DIR="${WIKI_INSPI_CONFIG_DIR:-$HOME/.config/wiki-inspi}"
export WIKI_INSPI_CONFIG="${WIKI_INSPI_CONFIG:-$CONF_DIR/config.json}"
LABEL_PREFIX="io.github.wiki-inspi"
PURGE_CONFIG=0; PURGE_LOGS=0
for a in "$@"; do case "$a" in --purge-config) PURGE_CONFIG=1 ;; --purge-logs) PURGE_LOGS=1 ;; *) echo "unknown option: $a" >&2; exit 2 ;; esac; done
REPO="$(cd "$(dirname "$0")" && pwd)"
PY="$HOME/.local/share/wiki-inspi/venv/bin/python"; [ -x "$PY" ] || PY="$(command -v python3)"
SERVICE="$("$PY" "$REPO/src/config.py" get keychain_service 2>/dev/null || echo wiki-inspi-token)"
LOG_DIR="$("$PY" "$REPO/src/config.py" get log_dir 2>/dev/null || echo "$HOME/Library/Logs/wiki-inspi")"

for name in receiver watcher enricher map; do
  launchctl bootout "gui/$(id -u)/$LABEL_PREFIX.$name" 2>/dev/null || true
  rm -f "$HOME/Library/LaunchAgents/$LABEL_PREFIX.$name.plist"
done
rm -f "$HOME/.local/bin/inspi" "$HOME/.local/bin/inspi-vision" "$HOME/.local/state/wiki-inspi/pair-open"
rm -rf "/Applications/Wiki Inspi.app" "$HOME/Applications/Wiki Inspi.app"
security delete-generic-password -a "${USER:-$(id -un)}" -s "$SERVICE" >/dev/null 2>&1 || true
rm -rf "$HOME/.local/share/wiki-inspi/venv"
[ "$PURGE_CONFIG" = 1 ] && rm -rf "$CONF_DIR"
[ "$PURGE_LOGS" = 1 ] && rm -rf "$LOG_DIR"
echo "Uninstalled. Your vault was not touched. Remove the extension from chrome://extensions by hand."

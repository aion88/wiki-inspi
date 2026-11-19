#!/usr/bin/env bash
# setup-token.sh: (re)generate the Wiki Inspi token, store it in the macOS Keychain and open the
# 60 s pairing window. The token value is never printed and never put in the clipboard.
# Re-running ROTATES it (the receiver reads the Keychain on every request; re-pair afterwards).
#
#   setup-token.sh [--extension-id <32 letters>] [--no-window]
#
# --extension-id  pin the extension id up front (shown on chrome://extensions) instead of
#                 learning it at the first pairing.
# --no-window     only store the token, do not open the pairing window (used by install.sh).
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
PYTHON="${PYTHON:-python3}"
EXT_ID=""; WINDOW=1
while [ $# -gt 0 ]; do
  case "$1" in
    --extension-id) EXT_ID="${2:-}"; shift 2 ;;
    --no-window) WINDOW=0; shift ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
done
PAIR_FILE="${WIKI_INSPI_PAIR_FILE:-$("$PYTHON" "$HERE/../src/config.py" get pair_file)}"

if [ -n "$EXT_ID" ]; then
  [[ "$EXT_ID" =~ ^[a-p]{32}$ ]] || { echo "extension id must be 32 letters a-p" >&2; exit 2; }
  "$PYTHON" -c 'import sys; sys.path.insert(0, sys.argv[1]); import config; config.save_key("extension_id", sys.argv[2])' "$HERE/../src" "$EXT_ID"
fi

"$PYTHON" "$HERE/../src/config.py" rotate-token
if [ "$WINDOW" = 1 ]; then
  mkdir -p "$(dirname "$PAIR_FILE")"
  : > "$PAIR_FILE"
  cat <<MSG
Pairing window open for 60 seconds, one use:
  chrome://extensions > Wiki Inspi > Details > Extension options > Pair, then Test.
If the window closed, run this script again.
MSG
fi

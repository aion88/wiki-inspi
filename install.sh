#!/usr/bin/env bash
# Wiki Inspi installer (macOS). Idempotent: safe to run again, never overwrites your notes,
# taxonomy or existing config values.
#
#   ./install.sh [--vault PATH] [--port N] [--backend claude-cli|anthropic-api|ollama]
#                [--drop-folder PATH] [--extension-id ID] [--non-interactive] [--no-launchd] [--no-keychain] [--token-file PATH]
#
# What it does: checks Python >= 3.10 and the dependencies (creating a private venv if they are
# missing), creates the vault skeleton, writes ~/.config/wiki-inspi/config.json, exports the Web
# Clipper templates, stores a token in the macOS Keychain, installs the `inspi` command and the
# launchd agents. Nothing leaves the machine.
set -euo pipefail

REPO="$(cd "$(dirname "$0")" && pwd)"
CONF_DIR="${WIKI_INSPI_CONFIG_DIR:-$HOME/.config/wiki-inspi}"
export WIKI_INSPI_CONFIG="${WIKI_INSPI_CONFIG:-$CONF_DIR/config.json}"
VENV="$HOME/.local/share/wiki-inspi/venv"
BIN_DIR="$HOME/.local/bin"
LABEL_PREFIX="io.github.wiki-inspi"

VAULT=""; PORT=""; BACKEND=""; DROP=""; EXT_ID=""; INTERACTIVE=1; LAUNCHD=1; NO_KEYCHAIN=0; TOKEN_FILE=""
while [ $# -gt 0 ]; do
  case "$1" in
    --vault) VAULT="${2:?}"; shift 2 ;;
    --port) PORT="${2:?}"; shift 2 ;;
    --backend) BACKEND="${2:?}"; shift 2 ;;
    --drop-folder) DROP="${2:?}"; shift 2 ;;
    --extension-id) EXT_ID="${2:?}"; shift 2 ;;
    --non-interactive) INTERACTIVE=0; shift ;;
    --no-launchd) LAUNCHD=0; shift ;;
    --token-file) TOKEN_FILE="${2:?}"; NO_KEYCHAIN=1; shift 2 ;;
    --no-keychain) NO_KEYCHAIN=1; shift ;;  # dry runs / CI: token in a 0600 file, no Keychain access
    -h|--help) sed -n '2,12p' "$0"; exit 0 ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
done
say() { printf '%s\n' "$*"; }
die() { printf 'error: %s\n' "$*" >&2; exit 1; }

[ "$(uname -s)" = "Darwin" ] || die "macOS only (Keychain and launchd are used)."

# ---- 1. Python and dependencies
PY="$(command -v python3 || true)"
[ -n "$PY" ] || die "python3 not found. Install Python 3.10+ (https://www.python.org or brew install python)."
"$PY" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' || die "Python 3.10+ required (found $("$PY" -V))."
if [ -x "$VENV/bin/python" ]; then PY="$VENV/bin/python"; fi
if ! "$PY" -c 'import PIL, yaml, anthropic, networkx, numpy' 2>/dev/null; then
  say "Installing dependencies (Pillow, PyYAML, anthropic, networkx, numpy) into a private venv: $VENV"
  "$PY" -m venv "$VENV"
  "$VENV/bin/python" -m pip install --quiet -r "$REPO/requirements.txt"
  PY="$VENV/bin/python"
fi
"$PY" -c 'import PIL, yaml, anthropic, networkx, numpy' || die "dependencies still missing after install."
say "python:  $PY ($("$PY" -V))"
xcode-select -p >/dev/null 2>&1 || say "note:    Xcode command line tools not found (no swiftc). The gallery works without it, but \"more like this\" (pixels) and the text read in pictures need it: xcode-select --install"

# ---- 2. Vault path
DEFAULT_VAULT="$("$PY" "$REPO/src/config.py" get vault)"
VAULT_SET=0; [ -n "$VAULT" ] && VAULT_SET=1
if [ -z "$VAULT" ] && [ "$INTERACTIVE" = 1 ]; then
  read -r -p "Vault folder [$DEFAULT_VAULT]: " VAULT || true
  [ -n "$VAULT" ] && VAULT_SET=1
fi
VAULT="${VAULT:-$DEFAULT_VAULT}"
VAULT="${VAULT/#\~/$HOME}"
case "$VAULT" in /*) ;; *) die "vault path must be absolute: $VAULT" ;; esac
mkdir -p "$VAULT"
VAULT="$(cd "$VAULT" && pwd -P)"   # the receiver refuses symlinked vault paths

# ---- 3. Vault skeleton (never overwrites)
for d in Inbox Library MOCs Collections Moodboards _attachments .obsidian; do mkdir -p "$VAULT/$d"; done
copy_if_absent() { [ -e "$2" ] || { cp "$1" "$2"; say "created $2"; }; }
for f in _taxonomy.yml "_Wiki Inspi.md" "_Wiki Inspi.base"; do copy_if_absent "$REPO/vault-skeleton/$f" "$VAULT/$f"; done
for f in app.json community-plugins.json; do copy_if_absent "$REPO/vault-skeleton/.obsidian/$f" "$VAULT/.obsidian/$f"; done

# ---- 4. Config (existing values win unless a flag or prompt answer overrides them)
"$PY" - "$REPO/src" "$VAULT" "$VAULT_SET" "$PORT" "$BACKEND" "$DROP" "$EXT_ID" "$NO_KEYCHAIN" "$TOKEN_FILE" <<'PYEOF'
import json, sys
sys.path.insert(0, sys.argv[1])
import config
vault, vault_set, port, backend, drop, ext, nokc, tfile = sys.argv[2:10]
p = config.config_path()
cur = {}
try: cur = json.loads(p.read_text())
except Exception: pass
if vault_set == "1" or "vault" not in cur: cur["vault"] = vault
if port: cur["port"] = int(port)
if backend: cur["backend"] = backend
if drop: cur["drop_folder"] = drop
if ext: cur["extension_id"] = ext
if nokc == "1": cur["token_store"] = "file"
if tfile: cur["token_file"] = tfile
for k in ("port", "backend", "drop_folder"): cur.setdefault(k, config.DEFAULTS[k])
p.parent.mkdir(parents=True, exist_ok=True)
p.write_text(json.dumps(cur, indent=2) + "\n")
PYEOF
if [ -n "$EXT_ID" ]; then [[ "$EXT_ID" =~ ^[a-p]{32}$ ]] || die "extension id must be 32 letters a-p"; fi
say "config:  $WIKI_INSPI_CONFIG"
cfg() { "$PY" "$REPO/src/config.py" get "$1"; }
LOG_DIR="$(cfg log_dir)"; DROP="$(cfg drop_folder)"; PORT="$(cfg port)"
mkdir -p "$LOG_DIR" "$DROP"

# ---- 5. Web Clipper templates, with this vault's name
CLIP_DIR="$CONF_DIR/clipper"; mkdir -p "$CLIP_DIR"
"$PY" - "$REPO/clipper" "$CLIP_DIR" "$(basename "$VAULT")" <<'PYEOF'
import json, sys
from pathlib import Path
src, dst, name = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]
for f in sorted(src.glob("*.json")):
    t = json.loads(f.read_text()); t["vault"] = name
    (dst / f.name).write_text(json.dumps(t, indent="\t", ensure_ascii=False) + "\n")
PYEOF
say "clipper: templates for vault \"$(basename "$VAULT")\" in $CLIP_DIR"

# ---- 6. Token (kept if it already exists; rotate with scripts/setup-token.sh)
if [ "$(cfg token_store)" = "file" ]; then
  if [ -s "$(cfg token_file)" ]; then say "token:   already stored in $(cfg token_file), kept"
  else PYTHON="$PY" "$REPO/scripts/setup-token.sh" --no-window; fi
else
  SERVICE="$(cfg keychain_service)"; ACCOUNT="${USER:-$(id -un)}"
  if security find-generic-password -a "$ACCOUNT" -s "$SERVICE" >/dev/null 2>&1; then
    say "token:   already in the Keychain (service $SERVICE), kept"
  else
    PYTHON="$PY" "$REPO/scripts/setup-token.sh" --no-window
  fi
fi

# ---- 7. `inspi` command
mkdir -p "$BIN_DIR"
cat > "$BIN_DIR/inspi" <<WRAP
#!/bin/sh
exec "$PY" "$REPO/bin/inspi" "\$@"
WRAP
chmod +x "$BIN_DIR/inspi"
say "command: $BIN_DIR/inspi"

# ---- 8. launchd agents
render() { # template -> LaunchAgents file
  "$PY" - "$1" "$2" "$PY" "$REPO" "$HOME" "$LOG_DIR" "$DROP" <<'PYEOF'
import sys
tpl, out, py, repo, home, logdir, drop = sys.argv[1:8]
t = open(tpl).read()
for k, v in (("@PYTHON@", py), ("@REPO@", repo), ("@HOME@", home), ("@LOGDIR@", logdir), ("@DROP@", drop)):
    t = t.replace(k, v.replace("&", "&amp;").replace("<", "&lt;"))
open(out, "w").write(t)
PYEOF
}
if [ "$LAUNCHD" = 1 ]; then
  AGENTS="$HOME/Library/LaunchAgents"; mkdir -p "$AGENTS"
  for name in receiver watcher enricher map; do
    plist="$AGENTS/$LABEL_PREFIX.$name.plist"
    render "$REPO/launchd/$LABEL_PREFIX.$name.plist.template" "$plist"
    launchctl bootout "gui/$(id -u)/$LABEL_PREFIX.$name" 2>/dev/null || true
    launchctl bootstrap "gui/$(id -u)" "$plist"
    say "launchd: loaded $LABEL_PREFIX.$name"
  done
  "$PY" "$REPO/bin/inspi" map --no-open --vision-limit 50 >/dev/null 2>&1 || say "gallery: first build failed, run: inspi map"
  MAP_PORT="$("$PY" "$REPO/src/config.py" get map_port 2>/dev/null || echo 18766)"
  APP="$("$REPO/scripts/make_app.sh" --label "$LABEL_PREFIX.map" --url "http://127.0.0.1:$MAP_PORT/")"
  say "app: $APP (drag it to the Dock; it opens the gallery on http://127.0.0.1:$MAP_PORT/)"
else
  say "launchd: skipped (--no-launchd). Run the receiver by hand: $PY $REPO/src/receiver.py"
fi

PAIRED="not paired yet; the extension id is learned at the first pairing"
[ -n "$(cfg extension_id)" ] && PAIRED="extension id pinned in the config"
cat <<MSG

Done. Vault: $VAULT   Receiver: 127.0.0.1:$PORT   ($PAIRED)

Next steps
 1. Chrome or Brave: chrome://extensions > Developer mode > Load unpacked > $REPO/extension
 2. $REPO/scripts/setup-token.sh   (opens a 60 s pairing window; add --extension-id <id> to pin the id)
    then: extension Details > Extension options > Pair, then Test.
 3. Plugins: $PY $REPO/scripts/install_plugins.py --vault "$VAULT"  (downloads Dataview, Note Gallery,
    Excalidraw from their official releases). Then open the folder as a vault in Obsidian and
    turn on Settings > Community plugins.
 4. Obsidian Web Clipper (optional): Settings > Templates > Import each file in $CLIP_DIR
 5. Right-click an image > "Send to Wiki Inspi". Drop folder: $DROP
 6. Classifier: backend "$(cfg backend)" (change in $WIKI_INSPI_CONFIG). claude-cli needs the
    Claude Code CLI logged in; anthropic-api needs ANTHROPIC_API_KEY in the launchd environment
    (edit the plists); ollama needs a local vision model.
 7. Check everything at any time: inspi doctor
 8. Search and boards: add $BIN_DIR to your PATH, then: inspi search "dark blue" / inspi board "query" --name my-board
MSG

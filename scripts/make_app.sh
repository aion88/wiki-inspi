#!/bin/bash
# make_app.sh: build "Wiki Inspi.app", an icon that opens the gallery in the default browser.
# The app only starts the local gallery server (launchd) if it is not answering, then opens it.
#
# Usage: scripts/make_app.sh [--dest DIR] [--label LAUNCHD_LABEL] [--url URL]
#   defaults: --dest /Applications (falls back to ~/Applications if not writable)
#             --label io.github.wiki-inspi.map   --url http://127.0.0.1:18766/
set -euo pipefail

HERE="$(cd "$(dirname "$0")/.." && pwd)"
DEST="/Applications"
LABEL="io.github.wiki-inspi.map"
URL="http://127.0.0.1:18766/"
while [ $# -gt 0 ]; do
  case "$1" in
    --dest) DEST="$2"; shift 2 ;;
    --label) LABEL="$2"; shift 2 ;;
    --url) URL="$2"; shift 2 ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
done
case "$URL" in http://127.0.0.1:*) ;; *) echo "--url must be http://127.0.0.1:<port>/" >&2; exit 2 ;; esac
[[ "$LABEL" =~ ^[A-Za-z0-9._-]+$ ]] || { echo "bad --label" >&2; exit 2; }
mkdir -p "$DEST" 2>/dev/null || true
if [ ! -w "$DEST" ]; then DEST="$HOME/Applications"; mkdir -p "$DEST"; fi

APP="$DEST/Wiki Inspi.app"
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
# Build the .icns from the PNG with the macOS tools (sips, iconutil); no binary icon in the repo.
SET="$(mktemp -d)/icon.iconset"; mkdir -p "$SET"
for s in 16 32 128 256 512; do
  sips -z $s $s "$HERE/assets/icon-1024.png" --out "$SET/icon_${s}x${s}.png" >/dev/null
  sips -z $((s * 2)) $((s * 2)) "$HERE/assets/icon-1024.png" --out "$SET/icon_${s}x${s}@2x.png" >/dev/null
done
iconutil -c icns "$SET" -o "$APP/Contents/Resources/icon.icns"
rm -rf "$(dirname "$SET")"
cat > "$APP/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key><string>Wiki Inspi</string>
  <key>CFBundleDisplayName</key><string>Wiki Inspi</string>
  <key>CFBundleIdentifier</key><string>io.github.wiki-inspi.launcher</string>
  <key>CFBundleVersion</key><string>1.0</string>
  <key>CFBundleShortVersionString</key><string>1.0</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleExecutable</key><string>wiki-inspi</string>
  <key>CFBundleIconFile</key><string>icon</string>
  <key>NSHighResolutionCapable</key><true/>
</dict>
</plist>
PLIST
cat > "$APP/Contents/MacOS/wiki-inspi" <<LAUNCH
#!/bin/bash
URL="$URL"
LABEL="$LABEL"
up() { curl -s -o /dev/null -m 2 -w '%{http_code}' "\$URL" | grep -q '^2'; }
if ! up; then
  launchctl kickstart -k "gui/\$(id -u)/\$LABEL" >/dev/null 2>&1 \\
    || launchctl bootstrap "gui/\$(id -u)" "\$HOME/Library/LaunchAgents/\$LABEL.plist" 2>/dev/null
  for _ in \$(seq 1 20); do up && break; sleep 0.5; done
fi
if up; then open "\$URL"
else osascript -e 'display notification "the gallery server does not answer: run inspi doctor" with title "Wiki Inspi"'
fi
LAUNCH
chmod +x "$APP/Contents/MacOS/wiki-inspi"
touch "$APP"
echo "$APP"

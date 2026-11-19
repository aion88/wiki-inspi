#!/bin/bash
# watcher.sh: move new images/videos from the drop folder into <vault>/_attachments with a stub
# note in Inbox/, then trigger enrichment. Run by launchd (WatchPaths on the drop folder).
# Folder and vault come from ~/.config/wiki-inspi/config.json.

set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
PYTHON="${PYTHON:-python3}"
cfg() { "$PYTHON" "$HERE/../src/config.py" get "$1"; }

DROP="${WIKI_INSPI_DROP:-$(cfg drop_folder)}"
VAULT="${INSPI_VAULT:-$(cfg vault)}"
INBOX="$VAULT/Inbox"
ATTACH="$VAULT/_attachments"
LOG_DIR="$(cfg log_dir)"
LOG="$LOG_DIR/watcher.log"

mkdir -p "$LOG_DIR" "$DROP"
mkdir -p "$INBOX" "$ATTACH"

today="$(date +%F)"
moved=0

shopt -s nullglob
for f in "$DROP"/*.{png,jpg,jpeg,webp,gif,mp4,mov,webm,PNG,JPG,JPEG,WEBP,GIF,MP4,MOV,WEBM}; do
  [ -f "$f" ] || continue
  base="$(basename "$f")"
  # strip extension + slugify
  name="${base%.*}"
  ext="${base##*.}"
  ext_lower="$(echo "$ext" | tr '[:upper:]' '[:lower:]')"
  slug="$(echo "$name" | tr '[:upper:] ' '[:lower:]-' | sed 's/[^a-z0-9-]//g' | sed 's/--*/-/g' | sed 's/^-//;s/-$//')"
  [ -z "$slug" ] && slug="capture-$(date +%s)"

  # uniqueness guard
  target_asset="$ATTACH/${slug}.${ext_lower}"
  target_stub="$INBOX/${slug}.md"
  i=2
  while [ -e "$target_asset" ] || [ -e "$target_stub" ]; do
    target_asset="$ATTACH/${slug}-${i}.${ext_lower}"
    target_stub="$INBOX/${slug}-${i}.md"
    i=$((i+1))
  done

  mv "$f" "$target_asset"
  thumb_rel="_attachments/$(basename "$target_asset")"

  cat > "$target_stub" <<STUB
---
source_url:
captured: $today
ingest_source: watcher
thumbnail: $thumb_rel
---

# $name

![[$thumb_rel]]

## Origin

- Dropped via the drop folder on $today.
STUB

  echo "[$(date -Iseconds)] moved $base → $thumb_rel" >> "$LOG"
  moved=$((moved+1))
done

# Enrichment is triggered by the capture itself. enrich.sh is lock-guarded and queues a rerun
# if a batch is in flight. --limit caps the pass.
if [ "$moved" -gt 0 ] && [ -x "$HERE/enrich.sh" ]; then
  echo "[$(date -Iseconds)] triggering enrich ($moved new)" >> "$LOG"
  INSPI_VAULT="$VAULT" nohup "$HERE/enrich.sh" --limit "${ENRICH_LIMIT:-10}" >/dev/null 2>&1 &
  disown
fi

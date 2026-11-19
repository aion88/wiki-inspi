#!/bin/bash
# enrich.sh: run the Wiki Inspi classifier on <vault>/Inbox/.
# Called by the receiver (on each capture), by watcher.sh, and by the hourly launchd safety net.
#
# Usage: enrich.sh [enrich.py args]       default: --limit ${ENRICH_LIMIT:-10}
#        enrich.sh --one "<path in Inbox>"  one note (capture-triggered)
#
# Settings come from ~/.config/wiki-inspi/config.json (vault, backend, model); see README.
# Mutex via mkdir-lock. A run that finds the lock held leaves a RERUN marker; the holder does
# another pass when it finishes (max 3), so a capture that lands mid-run is not stranded.
# Log: "done" ONLY when enrich.py exited 0. Any failed item => "partial/fail" and exit non-zero.

set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
SRC="$HERE/../src"
PYTHON="${PYTHON:-python3}"
cfg() { "$PYTHON" "$SRC/config.py" get "$1"; }

VAULT="${INSPI_VAULT:-$(cfg vault)}"
export INSPI_VAULT="$VAULT"
INBOX="$VAULT/Inbox"
LOG_DIR="${WIKI_INSPI_LOG_DIR:-$(cfg log_dir)}"
LOG="$LOG_DIR/enrich.log"
LOCK="${ENRICH_LOCK:-${TMPDIR:-/tmp}/wiki-inspi-enrich.lock}"
RERUN="${LOCK}.rerun"
ENRICHER="${ENRICHER:-$SRC/enrich.py}"
MAX_PASSES=3

mkdir -p "$LOG_DIR"
log() { echo "[$(date -Iseconds)] $*" >> "$LOG"; }

[ -d "$INBOX" ] || { log "skip: inbox dir missing: $INBOX"; exit 0; }

# Atomic mkdir lock. A lock whose pid is dead (killed by the watchdog, reboot) is stale: clear it.
if ! mkdir "$LOCK" 2>/dev/null; then
  holder="$(cat "$LOCK/pid" 2>/dev/null || true)"
  if [ -n "$holder" ] && ! kill -0 "$holder" 2>/dev/null; then
    log "stale lock from dead pid $holder, clearing"
    rm -rf "$LOCK"
    mkdir "$LOCK" 2>/dev/null || { touch "$RERUN"; log "skip: lock re-taken"; exit 0; }
  else
    touch "$RERUN"
    log "skip: lock held by pid ${holder:-?} (rerun queued)"
    exit 0
  fi
fi
echo $$ > "$LOCK/pid"
trap 'rm -rf "$LOCK"' EXIT

if [ "$#" -eq 0 ]; then
  set -- --limit "${ENRICH_LIMIT:-10}"
fi

run_once() {
  local count out rc summary
  count=$(find "$INBOX" -maxdepth 1 -type f ! -name '.*' 2>/dev/null | wc -l | tr -d ' ')
  if [ "$count" -eq 0 ]; then
    log "skip: inbox clear"
    return 0
  fi
  log "start: $count unprocessed items (args: $*)"
  out="$(mktemp -t wiki-inspi-enrich-out)"
  # 15-min watchdog (macOS has no /usr/bin/timeout). `set +m` keeps the kill silent.
  set +m
  "$PYTHON" "$ENRICHER" "$@" > "$out" 2>&1 &
  local enrich_pid=$!
  ( sleep 900 && kill -9 "$enrich_pid" 2>/dev/null && log "killed: 15min timeout" ) 2>/dev/null &
  local watchdog_pid=$!
  rc=0
  wait "$enrich_pid" || rc=$?
  kill "$watchdog_pid" 2>/dev/null || true
  wait "$watchdog_pid" 2>/dev/null || true
  cat "$out" >> "$LOG"
  summary="$(grep '^Summary:' "$out" | tail -1 || true)"
  rm -f "$out"
  if [ "$rc" -eq 0 ]; then
    log "done: ${summary:-batch complete}"
  else
    log "partial/fail: enrich.py exited $rc ${summary:+($summary)}"
  fi
  return "$rc"
}

pass=0
final=0
while [ "$pass" -lt "$MAX_PASSES" ]; do
  rm -f "$RERUN"
  pass=$((pass + 1))
  rc=0
  run_once "$@" || rc=$?
  [ "$rc" -ne 0 ] && final=$rc
  if [ -f "$RERUN" ] && [ "$rc" -eq 0 ]; then
    continue
  fi
  break
done
exit "$final"

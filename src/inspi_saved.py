"""Saved searches of the gallery, and the boards that fill themselves.

<vault>/.wiki-inspi/saved.json: [{name, query, facets, color, tol, board}].
Same matching rules as inspi_filter.js (one fixture file tests both).
A search with board=true owns Moodboards/auto-<name>.excalidraw.md: created on the first pass, then
only NEW matches are appended. Every picture ever added is remembered (saved-ledger.json) so a
picture she removed from the board never comes back. A board edited in the last 2 minutes is skipped
(Excalidraw may hold it open). All writes happen under the vault write lock.
"""

from __future__ import annotations

import contextlib
import fcntl
import json
import math
import re
import time
from pathlib import Path

import inspilib as L

MAX_ADD = 60
FRESH_EDIT = 120
FACET_KEYS = ("ty", "te", "su", "co", "tp")


@contextlib.contextmanager
def write_lock(vault: Path):
    p = vault / ".wiki-inspi" / "write.lock"
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


def _lab(hexc):
    m = re.fullmatch(r"#?([0-9a-fA-F]{6})", str(hexc or "").strip())
    return L.rgb_to_lab(L.hex_to_rgb("#" + m.group(1))) if m else None


def color_dist(it: dict, hexc: str):
    t = _lab(hexc)
    if not t:
        return None
    ds = [math.dist(l, t) for l in (_lab(c) for c in [it.get("c"), *(it.get("pal") or [])]) if l]
    return min(ds) if ds else None


def query_ok(it, q, fam_label=""):
    q = str(q or "").strip().lower()
    if not q:
        return True
    fv = " ".join(v for vs in (it.get("fa") or {}).values() for v in vs)
    hay = " ".join([it.get("t", ""), it.get("b", ""), " ".join(it.get("s") or []), " ".join(it.get("m") or []),
                    fv, fam_label or "", it.get("txt", "")]).lower()
    return all(w in hay for w in q.split())


def facets_ok(it, facets):
    for k, want in (facets or {}).items():
        if want and not any(v in want for v in (it.get("fa") or {}).get(k, [])):
            return False
    return True


def saved_ok(it, s, fam_label=""):
    if not query_ok(it, s.get("query"), fam_label) or not facets_ok(it, s.get("facets")):
        return False
    if s.get("color"):
        d = color_dist(it, s["color"])
        return d is not None and d <= float(s.get("tol") or 25)
    return True


# ---------------------------------------------------------------- storage

def path(vault: Path) -> Path:
    return vault / ".wiki-inspi" / "saved.json"


def load(vault: Path) -> list[dict]:
    try:
        data = json.loads(path(vault).read_text())
        return data if isinstance(data, list) else []
    except (OSError, ValueError):
        return []


def clean(s: dict) -> dict:
    name = L.safe_board_name(str(s.get("name") or "")[:75]).strip("-_")[:75]
    if not name:
        raise ValueError("bad name")
    facets = {k: [str(v)[:60] for v in (s.get("facets") or {}).get(k, [])][:20] for k in FACET_KEYS}
    color = str(s.get("color") or "")
    if color and not re.fullmatch(r"#[0-9a-fA-F]{6}", color):
        raise ValueError("bad color")
    tol = float(s.get("tol") or 25)
    if not 1 <= tol <= 100:
        raise ValueError("bad tolerance")
    return {"name": name, "query": str(s.get("query") or "")[:200], "facets": {k: v for k, v in facets.items() if v},
            "color": color.lower(), "tol": tol, "board": bool(s.get("board"))}


def put(vault: Path, s: dict) -> list[dict]:
    s = clean(s)
    with write_lock(vault):
        lst = [x for x in load(vault) if x.get("name") != s["name"]] + [s]
        path(vault).write_text(json.dumps(lst, ensure_ascii=False, indent=1))
    return lst


def remove(vault: Path, name: str) -> list[dict]:
    with write_lock(vault):
        lst = [x for x in load(vault) if x.get("name") != name]
        path(vault).write_text(json.dumps(lst, ensure_ascii=False, indent=1))
    return lst


# ---------------------------------------------------------------- auto boards

def _ledger(vault):
    p = vault / ".wiki-inspi" / "saved-ledger.json"
    try:
        return p, json.loads(p.read_text())
    except (OSError, ValueError):
        return p, {}


def fill_boards(vault: Path, items: list[dict], fam_labels: dict, notes_by_rel: dict, log=None) -> list[str]:
    """Append new matches of each board=true search to its auto- board. Caller holds the write lock."""
    import inspi_board_add as BA

    report = []
    lp, ledger = _ledger(vault)
    for s in load(vault):
        if not s.get("board"):
            continue
        try:
            line = _fill_one(vault, s, items, fam_labels, notes_by_rel, ledger, BA)
        except Exception as e:  # one broken board never stops the others
            line = f"auto-{s.get('name')}: error {e}"
        if line:
            report.append(line)
        lp.write_text(json.dumps(ledger))  # after each board: a later failure cannot undo it
    if log and report:
        with (vault / ".wiki-inspi" / "saved.log").open("a") as fh:
            fh.write(time.strftime("%Y-%m-%d %H:%M:%S ") + "; ".join(report) + "\n")
    return report


def _fill_one(vault, s, items, fam_labels, notes_by_rel, ledger, BA):
    name = "auto-" + s["name"]
    board = vault / "Moodboards" / f"{name}.excalidraw.md"
    done = set(ledger.get(name, []))
    notes = []
    for it in items:
        if it.get("f") == -2 or not saved_ok(it, s, fam_labels.get(it.get("f"), "")):
            continue
        n = notes_by_rel.get(it["r"])
        if not n or not n["image"]:
            continue
        sha = it.get("sha") or ""
        if sha and sha not in done:
            notes.append((sha, n))
        if len(notes) >= MAX_ADD:
            break
    if not notes:
        return ""
    if board.exists() and time.time() - board.stat().st_mtime < FRESH_EDIT:  # checked right before writing
        return f"{name}: skipped, edited less than 2 minutes ago"
    if board.exists():
        _, added, _ = BA.append(vault, name, [n for _, n in notes])
    else:
        _, added, _ = L.write_board(vault, name, [n for _, n in notes], title=s["name"])
    ledger[name] = sorted(done | {sha for sha, _ in notes})
    return f"{name}: +{added}"

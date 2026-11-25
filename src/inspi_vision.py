"""Visual fingerprint + text of each picture, read locally by macOS Vision (no model download).

`inspi_vision.swift` is compiled once to `~/.local/bin/inspi-vision` (rebuilt when the source is newer).
Cache: <vault>/.wiki-inspi/vision.jsonl, one JSON line per picture keyed by the sha1 of its bytes:
{sha, fp (base64 float32), n, rev, txt}. Rows of another size or revision are ignored.
"""

from __future__ import annotations

import base64
import hashlib
import json
import subprocess
from pathlib import Path

SRC = Path(__file__).with_name("inspi_vision.swift")
BIN = Path.home() / ".local" / "bin" / "inspi-vision"
REV, DIM = 2, 768


def binary() -> Path:
    if not BIN.exists() or BIN.stat().st_mtime < SRC.stat().st_mtime:
        BIN.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["swiftc", "-O", str(SRC), "-o", str(BIN)], check=True, capture_output=True)
    return BIN


def cache_path(vault: Path) -> Path:
    return vault / ".wiki-inspi" / "vision.jsonl"


def sha1(p: Path) -> str | None:
    try:
        return hashlib.sha1(p.read_bytes()).hexdigest()
    except OSError:
        return None


def load(vault: Path) -> dict[str, dict]:
    out = {}
    cp = cache_path(vault)
    if not cp.exists():
        return out
    for line in cp.read_text().splitlines():
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if r.get("sha") and r.get("rev", REV) == REV and (r.get("n") in (None, DIM)):
            out[r["sha"]] = r
    return out


def fill(vault: Path, images: list[Path], limit: int | None = None) -> int:
    """Read the pictures not in the cache yet (at most `limit`). Returns how many were read."""
    have = load(vault)
    todo: dict[str, str] = {}
    for p in images:
        s = sha1(p)
        if s and s not in have and s not in todo.values():
            todo[str(p)] = s
        if limit is not None and len(todo) >= limit:
            break
    if not todo:
        return 0
    try:
        res = subprocess.run([str(binary())], input="\n".join(todo) + "\n", capture_output=True, text=True, timeout=1800)
    except (OSError, subprocess.SubprocessError):
        return 0
    cp = cache_path(vault)
    cp.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with cp.open("a") as fh:
        for line in res.stdout.splitlines():
            try:
                r = json.loads(line)
            except ValueError:
                continue
            r["sha"] = todo.get(r.pop("p", ""))
            if r["sha"]:
                r.setdefault("rev", REV)
                fh.write(json.dumps(r) + "\n")
                n += 1
    return n


def vector(row: dict):
    import numpy as np

    if not row or "fp" not in row:
        return None
    v = np.frombuffer(base64.b64decode(row["fp"]), dtype=np.float32)
    return v if v.size == DIM else None


def neighbours(vecs: list, k: int = 30) -> list[list[list]]:
    """For each vector (or None), the k nearest others as [[index, similarity 0..1]]."""
    import numpy as np

    idx = [i for i, v in enumerate(vecs) if v is not None]
    out: list[list[list]] = [[] for _ in vecs]
    if len(idx) < 2:
        return out
    M = np.stack([vecs[i] for i in idx])
    M = M / (np.linalg.norm(M, axis=1, keepdims=True) + 1e-9)
    with np.errstate(all="ignore"):  # numpy 2.0 + Accelerate warns spuriously on finite input
        S = M.astype(np.float64) @ M.astype(np.float64).T  # cosine; distance = sqrt(2 - 2 cos)
    S = np.nan_to_num(S, nan=-1.0, posinf=-1.0, neginf=-1.0)
    np.fill_diagonal(S, -2)
    for r, i in enumerate(idx):
        top = np.argpartition(-S[r], min(k, len(idx) - 1) - 1)[:k]
        top = top[np.argsort(-S[r][top])]
        out[i] = [[idx[c], round(float(max(0.0, 1 - np.sqrt(max(0.0, 2 - 2 * S[r][c])) / 2)), 3)] for c in top]
    return out

"""Append images to an existing Excalidraw moodboard without touching what is already on it.

The board may be plain json or Excalidraw's compressed-json (LZString base64). The existing
elements stay exactly as they are; the new images go in a block below the lowest element.
A copy of the board is saved before writing.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import time
import uuid
from pathlib import Path

import inspilib as L

BACKUP = Path.home() / ".local" / "state" / "wiki-inspi" / "board-backups"
_KEY = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/="
DRAW_RE = re.compile(r"```(compressed-json|json)\n(.*?)\n```", re.S)


def lz_decompress_b64(s: str) -> str:
    """LZString.decompressFromBase64 (pieroxy/lz-string 1.4.4)."""
    s = re.sub(r"\s+", "", s)
    if not s:
        return ""
    vals = [_KEY.index(c) for c in s]
    pos, val, idx = 32, vals[0], 1

    def bits(n):
        nonlocal pos, val, idx
        res, p = 0, 1
        for _ in range(n):
            resb = val & pos
            pos >>= 1
            if pos == 0:
                pos = 32
                val = vals[idx] if idx < len(vals) else 0
                idx += 1
            if resb:
                res |= p
            p <<= 1
        return res

    d = {0: 0, 1: 1, 2: 2}
    enlarge, dsize, nbits = 4, 4, 3
    c = bits(2)
    if c == 2:
        return ""
    w = chr(bits(8 if c == 0 else 16))
    d[3] = w
    out = [w]
    while True:
        if idx > len(vals) + 1:
            return "".join(out)
        c = bits(nbits)
        if c in (0, 1):
            d[dsize] = chr(bits(8 if c == 0 else 16))
            dsize += 1
            c = dsize - 1
            enlarge -= 1
        elif c == 2:
            return "".join(out)
        if enlarge == 0:
            enlarge = 2**nbits
            nbits += 1
        if c in d:
            entry = d[c]
        elif c == dsize:
            entry = w + w[0]
        else:
            raise ValueError("corrupt compressed drawing")
        out.append(entry)
        d[dsize] = w + entry[0]
        dsize += 1
        enlarge -= 1
        w = entry
        if enlarge == 0:
            enlarge = 2**nbits
            nbits += 1


def list_boards(vault: Path) -> list[dict]:
    out = []
    for f in sorted(
        (vault / "Moodboards").glob("*.excalidraw.md"), key=lambda p: -p.stat().st_mtime
    ):
        slug = f.name[: -len(".excalidraw.md")]
        n = len(re.findall(r"^[0-9a-f]{40}: \[\[", f.read_text(errors="ignore"), re.M))
        out.append({"name": slug, "images": n})
    return out


def read_drawing(text: str) -> tuple[dict, re.Match]:
    m = DRAW_RE.search(text)
    if not m:
        raise ValueError("no drawing block in this board")
    raw = m.group(2)
    data = json.loads(
        lz_decompress_b64(raw) if m.group(1) == "compressed-json" else raw
    )
    return data, m


def append(vault: Path, name: str, notes: list[dict]) -> tuple[Path, int, int]:
    slug = L.safe_board_name(name)
    path = vault / "Moodboards" / f"{slug}.excalidraw.md"
    if not path.is_file():
        raise ValueError(f"no board named {slug}")
    text = path.read_text(encoding="utf-8")
    data, m = read_drawing(text)
    have = set(re.findall(r"^([0-9a-f]{40}): \[\[", text, re.M))
    have |= {e.get("fileId") for e in data.get("elements", []) if e.get("fileId")}
    fresh = []
    for n in notes:
        if not n["image"]:
            continue
        fid = hashlib.sha1(n["image"].read_bytes()).hexdigest()
        if fid not in have:
            have.add(fid)
            fresh.append(n)
    if not fresh:
        return path, 0, len(notes)
    built, _ = L.build_board(fresh, vault, "x")
    new_draw, _ = read_drawing(built)
    live = [e for e in data.get("elements", []) if not e.get("isDeleted")]
    bottom = max((e.get("y", 0) + e.get("height", 0) for e in live), default=0)
    left = min((e.get("x", 0) for e in live), default=120)
    tag = uuid.uuid4().hex[:6]
    add = []
    for e in new_draw["elements"]:
        if e["id"] in ("hdr", "sub") or e["id"].startswith("pal"):
            continue
        e = dict(e)
        e["id"] = f"{e['id']}-{tag}"
        e["y"] = e["y"] - 230 + bottom + 120
        e["x"] = e["x"] - 120 + left
        if e.get("containerId"):
            e["containerId"] = f"{e['containerId']}-{tag}"
        add.append(e)
    data["elements"] = data.get("elements", []) + add
    emb = "".join(
        f"{fid}: [[{rel}]]\n\n"
        for fid, rel in re.findall(r"^([0-9a-f]{40}): \[\[([^\]]+)\]\]", built, re.M)
    )
    head = text[: m.start()]
    marker = "## Embedded Files\n"
    if marker in head:
        i = head.index(marker) + len(marker)
        head = head[:i] + emb + head[i:]
    else:
        head = head.replace("%%\n## Drawing", marker + emb + "%%\n## Drawing", 1)
    body = "```json\n" + json.dumps(data, indent=2, ensure_ascii=False) + "\n```"
    BACKUP.mkdir(parents=True, exist_ok=True)
    shutil.copy2(
        path, BACKUP / f"{slug}.{time.strftime('%Y%m%d-%H%M%S')}.excalidraw.md"
    )
    path.write_text(head + body + text[m.end() :], encoding="utf-8")
    return path, len(fresh), len(notes) - len(fresh)

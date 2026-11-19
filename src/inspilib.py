"""inspilib: search and moodboard generation for the Wiki Inspi vault.

Stdlib + PyYAML (+ PIL for image ratios, optional: falls back to a square).
The vault path is a parameter everywhere, so tests run on a fixture vault.
"""

from __future__ import annotations

import colorsys
import hashlib
import json
import math
import re
import sys
import urllib.parse
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
import config as _config  # noqa: E402

VAULT = Path(_config.load()["vault"])
NOTE_DIRS = ("Library",)
HEX_RE = re.compile(r"^#?([0-9a-fA-F]{6})$")
EMBED_RE = re.compile(r"!\[\[([^\]|#]+)")

# Basic color names -> hex, for `--color blue`. Lab distance does the rest.
COLOR_NAMES = {
    "black": "#0a0a0a",
    "white": "#f5f5f5",
    "gray": "#808080",
    "grey": "#808080",
    "red": "#d32f2f",
    "orange": "#f57c00",
    "yellow": "#fdd835",
    "gold": "#d4a017",
    "green": "#388e3c",
    "lime": "#9ccc3a",
    "teal": "#00897b",
    "cyan": "#26c6da",
    "blue": "#1e66d0",
    "navy": "#0d1b4c",
    "dark blue": "#0d2a5c",
    "purple": "#7b3fa0",
    "violet": "#7e57c2",
    "pink": "#ec6fa0",
    "magenta": "#c2188f",
    "brown": "#6d4c41",
    "beige": "#d9c7a3",
    "cream": "#f3ead2",
    "olive": "#6b7a2a",
    "maroon": "#6a1b2a",
}

# ----------------------------------------------------------------- colors


def parse_color(value: str) -> str:
    """'#1e66d0' | '1e66d0' | 'blue' -> '#1e66d0'. Raises ValueError."""
    v = value.strip().lower()
    m = HEX_RE.match(v)
    if m:
        return "#" + m.group(1)
    if v in COLOR_NAMES:
        return COLOR_NAMES[v]
    raise ValueError(
        f"unknown color {value!r}: use #rrggbb or one of {', '.join(sorted(COLOR_NAMES))}"
    )


def hex_to_rgb(h: str) -> tuple[int, int, int]:
    h = h.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def _lin(c: float) -> float:
    c /= 255.0
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def rgb_to_lab(rgb: tuple[int, int, int]) -> tuple[float, float, float]:
    r, g, b = (_lin(x) for x in rgb)
    x = (0.4124564 * r + 0.3575761 * g + 0.1804375 * b) / 0.95047
    y = 0.2126729 * r + 0.7151522 * g + 0.0721750 * b
    z = (0.0193339 * r + 0.1191920 * g + 0.9503041 * b) / 1.08883

    def f(t: float) -> float:
        return t ** (1 / 3) if t > 0.008856 else 7.787 * t + 16 / 116

    fx, fy, fz = f(x), f(y), f(z)
    return 116 * fy - 16, 500 * (fx - fy), 200 * (fy - fz)


def color_distance(a: str, b: str) -> float:
    """CIE76 delta E between two hex colors (0 = identical, ~100 = black vs white)."""
    la, lb = rgb_to_lab(hex_to_rgb(a)), rgb_to_lab(hex_to_rgb(b))
    return math.sqrt(sum((p - q) ** 2 for p, q in zip(la, lb)))


# ------------------------------------------------------------------ notes


def _norm(s) -> str:
    return re.sub(r"[\s_\-]+", " ", str(s).lower()).strip()


def _as_list(v) -> list:
    if v is None:
        return []
    if isinstance(v, (list, tuple)):
        return [x for x in v if x is not None and str(x).strip() != ""]
    s = str(v).strip()
    return [s] if s else []


def _valid_hex(v) -> str | None:
    m = HEX_RE.match(str(v).strip()) if v else None
    return "#" + m.group(1).lower() if m else None


def read_frontmatter(path: Path) -> tuple[dict, str]:
    text = path.read_text(encoding="utf-8", errors="replace")
    if not text.startswith("---"):
        return {}, text
    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}, text
    try:
        fm = yaml.safe_load(parts[1]) or {}
    except yaml.YAMLError:
        return {}, parts[2]
    return (fm if isinstance(fm, dict) else {}), parts[2]


def _why(body: str) -> str:
    m = re.search(r"^##\s+Why\s*\n(.*?)(?=^##\s|\Z)", body, re.S | re.M)
    return m.group(1).strip() if m else ""


def _title(fm: dict, body: str, stem: str) -> str:
    m = re.search(r"^#\s+(.+)$", body, re.M)
    return (m.group(1).strip() if m else None) or str(fm.get("title") or stem)


def _colors(fm: dict) -> list[str]:
    """Palette hexes, v2 `palette` first, then v1 `dominant_colors`, deduplicated."""
    out: list[str] = []
    for item in _as_list(fm.get("palette")):
        h = _valid_hex(item.get("hex") if isinstance(item, dict) else item)
        if h:
            out.append(h)
    for item in _as_list(fm.get("dominant_colors")):
        h = _valid_hex(item)
        if h:
            out.append(h)
    mc = _valid_hex(fm.get("main_color"))
    if mc:
        out.insert(0, mc)
    seen: set[str] = set()
    return [c for c in out if not (c in seen or seen.add(c))]


def _palette_weighted(fm: dict) -> list[tuple[str, float]]:
    pal = []
    for item in _as_list(fm.get("palette")):
        if isinstance(item, dict):
            h = _valid_hex(item.get("hex"))
            if h:
                try:
                    share = float(item.get("share") or 0.2)
                except (TypeError, ValueError):
                    share = 0.2
                pal.append((h, share))
    if pal:
        return pal
    dc = [h for h in (_valid_hex(x) for x in _as_list(fm.get("dominant_colors"))) if h]
    return [(h, 1.0 / max(len(dc), 1)) for h in dc]


def resolve_image(vault: Path, fm: dict, body: str) -> Path | None:
    """Local image: `thumbnail` first, else the first ![[embed]] that is a file."""
    cands = []
    if fm.get("thumbnail"):
        cands.append(str(fm["thumbnail"]))
    cands += EMBED_RE.findall(body)
    for c in cands:
        p = (vault / c).resolve()
        try:
            p.relative_to(vault.resolve())
        except ValueError:
            continue  # confined under the vault
        if p.is_file():
            return p
    return None


def load_notes(vault: Path = VAULT, dirs=NOTE_DIRS) -> list[dict]:
    notes = []
    for d in dirs:
        for path in sorted((vault / d).glob("*.md")):
            fm, body = read_frontmatter(path)
            if not fm:
                continue
            notes.append(
                {
                    "path": path,
                    "rel": str(path.relative_to(vault)),
                    "title": _title(fm, body, path.stem),
                    "fm": fm,
                    "why": _why(body),
                    "image": resolve_image(vault, fm, body),
                    "colors": _colors(fm),
                    "palette": _palette_weighted(fm),
                }
            )
    return notes


# ----------------------------------------------------------------- search

FIELD_WEIGHTS = {  # text-match fields and their weight
    "title": 3,
    "tags": 2,
    "style": 2,
    "movement": 2,
    "mood": 2,
    "technique": 2,
    "subject": 1,
    "why": 1,
    "author": 2,
    "medium_detail": 1,
    "texture": 1,
    "lighting": 1,
    "composition": 1,
    "references": 1,
    "era": 1,
}
LIST_FILTERS = (
    "style",
    "mood",
    "technique",
    "author",
    "domain",
    "use_for",
    "tags",
    "medium",
)


def _values(note: dict, field: str) -> list[str]:
    if field == "title":
        return [note["title"]]
    if field == "why":
        return [note["why"]] if note["why"] else []
    return [str(v) for v in _as_list(note["fm"].get(field))]


def text_score(note: dict, tokens: list[str]) -> tuple[int, int]:
    """(tokens matched, weighted score). A token matches if it is a substring of any field."""
    if not tokens:
        return 0, 0
    fields = {f: _norm(" ".join(_values(note, f))) for f in FIELD_WEIGHTS}
    matched = score = 0
    for t in tokens:
        hit = False
        for f, w in FIELD_WEIGHTS.items():
            if t in fields[f]:
                score += w
                hit = True
        matched += hit
    return matched, score


RANK_PENALTY = 6.0  # per palette position: main/first color counts more than the 5th


def best_color_distance(note: dict, target: str, penalized: bool = False) -> float | None:
    """Nearest palette color (delta E). penalized=True adds a penalty by palette rank, for sorting."""
    if not note["colors"]:
        return None
    return min(color_distance(c, target) + (RANK_PENALTY * i if penalized else 0.0)
               for i, c in enumerate(note["colors"]))


def search(
    notes: list[dict],
    query: str = "",
    color: str | None = None,
    filters: dict[str, str] | None = None,
    limit: int = 20,
    color_threshold: float = 28.0,
) -> list[dict]:
    """Filter, rank, return result dicts (no Path objects: JSON-safe)."""
    tokens = [t for t in _norm(query).split() if t]
    need = math.ceil(len(tokens) / 2) if tokens else 0
    target = parse_color(color) if color else None
    results = []
    for n in notes:
        ok = True
        for field, want in (filters or {}).items():
            if not want:
                continue
            w = _norm(want)
            if not any(w in _norm(v) for v in _values(n, field)):
                ok = False
                break
        if not ok:
            continue
        matched, score = text_score(n, tokens)
        if tokens and matched < need:
            continue
        dist = None
        if target:
            dist = best_color_distance(n, target)
            if dist is None or dist > color_threshold:
                continue
        rank = best_color_distance(n, target, True) if target else None
        results.append(
            {"note": n, "matched": matched, "score": score, "distance": dist, "rank": rank}
        )
    if target:
        results.sort(
            key=lambda r: (r["rank"], -r["matched"], -r["score"], r["note"]["rel"])
        )
    else:
        results.sort(key=lambda r: (-r["matched"], -r["score"], r["note"]["rel"]))
    return results[:limit] if limit else results


def result_json(r: dict, vault: Path) -> dict:
    n, fm = r["note"], r["note"]["fm"]
    return {
        "path": str(n["path"]),
        "rel": n["rel"],
        "title": n["title"],
        "image": str(n["image"]) if n["image"] else None,
        "style": _as_list(fm.get("style")),
        "mood": _as_list(fm.get("mood")),
        "tags": _as_list(fm.get("tags")),
        "author": fm.get("author"),
        "main_color": _valid_hex(fm.get("main_color"))
        or (n["colors"][0] if n["colors"] else None),
        "colors": n["colors"],
        "score": r["score"],
        "matched_tokens": r["matched"],
        "color_distance": None if r["distance"] is None else round(r["distance"], 1),
        "source_url": fm.get("source_url") or None,
    }


def obsidian_uri(vault_name: str, rel: str) -> str:
    return f"obsidian://open?vault={urllib.parse.quote(vault_name)}&file={urllib.parse.quote(rel.removesuffix('.md'))}"


# ------------------------------------------------------------------ board

SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,80}$")


def safe_board_name(name: str) -> str:
    s = re.sub(r"[^a-z0-9_-]+", "-", name.strip().lower()).strip("-")
    if not SLUG_RE.match(s):
        raise ValueError(f"invalid board name {name!r}: use letters, digits, - and _")
    return s


def image_size(path: Path) -> tuple[int, int]:
    try:
        from PIL import Image

        with Image.open(path) as im:
            return im.size
    except Exception:
        return 1, 1


def _el(kind: str, eid: str, x, y, w, h, **extra) -> dict:
    seed = int(hashlib.sha1(eid.encode()).hexdigest()[:8], 16)
    base = {
        "angle": 0,
        "strokeColor": "#181A26",
        "backgroundColor": "transparent",
        "fillStyle": "solid",
        "strokeWidth": 1,
        "strokeStyle": "solid",
        "roughness": 0,
        "opacity": 100,
        "groupIds": [],
        "frameId": None,
        "roundness": None,
        "seed": seed,
        "version": 1,
        "versionNonce": seed ^ 0x5F5F5F,
        "isDeleted": False,
        "boundElements": None,
        "updated": 1,
        "link": None,
        "locked": False,
        "id": eid,
        "type": kind,
        "x": round(x),
        "y": round(y),
        "width": round(w),
        "height": round(h),
    }
    base.update(extra)
    return base


def _text(eid, x, y, w, text, size=13, color="#181A26") -> dict:
    text = " ".join(str(text).split())
    lines = max(1, math.ceil(len(text) * size * 0.55 / w))
    h = round(lines * size * 1.25)
    return _el(
        "text",
        eid,
        x,
        y,
        w,
        h,
        strokeColor=color,
        fontSize=size,
        fontFamily=2,
        text=text,
        textAlign="left",
        verticalAlign="top",
        baseline=round(size * 0.85),
        containerId=None,
        originalText=text,
        lineHeight=1.25,
        autoResize=False,
    )


def _source_host(url) -> str:
    try:
        return urllib.parse.urlparse(str(url)).netloc.removeprefix("www.")
    except Exception:
        return ""


def merge_palette(
    notes: list[dict], k: int = 10, merge_below: float = 12.0
) -> list[tuple[str, float]]:
    """Weighted union of the notes' palettes, near-duplicates merged, top k."""
    buckets: list[list] = []  # [hex, weight]
    for n in notes:
        for h, w in n["palette"]:
            for b in buckets:
                if color_distance(b[0], h) < merge_below:
                    b[1] += w
                    break
            else:
                buckets.append([h, w])
    buckets.sort(key=lambda b: -b[1])
    return [(b[0], b[1]) for b in buckets[:k]]


def build_board(
    notes: list[dict],
    vault: Path,
    title: str,
    cols: int | None = None,
    cell_w: int = 340,
    gap: int = 40,
) -> tuple[str, list[str]]:
    """Return (excalidraw markdown, missing-image titles). Only notes with a local image are placed."""
    placed = [n for n in notes if n["image"]]
    skipped = [n["title"] for n in notes if not n["image"]]
    cols = cols or max(2, min(6, round(math.sqrt(max(len(placed), 1) * 1.3))))
    x0, y0 = 120, 230
    heights = [y0] * cols
    elements = [
        _text("hdr", 120, 100, 900, title, 34),
        _text(
            "sub",
            120,
            160,
            900,
            f"{len(placed)} images · {len(skipped)} without a local image",
            14,
            "#6b7280",
        ),
    ]
    files: dict[str, str] = {}
    for i, n in enumerate(placed):
        c = min(range(cols), key=lambda j: heights[j])
        w0, h0 = image_size(n["image"])
        h = round(cell_w * h0 / w0) if w0 else cell_w
        x, y = x0 + c * (cell_w + gap), heights[c]
        rel = str(n["image"].relative_to(vault.resolve()))
        fid = hashlib.sha1(n["image"].read_bytes()).hexdigest()
        files.setdefault(fid, rel)
        url = n["fm"].get("source_url")
        link = str(url) if url and str(url).startswith("http") else None
        elements.append(
            _el(
                "image",
                f"img-{i}",
                x,
                y,
                cell_w,
                h,
                fileId=fid,
                status="saved",
                scale=[1, 1],
                crop=None,
                link=link,
            )
        )
        author = n["fm"].get("author")
        cap = n["title"] + (f" · {author}" if author else "")
        host = _source_host(url)
        cap_el = _text(f"cap-{i}", x, y + h + 6, cell_w, cap, 13)
        elements.append(cap_el)
        used = cap_el["height"]
        if host:
            src = _text(f"src-{i}", x, y + h + 6 + used, cell_w, host, 11, "#6b7280")
            elements.append(src)
            used += src["height"]
        heights[c] = y + h + 6 + used + gap
    # palette strip under the lowest column
    py = max(heights) + 20
    elements.append(_text("pal-t", 120, py, 600, "Palette", 20))
    sw, sgap = 110, 20
    for j, (hx, _w) in enumerate(merge_palette(placed)):
        sx = 120 + j * (sw + sgap)
        elements.append(
            _el("rectangle", f"pal-{j}", sx, py + 40, sw, 80, backgroundColor=hx)
        )
        elements.append(_text(f"pal-{j}-l", sx, py + 126, sw, hx, 12))
    head = (
        "---\nexcalidraw-plugin: parsed\ntags: [excalidraw, moodboard, wiki-inspi]\n---\n"
        "==⚠  Switch to EXCALIDRAW VIEW in the MORE OPTIONS menu of this document. ⚠==\n\n"
        "# Excalidraw Data\n\n## Text Elements\n## Embedded Files\n"
    )
    emb = "".join(f"{fid}: [[{rel}]]\n\n" for fid, rel in files.items())
    drawing = {
        "type": "excalidraw",
        "version": 2,
        "source": "https://github.com/zsviczian/obsidian-excalidraw-plugin",
        "elements": elements,
        "appState": {"gridSize": None, "viewBackgroundColor": "#FFFFFF"},
        "files": {},
    }
    body = (
        "%%\n## Drawing\n```json\n"
        + json.dumps(drawing, indent=2, ensure_ascii=False)
        + "\n```\n%%\n"
    )
    return head + emb + body, skipped


def write_board(
    vault: Path, name: str, notes: list[dict], title: str | None = None, **kw
) -> tuple[Path, int, list[str]]:
    slug = safe_board_name(name)
    out = vault / "Moodboards" / f"{slug}.excalidraw.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    text, skipped = build_board(notes, vault, title or slug, **kw)
    out.write_text(text, encoding="utf-8")
    return out, len([n for n in notes if n["image"]]), skipped

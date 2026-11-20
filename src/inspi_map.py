"""inspi map: a browser view of the Wiki Inspi library, grouped into families of images.

Families = communities in a k-nearest-neighbour graph built from each note's classification
(style, mood, tags, technique, movement, themes, Pinterest board) plus its main color.
Output: <vault>/.wiki-inspi/map/index.html + data.js + thumbs/ (local only, gitignored).
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections import Counter, defaultdict
from pathlib import Path

import networkx as nx

import inspi_facets as F
import inspilib as L

FIELDS = {  # field -> weight in the similarity
    "style": 3.0,
    "movement": 2.5,
    "mood": 2.0,
    "technique": 2.0,
    "tags": 1.5,
    "suggested_mocs": 1.5,
    "pinterest_board": 1.0,
    "domain": 0.5,
}
K = 8
THUMB = 220


def _list(v):
    if v is None:
        return []
    if isinstance(v, (list, tuple)):
        return [str(x) for x in v if x not in (None, "")]
    return [str(v)]


def _norm(tok: str) -> str:
    tok = re.sub(r"[\[\]\"]", "", tok).strip().lower()
    return re.sub(r"[\s_]+", "-", tok)


def features(note) -> dict[str, float]:
    f: dict[str, float] = {}
    for field, w in FIELDS.items():
        for t in _list(note["fm"].get(field)):
            t = _norm(t)
            if t and t not in ("null", "none"):
                f[f"{field}:{t}"] = max(f.get(f"{field}:{t}", 0), w)
    return f


def tfidf(feats: list[dict]) -> list[dict]:
    n = len(feats)
    df = Counter(k for f in feats for k in f)
    out = []
    for f in feats:
        v = {k: w * math.log((1 + n) / (1 + df[k])) for k, w in f.items() if df[k] > 1}
        norm = math.sqrt(sum(x * x for x in v.values())) or 1.0
        out.append({k: x / norm for k, x in v.items()})
    return out


def lab(hexc):
    try:
        return L.rgb_to_lab(L.hex_to_rgb(L.parse_color(hexc)))
    except Exception:
        return None


def build_graph(vecs, labs):
    inv = defaultdict(list)
    for i, v in enumerate(vecs):
        for k, x in v.items():
            inv[k].append((i, x))
    g = nx.Graph()
    g.add_nodes_from(range(len(vecs)))
    g.graph["nb"] = {}
    for i, v in enumerate(vecs):
        scores = defaultdict(float)
        for k, x in v.items():
            for j, y in inv[k]:
                if j != i:
                    scores[j] += x * y
        if labs[i]:
            for j in list(scores):
                if labs[j]:
                    d = math.dist(labs[i], labs[j])
                    scores[j] += 0.15 * max(0.0, 1 - d / 60)
        ranked = sorted(scores.items(), key=lambda t: -t[1])
        g.graph["nb"][i] = [[j, round(s, 3)] for j, s in ranked[:30] if s > 0.05]
        for j, s in ranked[:K]:
            if s > 0.08:
                w = max(s, g[i][j]["weight"]) if g.has_edge(i, j) else s
                g.add_edge(i, j, weight=w)
    return g


def label_for(members, vecs, feats_raw):
    score = Counter()
    for i in members:
        for k, x in vecs[i].items():
            if not k.startswith(("pinterest_board:", "domain:")):
                score[k] += x
    words = []
    for k, _ in score.most_common(12):
        w = k.split(":", 1)[1].replace("-", " ")
        if w not in words:
            words.append(w)
        if len(words) == 3:
            break
    return " · ".join(words) or "divers"


def dhash(src: Path) -> int | None:
    from PIL import Image

    try:
        with Image.open(src) as im:
            im.seek(0)
            px = list(im.convert("L").resize((9, 8)).getdata())
    except Exception:
        return None
    mean = sum(px) / len(px)
    if (sum((v - mean) ** 2 for v in px) / len(px)) ** 0.5 < 4:
        return None  # flat picture (a swatch, a blank card): its hash says nothing, do not group it
    return sum(1 << i for i in range(64) if px[(i // 8) * 9 + i % 8] > px[(i // 8) * 9 + i % 8 + 1])


def dup_groups(items, paths):
    """Groups of the same picture: identical bytes, or a near-identical dHash (<= 2 bits)."""
    parent = {it["i"]: it["i"] for it in items}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    by_sha, by_dh = {}, []
    exact = set()
    for it in items:
        p = paths[it["i"]]
        try:
            sha = hashlib.sha1(p.read_bytes()).hexdigest()
        except OSError:
            continue
        if sha in by_sha:
            parent[find(it["i"])] = find(by_sha[sha])
            exact.add(it["i"]); exact.add(by_sha[sha])
        else:
            by_sha[sha] = it["i"]
            h = dhash(p)
            if h is not None:
                by_dh.append((h, it["i"]))
    for a in range(len(by_dh)):
        for b in range(a + 1, len(by_dh)):
            if bin(by_dh[a][0] ^ by_dh[b][0]).count("1") <= 2:
                parent[find(by_dh[a][1])] = find(by_dh[b][1])
    groups = defaultdict(list)
    for it in items:
        groups[find(it["i"])].append(it)
    out = []
    for g in groups.values():
        if len(g) < 2:
            continue
        # keep: classified first, then the biggest file, then the oldest
        g.sort(key=lambda it: (it["f"] == -2, -paths[it["i"]].stat().st_size, it["d"]))
        out.append({"ids": [it["i"] for it in g], "exact": all(it["i"] in exact for it in g)})
    return out


def thumb(src: Path, out_dir: Path) -> str | None:
    from PIL import Image

    h = hashlib.sha1(str(src).encode()).hexdigest()[:16]
    dest = out_dir / f"{h}.jpg"
    if not dest.exists() or dest.stat().st_mtime < src.stat().st_mtime:
        try:
            with Image.open(src) as im:
                im.seek(0)
                im = im.convert("RGB")
                im.thumbnail((THUMB, THUMB * 2))
                im.save(dest, "JPEG", quality=78)
        except Exception:
            return None
    return f"thumbs/{dest.name}"


def build(vault: Path = L.VAULT) -> Path:
    out = vault / ".wiki-inspi" / "map"
    (out / "thumbs").mkdir(parents=True, exist_ok=True)
    notes = [n for n in L.load_notes(vault) if n["image"]]
    waiting = [n for n in L.load_notes(vault, ("Inbox",)) if n["image"]]
    feats = [features(n) for n in notes]
    vecs = tfidf(feats)
    labs = [
        lab(n["fm"].get("main_color") or "") if n["fm"].get("main_color") else None
        for n in notes
    ]
    g = build_graph(vecs, labs)
    comms = nx.community.louvain_communities(g, weight="weight", resolution=1.2, seed=7)
    comms = sorted(comms, key=len, reverse=True)
    fam_of = {}
    families = []
    for fi, c in enumerate(comms):
        if len(c) < 3:
            continue
        families.append({"id": fi, "label": label_for(c, vecs, feats), "size": len(c)})
        for i in c:
            fam_of[i] = fi
    vault_name = vault.name
    items = []
    for i, n in enumerate(notes):
        t = thumb(n["image"], out / "thumbs")
        if not t:
            continue
        fm = n["fm"]
        items.append(
            {
                "i": i,
                "t": n["title"][:120],
                "img": t,
                "full": "../../" + str(n["image"].relative_to(vault)),
                "f": fam_of.get(i, -1),
                "fa": F.facets(fm),
                "nb": g.graph["nb"].get(i, []),
                "d": int(n["path"].stat().st_mtime),
                "r": n["rel"],
                "c": fm.get("main_color") or "",
                "b": str(fm.get("pinterest_board") or ""),
                "s": _list(fm.get("style"))[:3],
                "m": _list(fm.get("mood"))[:3],
                "src": str(fm.get("source_url") or ""),
                "o": "obsidian://open?vault="
                + _q(vault_name)
                + "&file="
                + _q(n["rel"][:-3]),
            }
        )
    pos = layout(g, fam_of)
    for it in items:
        x, y = pos[it["i"]]
        it["x"], it["y"] = round(float(x), 4), round(float(y), 4)
    for j, n in enumerate(waiting):
        t = thumb(n["image"], out / "thumbs")
        if t:
            items.append({
                "i": len(notes) + j, "t": n["title"][:120], "img": t,
                "full": "../../" + str(n["image"].relative_to(vault)), "f": -2, "fa": {}, "r": n["rel"], "d": int(n["path"].stat().st_mtime),
                "c": "", "b": str(n["fm"].get("pinterest_board") or ""), "s": [], "m": [],
                "src": str(n["fm"].get("source_url") or ""),
                "o": "obsidian://open?vault=" + _q(vault_name) + "&file=" + _q(n["rel"][:-3]),
            })
    keep = {it["i"] for it in items}
    edges = [
        [a, b, round(d["weight"], 3)]
        for a, b, d in g.edges(data=True)
        if a in keep and b in keep
    ]
    paths = {i: n["image"] for i, n in enumerate(notes)}
    paths.update({len(notes) + j: n["image"] for j, n in enumerate(waiting)})
    data = {"items": items, "edges": edges, "families": families, "dups": dup_groups(items, paths)}
    (out / "data.js").write_text(
        "window.INSPI=" + json.dumps(data, ensure_ascii=False) + ";"
    )
    (out / "index.html").write_text(
        (Path(__file__).with_name("inspi_map.html")).read_text()
    )
    return out / "index.html"


def layout(g, fam_of):
    h = nx.Graph()
    h.add_nodes_from(g.nodes)
    for a, b, d in g.edges(data=True):
        same = fam_of.get(a, -1) == fam_of.get(b, -2)
        h.add_edge(a, b, weight=d["weight"] * (4.0 if same else 0.25))
    return nx.spring_layout(h, weight="weight", k=1.2 / math.sqrt(max(len(h), 1)), iterations=120, seed=7)


def _q(s: str) -> str:
    import urllib.parse

    return urllib.parse.quote(s, safe="")

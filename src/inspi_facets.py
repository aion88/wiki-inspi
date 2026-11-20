"""Filter facets for the Wiki Inspi gallery: type, technique, subject, color.

Deterministic keyword rules over what Haiku already wrote (technique, style, tags, movement,
medium_detail, subject, domain) plus main_color. No model call: rebuilt on every `inspi map`.
"""

from __future__ import annotations

import colorsys
import re

TYPE = {
    "Photographie": r"photo|35mm|film-grain|studio-light|depth-of-field|shallow-focus|macro|long-exposure|available-light|natural-light|lens",
    "Cinématique": r"cinemat|film-still|video-still|movie|frame-grab|anamorphic|widescreen",
    "Illustration": r"illustrat|drawing|drawn|marker|pen-and-ink|stippl|comic|sketch|linework|line-art",
    "Peinture": r"paint|watercolo|gouache|oil-|acrylic|brushwork|brush-stroke|ink-wash|impasto|fresco",
    "Graphisme & typo": r"poster|typograph|swiss-design|editorial-design|editorial-layout|magazine|layout|brochure|offset|grid|logo|brand|lettering|signage|wayfinding",
    "Collage & mix média": r"collage|mixed-media|photomontage|photo-composite|assemblage|cut-out|cutout",
    "3D": r"(^|[^a-z0-9])3d|render|cgi|blender|octane|cinema4d",
    "Interface": r"(^|[^a-z])ui([^a-z]|$)|dashboard|screenshot|screen-capture|website|interface|bento|dark-mode|app-",
    "Sculpture & installation": r"sculpt|installation|ceramic|resin|textile|embroider|weav",
}
TECHNIQUE = {
    "Encre": r"ink|pen-and-",
    "Aquarelle": r"watercolo",
    "Gouache": r"gouache",
    "Huile": r"oil-paint|oil-on|(^|-)oil($|-)",
    "Acrylique": r"acrylic",
    "Crayon & dessin": r"pencil|graphite|charcoal|drawing|sketch",
    "Marqueur": r"marker",
    "Pastel": r"(^|-)pastel-(chalk|drawing)|oil-pastel|soft-pastel",
    "Collage": r"collage|cut-out|cutout|photomontage",
    "Mix média": r"mixed-media|mixed media|assemblage",
    "Risographie": r"riso",
    "Sérigraphie": r"screen-print|screenprint|silkscreen|serigra",
    "Gravure": r"etching|engraving|woodcut|linocut|intaglio|lithograph",
    "Trame & halftone": r"halftone|dot-screen|ben-day",
    "Argentique": r"35mm|film-photo|analog|film-grain|black-and-white-film|polaroid|medium-format",
    "Vectoriel": r"vector",
    "Peinture numérique": r"digital-paint",
    "Rendu 3D": r"(^|[^a-z0-9])3d|render|cgi",
    "Généré par IA": r"ai-generated|midjourney|diffusion",
}
SUBJECT = {
    "Abstrait": r"abstract|non-figurative|non-representational",
    "Géométrique": r"geometr|grid|circle|square|triangle|shapes",
    "Formes organiques": r"organic|fluid|blob|biomorph|flowing|curv",
    "Personne": r"(person|people|woman|women|man|men|portraits?|figures?|faces?|girls?|boys?|body|bodies|crowd|dancers?|child|children|self-portrait)(?![a-z])",
    "Mode": r"fashion|garment|outfit|jacket|dress|coat|makeup|beauty|clothing|knit",
    "Paysage & nature": r"(landscape|nature|seas?|seascape|ocean|forest|mountains?|sky|water|trees?|fields?|beach|desert|lake|river|dunes?)(?![a-z])",
    "Architecture & intérieur": r"architect|building|interior|room|facade|house|stair|street",
    "Objet & nature morte": r"still-life|still life|object|product|bottle|vase|fruit|glass|tableware",
    "Typographie": r"(typograph|letter|type|font|word)",
    "Animal": r"(animals?|birds?|dogs?|cats?|horses?|fish|insects?|butterfl(y|ies))(?![a-z])",
    "Données": r"(data|charts?|infographics?|diagrams?|maps?|dataviz)(?![a-z])",
    "Noir et blanc": r"black-and-white|monochrome|grayscale|greyscale",
}
_C = {
    k: {n: re.compile(r"(?<![a-z])(?:" + p + ")") for n, p in d.items()}
    for k, d in (("ty", TYPE), ("te", TECHNIQUE), ("su", SUBJECT))
}


def _tokens(fm: dict) -> str:
    parts = []
    for k in ("technique", "style", "tags", "movement", "domain"):
        v = fm.get(k)
        parts += [str(x) for x in (v if isinstance(v, list) else [v]) if x]
    for k in ("medium_detail", "subject"):
        if fm.get(k):
            parts.append(re.sub(r"\s+", "-", str(fm[k]).lower()))
    return " ".join(p.lower().replace("_", "-").replace(" ", "-") for p in parts)


def color_name(hexc) -> str | None:
    m = re.fullmatch(r"#?([0-9a-fA-F]{6})", str(hexc or "").strip())
    if not m:
        return None
    r, g, b = (int(m.group(1)[i : i + 2], 16) / 255 for i in (0, 2, 4))
    h, l, s = colorsys.rgb_to_hls(r, g, b)
    h *= 360
    if s < 0.18 or l < 0.08 or l > 0.94:
        return "Noir" if l < 0.22 else "Blanc & crème" if l > 0.82 else "Gris"
    if (h < 40 or h >= 345) and l < 0.32 and s < 0.6:
        return "Brun"
    if h < 15 or h >= 345:
        return "Rouge"
    if h < 40:
        return "Brun" if l < 0.35 else "Beige" if s < 0.45 and l > 0.7 else "Orange"
    if h < 65:
        return "Jaune"
    if h < 165:
        return "Vert"
    if h < 255:
        return "Bleu"
    if h < 290:
        return "Violet"
    return "Rose"


def facets(fm: dict) -> dict:
    text = _tokens(fm)
    out = {
        k: [n for n, rx in rules.items() if rx.search(text)] for k, rules in _C.items()
    }
    c = color_name(fm.get("main_color"))
    out["co"] = [c] if c else []
    t = str(fm.get("color_temperature") or "").lower()
    out["tp"] = {"warm": ["Chaud"], "cool": ["Froid"], "neutral": ["Neutre"]}.get(t, [])
    return out

import json
import re
import sys
from pathlib import Path

import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import inspilib as L  # noqa: E402


def make_note(vault, slug, fm_lines, size=(200, 100), color=(10, 20, 200), body_extra=""):
    (vault / "_attachments").mkdir(exist_ok=True)
    (vault / "Library").mkdir(exist_ok=True)
    Image.new("RGB", size, color).save(vault / "_attachments" / f"{slug}.png")
    fm = "\n".join(fm_lines)
    (vault / "Library" / f"{slug}.md").write_text(
        f"---\nthumbnail: _attachments/{slug}.png\n{fm}\n---\n\n# {slug.title()}\n\n"
        f"![[_attachments/{slug}.png]]\n\n## Why\n\n{body_extra}\n", encoding="utf-8")


@pytest.fixture
def vault(tmp_path):
    make_note(tmp_path, "ocean", [
        'source_url: https://www.example.com/p/1', 'tags: ["water", "dark-blue"]',
        'mood: ["calm", "cold"]', 'style: ["minimalism"]', 'author: "A. Artist"',
        'main_color: "#1e3a8a"',
        'palette: [{"hex": "#1e3a8a", "name": "navy", "share": 0.6}, {"hex": "#e5e7eb", "name": "fog", "share": 0.4}]',
    ], body_extra="Still water at dusk.")
    make_note(tmp_path, "sunset", [
        'tags: ["warm", "orange"]', 'mood: ["bold"]', 'style: ["brutalism"]',
        'dominant_colors: ["#f97316", "#7c2d12"]',
    ], size=(100, 200), color=(240, 120, 20))
    make_note(tmp_path, "plain", ['tags: ["neutral"]', 'mood: []'], color=(120, 120, 120))
    # note whose image is missing
    (tmp_path / "Library" / "ghost.md").write_text(
        "---\ntags: [ghost]\nthumbnail: _attachments/none.png\n---\n# Ghost\n", encoding="utf-8")
    return tmp_path


def titles(results):
    return [r["note"]["title"] for r in results]


# -------------------------------------------------------------- search filters
def test_text_match_over_tags_and_hyphens(vault):
    r = L.search(L.load_notes(vault), "dark blue")
    assert titles(r) == ["Ocean"]  # "dark-blue" tag matches "dark blue"


def test_text_match_in_why_body(vault):
    assert titles(L.search(L.load_notes(vault), "dusk")) == ["Ocean"]


def test_filters_style_mood_author(vault):
    notes = L.load_notes(vault)
    assert titles(L.search(notes, filters={"style": "brutal"})) == ["Sunset"]
    assert titles(L.search(notes, filters={"mood": "calm"})) == ["Ocean"]
    assert titles(L.search(notes, filters={"author": "artist"})) == ["Ocean"]
    assert L.search(notes, filters={"style": "nonexistent"}) == []


def test_empty_values_do_not_crash_and_limit(vault):
    notes = L.load_notes(vault)
    assert len(notes) == 4
    assert len(L.search(notes, limit=2)) == 2


# -------------------------------------------------------------- color matching
def test_parse_color():
    assert L.parse_color("#ABCDEF") == "#abcdef"
    assert L.parse_color("blue") == L.COLOR_NAMES["blue"]
    with pytest.raises(ValueError):
        L.parse_color("notacolor")


def test_color_distance_properties():
    assert L.color_distance("#336699", "#336699") == 0
    assert L.color_distance("#000000", "#ffffff") == pytest.approx(100, abs=1)
    assert L.color_distance("#1e3a8a", "#1f3b8b") < 2
    assert L.color_distance("#1e3a8a", "#f97316") > 60


def test_color_search_uses_palette_and_dominant_colors(vault):
    notes = L.load_notes(vault)
    navy = L.search(notes, color="#203c8c")
    assert titles(navy) == ["Ocean"]  # v2 palette
    assert navy[0]["distance"] < 5
    orange = L.search(notes, color="orange")
    assert titles(orange)[0] == "Sunset"  # v1 dominant_colors
    assert L.search(notes, color="#00ff00") == []  # nothing near pure green


def test_color_and_text_combine(vault):
    assert L.search(L.load_notes(vault), "water", color="#1e3a8a")[0]["note"]["title"] == "Ocean"
    assert L.search(L.load_notes(vault), "warm", color="#1e3a8a") == []


def test_result_json_is_serialisable(vault):
    r = L.search(L.load_notes(vault), "water")
    j = L.result_json(r[0], vault)
    json.dumps(j)
    assert j["main_color"] == "#1e3a8a" and j["image"].endswith("ocean.png")


# ------------------------------------------------------------------- boards
def parse_board(path: Path):
    s = path.read_text(encoding="utf-8")
    assert s.startswith("---\nexcalidraw-plugin: parsed")
    assert "## Embedded Files" in s and "%%\n## Drawing\n```json\n" in s
    data = json.loads(s.split("```json\n")[1].split("\n```")[0])
    emb = dict(re.findall(r"^([0-9a-f]{40}): \[\[(.+?)\]\]", s, re.M))
    return data, emb


def test_board_valid(vault):
    notes = L.load_notes(vault)
    out, placed, skipped = L.write_board(vault, "My Board!", notes, title="test")
    assert out == vault / "Moodboards" / "my-board.excalidraw.md"
    assert placed == 3 and skipped == ["Ghost"]
    data, emb = parse_board(out)
    assert data["type"] == "excalidraw" and data["files"] == {}
    images = [e for e in data["elements"] if e["type"] == "image"]
    assert len(images) == 3
    for e in images:  # every fileId has an embed line, every embed target exists
        assert e["fileId"] in emb
    for rel in emb.values():
        assert (vault / rel).is_file()
    ids = [e["id"] for e in data["elements"]]
    assert len(ids) == len(set(ids))


def test_board_keeps_each_image_ratio_and_no_overlap(vault):
    notes = L.load_notes(vault)
    out, *_ = L.write_board(vault, "ratio", notes)
    data, _ = parse_board(out)
    imgs = {e["id"]: e for e in data["elements"] if e["type"] == "image"}
    ratios = sorted(round(e["width"] / e["height"], 1) for e in imgs.values())
    assert ratios == [0.5, 2.0, 2.0]
    boxes = [e for e in data["elements"] if e["type"] in ("image", "text", "rectangle")]
    for i, a in enumerate(boxes):
        for b in boxes[i + 1:]:
            sep = (a["x"] + a["width"] <= b["x"] or b["x"] + b["width"] <= a["x"]
                   or a["y"] + a["height"] <= b["y"] or b["y"] + b["height"] <= a["y"])
            assert sep, (a["id"], b["id"])


def test_board_caption_palette_strip(vault):
    notes = L.load_notes(vault)
    out, *_ = L.write_board(vault, "cap", notes)
    data, _ = parse_board(out)
    texts = [e["text"] for e in data["elements"] if e["type"] == "text"]
    assert any("Ocean · A. Artist" == t for t in texts)
    assert "example.com" in texts
    swatches = [e for e in data["elements"] if e["type"] == "rectangle"]
    assert swatches and all(re.fullmatch(r"#[0-9a-f]{6}", e["backgroundColor"]) for e in swatches)
    img_bottom = max(e["y"] + e["height"] for e in data["elements"] if e["type"] == "image")
    assert min(e["y"] for e in swatches) > img_bottom  # strip is below the grid


def test_board_name_is_confined():
    for bad in ("", "../x", "!!!"):
        if bad == "../x":
            assert L.safe_board_name(bad) == "x"  # no path traversal survives
        else:
            with pytest.raises(ValueError):
                L.safe_board_name(bad)

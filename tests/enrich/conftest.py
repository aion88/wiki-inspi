"""Shared fixtures. The module under test is loaded from ENRICH_PY (default: the sibling enrich.py),
so the same tests can be pointed at the pre-change file to show them red."""
import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest

os.environ["WIKI_INSPI_CONFIG"] = "/nonexistent/wiki-inspi-config.json"  # hermetic: defaults only
os.environ.pop("INSPI_VAULT", None)
os.environ.pop("ENRICH_BACKEND", None)

HERE = Path(__file__).resolve().parent
ENRICH_PY = Path(os.environ.get("ENRICH_PY", HERE.parents[1] / "src" / "enrich.py"))


def load_enrich():
    spec = importlib.util.spec_from_file_location("enrich_under_test", ENRICH_PY)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["enrich_under_test"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture()
def enrich():
    return load_enrich()


@pytest.fixture()
def vault(tmp_path, enrich):
    """A temp vault whose path CONTAINS A SPACE, like the real one."""
    v = tmp_path / "Test Vault"
    for d in ("Inbox", "Library", "MOCs", "_attachments"):
        (v / d).mkdir(parents=True)
    (v / "_taxonomy.yml").write_text(
        "use_for:\n  - poster-design\n  - web-design\nsynonyms:\n  brutalist: brutalism\n  brutalisme: brutalism\n"
    )
    if hasattr(enrich, "_configure"):
        enrich._configure(v)
    return v


GOOD_JSON = {
    "type": "image",
    "title": "Deep Blue Study",
    "summary": "Soft blue gradients.",
    "tags": ["Brutalist", "dark mode", "Gradient"],
    "domain": "art",
    "medium": "static",
    "mood": ["calm", "Poetic"],
    "style": ["Brutalisme"],
    "movement": [],
    "palette": [
        {"hex": "#0a1a3a", "name": "navy", "share": 0.6},
        {"hex": "#fff", "name": "white", "share": 30},
    ],
    "main_color": "#0A1A3A",
    "color_temperature": "cool",
    "technique": ["3d-render"],
    "medium_detail": "digital render",
    "era": None,
    "subject": "abstract gradient",
    "composition": "centered",
    "lighting": None,
    "typography": None,
    "texture": "smooth",
    "references": [],
    "use_for": ["poster-design", "not-in-list"],
    "author": "MODEL SHOULD NOT BE ABLE TO SET THIS",
    "author_guess": "Someone",
    "author_confidence": 0.4,
    "candidate_mocs": [{"name": "Abstract Design", "confidence": 0.9}],
}


@pytest.fixture()
def good_json():
    return json.loads(json.dumps(GOOD_JSON))

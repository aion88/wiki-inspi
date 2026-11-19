"""--reenrich / --reenrich-all: bring schema-v1 Library notes to v2 without losing anything."""

import json

import pytest
import yaml
from PIL import Image

V1_BODY = "# Old Title\n\n![[_attachments/old.png]]\n\n## Why\n\nHand-written summary, keep me.\n\n## Source\n\n- https://x.test/p\n"


def make_v1(vault, name="old", extra_fm="", image=True, thumb=True):
    if image:
        Image.new("RGB", (64, 64), (10, 20, 200)).save(
            vault / "_attachments" / f"{name}.png"
        )
    fm = (
        "---\ntype: image\nsource_url: https://x.test/p\ncaptured: 2026-08-05\n"
        + (f"thumbnail: _attachments/{name}.png\n" if thumb else "thumbnail: \n")
        + 'dominant_colors: ["#1a1a1a", "#f5f0e8"]\ntags: ["keep-tag", "second"]\ndomain: editorial\n'
        'medium: static\nmood: ["calm"]\nsuggested_mocs: ["[[Editorial Web]]"]\nconfidence: 0.62\n'
        f"ingest_source: url-cli\n{extra_fm}---\n\n"
    )
    p = vault / "Library" / f"{name}.md"
    p.write_text(fm + V1_BODY.replace("old.png", f"{name}.png"))
    return p


def fm_of(p):
    import re

    m = re.match(r"\A---\n(.*?)\n---", p.read_text(), re.DOTALL)
    return yaml.safe_load(m.group(1))


def run_main(enrich, monkeypatch, *args):
    monkeypatch.setattr("sys.argv", ["enrich.py", *args])
    return enrich.main()


@pytest.fixture()
def ok_backend(enrich, monkeypatch, good_json):
    seen = {"calls": 0}

    def fine(payload, system):
        seen["calls"] += 1
        seen["payload"] = payload
        e = enrich._enrichment_from_json(json.loads(json.dumps(good_json)))
        e.enriched_by = "claude-cli:test"
        return e

    monkeypatch.setitem(enrich.PROVIDERS, "claude-cli", fine)
    monkeypatch.setenv("ENRICH_BACKEND", "claude-cli,ollama")
    return seen


@pytest.fixture()
def failing_backend(enrich, monkeypatch):
    def boom(payload, system):
        raise RuntimeError("backend down")

    monkeypatch.setitem(enrich.PROVIDERS, "claude-cli", boom)
    monkeypatch.setitem(enrich.PROVIDERS, "ollama", boom)


def test_merge_adds_v2_keeps_v1_fields_and_body(enrich, vault, ok_backend, monkeypatch):
    p = make_v1(vault)
    before_fm, before_text = fm_of(p), p.read_text()
    monkeypatch.setenv("INSPI_VAULT", str(vault))
    assert run_main(enrich, monkeypatch, "--reenrich", str(p)) == 0
    after = fm_of(p)
    for k, v in before_fm.items():
        assert after[k] == v, f"existing field {k} must be untouched"
    assert after["schema_version"] == 2
    assert after["main_color"] == "#0a1a3a"
    assert after["enriched_by"] == "claude-cli:test"
    assert after["author"] is None and after["author_source"] is None
    assert enrich.validate_note(p) == []
    body_before = before_text.split("\n---\n", 1)[1]
    assert p.read_text().split("\n---\n", 1)[1] == body_before, "body byte-identical"
    assert p.name == "old.md" and (vault / "_attachments" / "old.png").exists()
    assert ok_backend["payload"]["type"] == "image", (
        "model must be shown the local image"
    )


def test_idempotent_second_run_skips_v2(enrich, vault, ok_backend, monkeypatch):
    p = make_v1(vault)
    monkeypatch.setenv("INSPI_VAULT", str(vault))
    run_main(enrich, monkeypatch, "--reenrich-all", "--limit", "0")
    text = p.read_text()
    assert ok_backend["calls"] == 1
    assert run_main(enrich, monkeypatch, "--reenrich-all", "--limit", "0") == 0
    assert ok_backend["calls"] == 1 and p.read_text() == text


def test_reenrich_explicit_v2_note_is_skipped(
    enrich, vault, ok_backend, monkeypatch, capsys
):
    p = make_v1(vault, extra_fm="schema_version: 2\n")
    monkeypatch.setenv("INSPI_VAULT", str(vault))
    assert run_main(enrich, monkeypatch, "--reenrich", str(p)) == 0
    assert ok_backend["calls"] == 0 and "already" in capsys.readouterr().out.lower()


def test_existing_author_preserved_and_model_never_sets_it(
    enrich, vault, ok_backend, monkeypatch
):
    p = make_v1(vault, extra_fm="author: Jane Doe\n")
    monkeypatch.setenv("INSPI_VAULT", str(vault))
    run_main(enrich, monkeypatch, "--reenrich", str(p))
    fm = fm_of(p)
    assert fm["author"] == "Jane Doe" and fm["author_source"]
    assert "MODEL SHOULD NOT" not in p.read_text()
    assert enrich.validate_note(p) == []


def test_failure_records_error_and_leaves_note_valid_then_gives_up(
    enrich, vault, failing_backend, monkeypatch
):
    p = make_v1(vault)
    original = fm_of(p)
    monkeypatch.setenv("INSPI_VAULT", str(vault))
    for n in (1, 2, 3):
        assert run_main(enrich, monkeypatch, "--reenrich-all", "--limit", "0") == 1
        fm = fm_of(p)
        assert fm["enrich_attempts"] == n and "enrich_error" in fm
        for k, v in original.items():
            assert fm[k] == v
    assert fm_of(p)["enrich_failed"] is True
    assert V1_BODY.replace("old.png", "old.png").split("\n", 2)[2] in p.read_text()
    # now skipped
    assert run_main(enrich, monkeypatch, "--reenrich-all", "--limit", "0") == 0
    assert (
        run_main(
            enrich, monkeypatch, "--reenrich-all", "--limit", "0", "--retry-failed"
        )
        == 1
    )


def test_success_clears_previous_error_fields(enrich, vault, ok_backend, monkeypatch):
    p = make_v1(vault, extra_fm='enrich_attempts: 1\nenrich_error: "boom"\n')
    monkeypatch.setenv("INSPI_VAULT", str(vault))
    run_main(enrich, monkeypatch, "--reenrich", str(p))
    fm = fm_of(p)
    assert (
        "enrich_error" not in fm
        and "enrich_attempts" not in fm
        and fm["schema_version"] == 2
    )


def test_missing_image_fails_without_touching_note(
    enrich, vault, ok_backend, monkeypatch
):
    p = make_v1(vault, image=False)
    monkeypatch.setenv("INSPI_VAULT", str(vault))
    assert run_main(enrich, monkeypatch, "--reenrich", str(p)) == 1
    assert ok_backend["calls"] == 0
    fm = fm_of(p)
    assert "schema_version" not in fm and "enrich_error" in fm


def test_invalid_model_output_never_written(enrich, vault, monkeypatch, good_json):
    p = make_v1(vault)
    good_json["use_for"] = ["poster-design"]

    def fine(payload, system):
        e = enrich._enrichment_from_json(good_json)
        e.main_color = (
            "not-a-hex"  # bypass the normaliser: the gate must still catch it
        )
        e.enriched_by = "x"
        return e

    monkeypatch.setitem(enrich.PROVIDERS, "claude-cli", fine)
    monkeypatch.setenv("ENRICH_BACKEND", "claude-cli")
    monkeypatch.setenv("INSPI_VAULT", str(vault))
    assert run_main(enrich, monkeypatch, "--reenrich", str(p)) == 1
    assert "schema_version" not in fm_of(p)
    assert not list((vault / "Library").glob(".*.tmp"))


def test_limit_and_listing_only_v1(enrich, vault, ok_backend, monkeypatch):
    for n in ("a", "b", "c"):
        make_v1(vault, name=n)
    make_v1(vault, name="done", extra_fm="schema_version: 2\n")
    monkeypatch.setenv("INSPI_VAULT", str(vault))
    assert [p.name for p in enrich.find_reenrich_candidates()] == [
        "a.md",
        "b.md",
        "c.md",
    ]
    run_main(enrich, monkeypatch, "--reenrich-all", "--limit", "2")
    assert ok_backend["calls"] == 2


def test_dry_run_writes_nothing_and_calls_no_model(
    enrich, vault, ok_backend, monkeypatch
):
    p = make_v1(vault)
    text = p.read_text()
    monkeypatch.setenv("INSPI_VAULT", str(vault))
    assert run_main(enrich, monkeypatch, "--reenrich-all", "--dry-run") == 0
    assert ok_backend["calls"] == 0 and p.read_text() == text


def test_pinterest_provenance_survives_inbox_enrichment(
    enrich, vault, ok_backend, monkeypatch
):
    """Normal Inbox flow must not drop pinterest_* fields (moved Pinterest pins go through it)."""
    Image.new("RGB", (64, 64), (5, 5, 5)).save(vault / "_attachments" / "pin.png")
    stub = vault / "Inbox" / "pin.md"
    stub.write_text(
        "---\ntype: image\nsource_url: https://www.pinterest.com/pin/1/\ncaptured: 2026-07-23\n"
        "ingest_source: pinterest\nthumbnail: _attachments/pin.png\n"
        'pinterest_board: "Color Palettes"\npinterest_board_url: https://fr.pinterest.com/x/y/\n'
        "pinterest_pin_id: 1\n---\n\n# pin\n\n![[_attachments/pin.png]]\n"
    )
    monkeypatch.setenv("INSPI_VAULT", str(vault))
    assert run_main(enrich, monkeypatch) == 0
    note = next((vault / "Library").glob("*.md"))
    fm = fm_of(note)
    assert fm["pinterest_board"] == "Color Palettes" and fm["pinterest_pin_id"] == 1
    assert fm["pinterest_board_url"] == "https://fr.pinterest.com/x/y/"
    assert enrich.validate_note(note) == []

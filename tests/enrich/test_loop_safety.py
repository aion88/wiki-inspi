import json
import re
from pathlib import Path

import pytest
from PIL import Image


def make_stub(vault: Path, name="cap", with_image=True, extra=""):
    if with_image:
        Image.new("RGB", (64, 64), (200, 30, 30)).save(vault / "_attachments" / f"{name}.png")
    stub = vault / "Inbox" / f"{name}.md"
    stub.write_text(
        f"---\nsource_url: \"https://x.test/p\"\ncaptured: 2026-11-18\ningest_source: extension\n"
        f"thumbnail: _attachments/{name}.png\n{extra}---\n\n# {name}\n\n![[_attachments/{name}.png]]\n"
    )
    return stub


def run_main(enrich, monkeypatch, *args):
    monkeypatch.setattr("sys.argv", ["enrich.py", *args])
    return enrich.main()


@pytest.fixture()
def failing_backend(enrich, monkeypatch):
    def boom(payload, system):
        raise RuntimeError("backend down")
    monkeypatch.setitem(enrich.PROVIDERS, "claude-cli", boom)
    monkeypatch.setitem(enrich.PROVIDERS, "ollama", boom)
    monkeypatch.setenv("ENRICH_BACKEND", "claude-cli,ollama")


@pytest.fixture()
def ok_backend(enrich, monkeypatch, good_json):
    seen = {}
    def fine(payload, system):
        seen["payload"] = payload
        e = enrich._enrichment_from_json(json.loads(json.dumps(good_json)))
        e.enriched_by = "claude-cli:test"
        return e
    monkeypatch.setitem(enrich.PROVIDERS, "claude-cli", fine)
    monkeypatch.setenv("ENRICH_BACKEND", "claude-cli,ollama")
    return seen


def test_success_moves_note_to_library_and_exit_zero(enrich, vault, ok_backend, monkeypatch):
    stub = make_stub(vault)
    monkeypatch.setenv("INSPI_VAULT", str(vault))
    assert run_main(enrich, monkeypatch) == 0
    notes = list((vault / "Library").glob("*.md"))
    assert len(notes) == 1 and not stub.exists()
    assert enrich.validate_note(notes[0]) == []
    assert ok_backend["payload"]["type"] == "image", "model must be shown the stub's local image"
    assert (vault / "_attachments" / "deep-blue-study.png").exists()


def test_three_failures_mark_failed_then_skip(enrich, vault, failing_backend, monkeypatch, capsys):
    stub = make_stub(vault)
    monkeypatch.setenv("INSPI_VAULT", str(vault))
    for n in (1, 2, 3):
        assert run_main(enrich, monkeypatch) == 1
        assert f"enrich_attempts: {n}" in stub.read_text()
        assert "enrich_error:" in stub.read_text()
    assert "enrich_failed: true" in stub.read_text()
    assert stub.exists() and not list((vault / "Library").glob("*.md"))
    capsys.readouterr()
    assert run_main(enrich, monkeypatch) == 0, "an already-failed item is skipped, not re-failed"
    assert "enrich_failed" in capsys.readouterr().out
    assert run_main(enrich, monkeypatch, "--retry-failed") == 1


def test_image_without_frontmatter_uses_sidecar_state(enrich, vault, failing_backend, monkeypatch):
    Image.new("RGB", (40, 40)).save(vault / "Inbox" / "raw.png")
    monkeypatch.setenv("INSPI_VAULT", str(vault))
    for _ in range(3):
        run_main(enrich, monkeypatch)
    st = json.loads((vault / "Inbox" / ".enrich-state.json").read_text())
    assert st["raw.png"]["failed"] is True and st["raw.png"]["attempts"] == 3
    assert (vault / "Inbox" / "raw.png").exists()


def test_invalid_model_output_stays_in_inbox_never_in_library(enrich, vault, monkeypatch, good_json):
    bad = dict(good_json, palette=[])  # fails validator: empty palette
    def fine(payload, system):
        e = enrich._enrichment_from_json(bad)
        e.enriched_by = "t"
        return e
    monkeypatch.setitem(enrich.PROVIDERS, "claude-cli", fine)
    monkeypatch.setenv("ENRICH_BACKEND", "claude-cli")
    monkeypatch.setenv("INSPI_VAULT", str(vault))
    stub = make_stub(vault)
    assert run_main(enrich, monkeypatch) == 1
    assert stub.exists() and not list((vault / "Library").glob("*.md"))
    assert "validation failed" in stub.read_text()


def test_limit_caps_the_batch(enrich, vault, ok_backend, monkeypatch):
    for i in range(5):
        make_stub(vault, f"c{i}")
    monkeypatch.setenv("INSPI_VAULT", str(vault))
    assert run_main(enrich, monkeypatch, "--limit", "2") == 0
    assert len(list((vault / "Inbox").glob("*.md"))) == 3


def test_default_limit_is_ten(enrich):
    assert enrich.DEFAULT_LIMIT == 10


def test_one_mode_only_touches_that_item(enrich, vault, ok_backend, monkeypatch):
    a, b = make_stub(vault, "a"), make_stub(vault, "b")
    monkeypatch.setenv("INSPI_VAULT", str(vault))
    assert run_main(enrich, monkeypatch, "--one", str(a)) == 0
    assert not a.exists() and b.exists()


def test_one_mode_rejects_path_outside_inbox(enrich, vault, ok_backend, monkeypatch, tmp_path):
    outside = tmp_path / "evil.md"
    outside.write_text("---\n---\n")
    monkeypatch.setenv("INSPI_VAULT", str(vault))
    assert run_main(enrich, monkeypatch, "--one", str(outside)) == 2


# ---- MOC auto-stub bug --------------------------------------------------------

def test_moc_stub_bug_missing_mocs_are_found_and_stubbed(enrich, vault):
    (vault / "Library" / "n.md").write_text(
        '---\ntype: image\nsuggested_mocs: ["[[Abstract Design]]", "[[Bold Type]]"]\n---\n'
    )
    assert enrich.collect_referenced_mocs() == {"Abstract Design", "Bold Type"}
    stubbed, refs = enrich.stub_missing_mocs(False)
    assert sorted(stubbed) == ["Abstract Design", "Bold Type"] and refs == 2
    assert (vault / "MOCs" / "Abstract Design.md").exists()
    assert enrich.stub_missing_mocs(False)[0] == []


def test_moc_name_cannot_escape_the_vault(enrich, vault, good_json):
    good_json["candidate_mocs"] = [{"name": "../../evil", "confidence": 0.9}]
    e = enrich._enrichment_from_json(good_json)
    assert "/" not in e.candidate_mocs[0]["name"] and ".." not in e.candidate_mocs[0]["name"].replace("evil", "")

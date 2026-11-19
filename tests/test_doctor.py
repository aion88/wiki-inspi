import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))
import config  # noqa: E402
import doctor  # noqa: E402
import install_plugins  # noqa: E402

EXT = "a" * 32


@pytest.fixture
def world(tmp_path, monkeypatch):
    monkeypatch.setenv("WIKI_INSPI_CONFIG", str(tmp_path / "config.json"))
    (tmp_path / "config.json").write_text("{}")
    vault = tmp_path / "Vault"
    for d in doctor.VAULT_DIRS:
        (vault / d).mkdir(parents=True)
    (vault / "_taxonomy.yml").write_text("use_for: []\n")
    for pid in doctor.PLUGINS:
        (vault / ".obsidian/plugins" / pid).mkdir(parents=True)
        (vault / ".obsidian/plugins" / pid / "main.js").write_text("x")
    (vault / ".obsidian/community-plugins.json").write_text(json.dumps(list(doctor.PLUGINS)))
    (tmp_path / "token").write_text("tok-123\n")
    cfg = dict(config.DEFAULTS, vault=str(vault), extension_id=EXT, token_store="file",
               token_file=str(tmp_path / "token"), log_dir=str(tmp_path / "logs"),
               log_file=str(tmp_path / "logs/receiver.log"), backend="claude-cli")
    (tmp_path / "logs").mkdir()
    (tmp_path / "logs/receiver.log").write_text(json.dumps({"ts": "2026-01-01T00:00:00Z", "decision": "ok", "detail": "pair"}) + "\n")
    return tmp_path, cfg


def run(cfg, home, **kw):
    kw.setdefault("http", lambda m, u, h=None: (200, "{}"))
    kw.setdefault("sh", lambda cmd: 0)
    kw.setdefault("which", lambda n: "/usr/bin/claude")
    return {c.name: c for c in doctor.run_checks(cfg, env={}, home=home, uid=501, **kw)}


def test_everything_present_is_all_ok(world):
    home, cfg = world
    checks = run(cfg, home)
    assert [c.name for c in checks.values() if not c.ok] == []
    assert doctor.render(list(checks.values())).endswith("All good.")


def test_health_sends_origin_and_token_and_reports_401(world):
    home, cfg = world
    seen = {}
    def http(m, u, h=None):
        seen.update(method=m, url=u, headers=h); return 401, ""
    c = run(cfg, home, http=http)["receiver answers /health"]
    assert not c.ok and "401" in c.detail and "setup-token.sh" in c.fix
    assert seen["headers"] == {"Origin": f"chrome-extension://{EXT}", "Authorization": "Bearer tok-123"}
    assert seen["url"] == f"http://127.0.0.1:{cfg['port']}/health"


def test_output_never_contains_the_token(world):
    home, cfg = world
    text = doctor.render(list(run(cfg, home, http=lambda m, u, h=None: (401, "")).values()))
    assert "tok-123" not in text


def test_unpaired_extension_and_no_log_are_missing_with_fix(world):
    home, cfg = world
    cfg["extension_id"] = ""
    Path(cfg["log_file"]).write_text("")
    r = run(cfg, home)
    assert not r["receiver answers /health"].ok and "Pair" in r["receiver answers /health"].fix
    assert not r["extension paired and used"].ok and "chrome://extensions" in r["extension paired and used"].fix


def test_refusals_in_log_do_not_count_as_paired(world):
    home, cfg = world
    Path(cfg["log_file"]).write_text(json.dumps({"ts": "t", "decision": "block", "detail": "bad-token"}) + "\n")
    assert not run(cfg, home)["extension paired and used"].ok


def test_launchd_not_loaded_missing(world):
    home, cfg = world
    r = run(cfg, home, sh=lambda cmd: 113)
    assert [n for n, c in r.items() if n.startswith("launchd") and not c.ok] == [f"launchd agent {n}" for n in doctor.LABELS]
    assert "launchctl bootstrap gui/501" in r["launchd agent receiver"].fix


def test_missing_plugin_and_vault_dir(world):
    home, cfg = world
    (Path(cfg["vault"]) / ".obsidian/plugins/dataview/main.js").unlink()
    (Path(cfg["vault"]) / "Library").rmdir()
    r = run(cfg, home)
    assert not r["Obsidian plugin Dataview"].ok and "install_plugins.py" in r["Obsidian plugin Dataview"].fix
    assert not r["vault skeleton"].ok and "Library" in r["vault skeleton"].fix


def test_backends(world):
    home, cfg = world
    assert not run(cfg, home, which=lambda n: None)["backend claude-cli: `claude` installed"].ok
    cfg["backend"] = "anthropic-api"
    assert not run(cfg, home)["backend anthropic-api: ANTHROPIC_API_KEY"].ok
    ok = {c.name: c for c in doctor.run_checks(cfg, env={"ANTHROPIC_API_KEY": "x"}, home=home, uid=1, http=lambda *a, **k: (200, "{}"), sh=lambda c: 0)}
    assert ok["backend anthropic-api: ANTHROPIC_API_KEY"].ok
    cfg["backend"] = "ollama"; cfg["ollama_model"] = "gemma3:12b"
    tags = json.dumps({"models": [{"name": "gemma3:12b"}]})
    http = lambda m, u, h=None: (200, tags) if "11434" in u else (200, "{}")  # noqa: E731
    assert run(cfg, home, http=http)["backend ollama: model gemma3:12b"].ok
    def down(m, u, h=None):
        if "11434" in u: raise OSError("refused")
        return 200, "{}"
    c = run(cfg, home, http=down)["backend ollama: model gemma3:12b"]
    assert not c.ok and "ollama serve" in c.fix


def test_python_too_old(world):
    home, cfg = world
    assert not run(cfg, home, python_version=(3, 9, 1))["python >= 3.10"].ok


def test_install_plugins_with_fake_network(tmp_path):
    registry = json.dumps([{"id": "dataview", "repo": "o/dv"}, {"id": "note-gallery", "repo": "o/ng"}]).encode()
    def get(url):
        if url == install_plugins.REGISTRY: return registry
        if url.endswith("/styles.css") and "o/ng" in url: return None  # optional
        if url.startswith("https://github.com/o/") and url.endswith(("main.js", "manifest.json", "styles.css")): return b"data"
        return None
    res = install_plugins.install(tmp_path, ids=["dataview", "note-gallery", "obsidian-excalidraw-plugin"], get=get)
    assert res["dataview"].startswith("installed") and res["note-gallery"].startswith("installed")
    assert "registry" in res["obsidian-excalidraw-plugin"]
    assert (tmp_path / ".obsidian/plugins/dataview/main.js").read_bytes() == b"data"
    assert not (tmp_path / ".obsidian/plugins/note-gallery/styles.css").exists()
    assert json.loads((tmp_path / ".obsidian/community-plugins.json").read_text()) == ["dataview", "note-gallery"]

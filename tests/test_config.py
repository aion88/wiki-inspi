import json
import os
import stat
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
import config  # noqa: E402


def test_file_token_is_0600_and_rotates(tmp_path):
    cfg = dict(config.DEFAULTS, token_store="file", token_file=str(tmp_path / "sub" / "token"))
    assert config.read_token(cfg) == ""
    assert config.write_token(cfg)
    first = config.read_token(cfg)
    assert len(first) >= 40
    assert stat.S_IMODE(os.stat(cfg["token_file"]).st_mode) == 0o600
    config.write_token(cfg)
    assert config.read_token(cfg) != first


def test_save_key_keeps_other_keys(tmp_path):
    p = tmp_path / "c.json"
    p.write_text(json.dumps({"port": 1}))
    config.save_key("extension_id", "x" * 32, p)
    assert json.loads(p.read_text()) == {"port": 1, "extension_id": "x" * 32}


def test_load_env_vault_override(tmp_path, monkeypatch):
    monkeypatch.setenv("INSPI_VAULT", "/tmp/v")
    assert config.load(tmp_path / "missing.json")["vault"] == "/tmp/v"


def test_keychain_store_never_puts_the_token_in_argv(tmp_path, monkeypatch):
    """A fake `security` that prompts on the tty like the real one (-w without a value)."""
    fake = tmp_path / "bin" / "security"
    fake.parent.mkdir()
    fake.write_text(
        '#!/bin/bash\n'
        'echo "$@" > "$OUT_DIR/argv"\n'
        'case " $* " in *" -w "|*" -w") ;; *) echo "no bare -w" > "$OUT_DIR/err"; exit 3;; esac\n'
        'read -rs -p "password data for new item: " a </dev/tty; echo\n'
        'read -rs -p "retype password for new item: " b </dev/tty; echo\n'
        '[ "$a" = "$b" ] && printf %s "$a" > "$OUT_DIR/stored" || exit 4\n'
    )
    fake.chmod(0o755)
    monkeypatch.setenv("PATH", f"{fake.parent}:{os.environ['PATH']}")
    monkeypatch.setenv("OUT_DIR", str(tmp_path))
    cfg = dict(config.DEFAULTS, token_store="keychain", keychain_service="svc-test")
    assert config.write_token(cfg)
    stored = (tmp_path / "stored").read_text()
    assert len(stored) >= 40
    argv = (tmp_path / "argv").read_text()
    assert stored not in argv and "-s svc-test" in argv
    assert not (tmp_path / "err").exists()

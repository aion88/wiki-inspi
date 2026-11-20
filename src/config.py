"""Shared configuration for Wiki Inspi: one JSON file, sane defaults, no secrets in it.

Location: ~/.config/wiki-inspi/config.json (override with WIKI_INSPI_CONFIG).
The vault can also be overridden per process with INSPI_VAULT (used by tests and scripts).

    python3 config.py get vault      # print one value (used by the shell scripts)
"""

from __future__ import annotations

import getpass
import json
import os
import secrets
import subprocess
import sys
from pathlib import Path

HOME = Path.home()
SRC_DIR = Path(__file__).resolve().parent
ROOT = SRC_DIR.parent

KEYCHAIN_SERVICE = "wiki-inspi-token"  # default; account = the current macOS user

DEFAULTS: dict = {
    "vault": str(HOME / "Wiki Inspi"),
    "port": 18765,
    "map_port": 18766,  # the gallery page (inspi map / inspi serve), 127.0.0.1 only
    "token_store": "keychain",  # keychain | file (file: 0600 token_file, for CI and dry runs)
    "token_file": str(HOME / ".config/wiki-inspi/token"),
    "keychain_service": KEYCHAIN_SERVICE,
    "extension_id": "",  # empty = learned at the first pairing (see README)
    "backend": "claude-cli",  # claude-cli | anthropic-api | ollama (comma list = fallback chain)
    "model": "claude-haiku-5-5",  # used by claude-cli and anthropic-api
    "ollama_model": "gemma3:12b",
    "drop_folder": str(HOME / "Inspi Drop"),
    "log_dir": str(HOME / "Library/Logs/wiki-inspi"),
    "pair_file": str(HOME / ".local/state/wiki-inspi/pair-open"),
    "enrich_cmd": [str(ROOT / "scripts/enrich.sh")],  # the note path is appended; [] disables
}


def config_path() -> Path:
    return Path(os.environ.get("WIKI_INSPI_CONFIG") or HOME / ".config/wiki-inspi/config.json")


def load(path: Path | None = None) -> dict:
    """Defaults, then the config file, then INSPI_VAULT. A broken file falls back to defaults."""
    cfg = dict(DEFAULTS)
    p = Path(path) if path else config_path()
    try:
        if p.exists():
            cfg.update(json.loads(p.read_text()))
    except Exception as exc:
        print(f"! config unreadable ({type(exc).__name__}), using defaults", file=sys.stderr)
    if os.environ.get("INSPI_VAULT"):
        cfg["vault"] = os.environ["INSPI_VAULT"]
    cfg["log_file"] = str(Path(cfg["log_dir"]) / "receiver.log")
    return cfg


def read_token(cfg: dict) -> str:
    """The pairing token, from the configured store. Never printed or logged."""
    if cfg.get("token_store") == "file":
        try:
            return Path(cfg["token_file"]).read_text().strip()
        except OSError:
            return ""
    r = subprocess.run(
        ["security", "find-generic-password", "-a", getpass.getuser(), "-s", cfg["keychain_service"], "-w"],
        capture_output=True, text=True, timeout=10,
    )
    return r.stdout.strip() if r.returncode == 0 else ""


def write_token(cfg: dict) -> bool:
    """Generate a fresh token and store it (rotation). The value is never returned or printed."""
    value = secrets.token_urlsafe(32)
    if cfg.get("token_store") == "file":
        p = Path(cfg["token_file"])
        p.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(value)
        os.chmod(p, 0o600)
        return True
    return _keychain_store(cfg["keychain_service"], value)


def _keychain_store(service: str, value: str, timeout: float = 15.0) -> bool:
    """Store `value` with `security ... -w` (no value argument), answering its two password
    prompts through a pseudo-terminal, so the token never appears in a process argument list."""
    import pty
    import select
    import time

    pid, fd = pty.fork()
    if pid == 0:  # child
        try:
            os.execvp("security", ["security", "add-generic-password", "-U", "-a", getpass.getuser(), "-s", service, "-w"])
        finally:
            os._exit(127)
    sent, seen, deadline = 0, "", time.monotonic() + timeout
    try:
        while time.monotonic() < deadline:
            r, _, _ = select.select([fd], [], [], 0.2)
            if not r:
                continue
            try:
                chunk = os.read(fd, 1024).decode("utf-8", "replace")
            except OSError:  # child closed the pty
                break
            if not chunk:
                break
            seen += chunk
            if sent < 2 and seen.lower().count("password") > sent:
                os.write(fd, (value + "\n").encode())
                sent += 1
        _, status = os.waitpid(pid, os.WNOHANG)
        if status == 0 and _ == 0:
            time.sleep(0.3)
            _, status = os.waitpid(pid, os.WNOHANG)
    finally:
        os.close(fd)
    return sent == 2 and os.WIFEXITED(status) and os.WEXITSTATUS(status) == 0


def save_key(key: str, value, path: Path | None = None) -> None:
    """Persist one key, atomically, keeping the rest of the file."""
    p = Path(path) if path else config_path()
    data: dict = {}
    try:
        data = json.loads(p.read_text())
    except Exception:
        pass
    data[key] = value
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n")
    os.replace(tmp, p)


if __name__ == "__main__":
    if len(sys.argv) == 2 and sys.argv[1] == "rotate-token":
        c = load()
        ok = write_token(c)
        print(f"token stored ({c['token_store']}); value not shown" if ok else "token store failed")
        sys.exit(0 if ok else 1)
    if len(sys.argv) == 3 and sys.argv[1] == "get":
        v = load().get(sys.argv[2], "")
        print(" ".join(v) if isinstance(v, list) else v)
    else:
        sys.exit("usage: config.py get <key>")

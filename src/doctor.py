"""inspi doctor: check every link of the chain and print OK / MISSING with the exact fix.

Never prints the token or an API key. All side effects (subprocess, HTTP, environment, home
directory) are injectable so the checks are unit-tested without a browser, launchd or Keychain.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import urllib.request
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import config as _config  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
LABELS = ["receiver", "watcher", "enricher", "map"]
LABEL_PREFIX = "io.github.wiki-inspi"
PLUGINS = {"obsidian-excalidraw-plugin": "Excalidraw", "dataview": "Dataview", "note-gallery": "Note Gallery"}
VAULT_DIRS = ["Inbox", "Library", "MOCs", "Moodboards", "_attachments"]


@dataclass
class Check:
    name: str
    ok: bool
    fix: str = ""
    detail: str = ""


def _http(method: str, url: str, headers: dict | None = None, timeout: float = 5.0):
    """-> (status, body_text). Network errors raise."""
    req = urllib.request.Request(url, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, ""


def _sh(cmd: list[str]) -> int:
    try:
        return subprocess.run(cmd, capture_output=True, timeout=10).returncode
    except Exception:
        return 127


def last_ok_line(log_file: str) -> str | None:
    try:
        last = None
        for line in Path(log_file).read_text().splitlines():
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if rec.get("decision") == "ok":
                last = rec
        return f"{last['ts']} ({last['detail']})" if last else None
    except OSError:
        return None


def run_checks(cfg: dict, env=None, http=_http, sh=_sh, home: Path | None = None,
               python_version=sys.version_info, which=shutil.which, uid: int | None = None) -> list[Check]:
    env = os.environ if env is None else env
    home = home or Path.home()
    uid = os.getuid() if uid is None else uid
    out: list[Check] = []
    add = lambda *a, **k: out.append(Check(*a, **k))  # noqa: E731
    install = f"cd {REPO} && ./install.sh"

    # 1. python + deps
    py_ok = tuple(python_version[:2]) >= (3, 10)
    add("python >= 3.10", py_ok, "install Python 3.10+ (brew install python)", f"{python_version[0]}.{python_version[1]}")
    for mod, pkg in (("PIL", "Pillow"), ("yaml", "PyYAML")):
        try:
            __import__(mod); ok = True
        except ImportError:
            ok = False
        add(f"python package {pkg}", ok, f"{install} (creates a venv with the dependencies)")

    # 2. config + vault
    cpath = _config.config_path()
    add("config file", cpath.exists(), f"{install}  (writes {cpath})")
    vault = Path(cfg["vault"])
    add("vault folder", vault.is_dir(), f"{install} --vault '{vault}'", str(vault))
    missing = [d for d in VAULT_DIRS if not (vault / d).is_dir()]
    if (vault / "_taxonomy.yml").exists() is False:
        missing.append("_taxonomy.yml")
    add("vault skeleton", vault.is_dir() and not missing, f"{install} --vault '{vault}'  (missing: {', '.join(missing)})")

    # 3. Obsidian community plugins
    pdir = vault / ".obsidian" / "plugins"
    for pid, label in PLUGINS.items():
        add(f"Obsidian plugin {label}", (pdir / pid / "main.js").is_file(),
            f"python3 {REPO}/scripts/install_plugins.py --vault '{vault}'  (then enable it in Obsidian > Settings > Community plugins)")
    try:
        enabled = json.loads((vault / ".obsidian" / "community-plugins.json").read_text())
    except (OSError, ValueError):
        enabled = []
    off = [p for p in PLUGINS if p not in enabled]
    add("plugins listed as enabled", not off, f"python3 {REPO}/scripts/install_plugins.py --vault '{vault}'", ", ".join(off))

    # 4. launchd agents
    for name in LABELS:
        label = f"{LABEL_PREFIX}.{name}"
        add(f"launchd agent {name}", sh(["launchctl", "print", f"gui/{uid}/{label}"]) == 0,
            f"{install}  (or: launchctl bootstrap gui/{uid} ~/Library/LaunchAgents/{label}.plist)")

    # 5. receiver health with token
    ext = cfg.get("extension_id", "")
    token = ""
    try:
        token = _config.read_token(cfg)
    except Exception:
        pass
    add("token stored", bool(token), f"{REPO}/scripts/setup-token.sh   (store: {cfg['token_store']})")
    if not ext:
        add("receiver answers /health", False,
            f"extension not paired yet: {REPO}/scripts/setup-token.sh, then Pair in the extension options")
    elif not token:
        add("receiver answers /health", False, f"{REPO}/scripts/setup-token.sh")
    else:
        try:
            status, _ = http("POST", f"http://127.0.0.1:{cfg['port']}/health",
                             {"Origin": f"chrome-extension://{ext}", "Authorization": f"Bearer {token}"})
            ok, detail = status == 200, f"HTTP {status}"
        except Exception as exc:
            ok, detail = False, type(exc).__name__
        add("receiver answers /health", ok,
            f"start it: launchctl kickstart -k gui/{uid}/{LABEL_PREFIX}.receiver (or run {REPO}/src/receiver.py); "
            f"401 = token mismatch, re-run setup-token.sh and Pair", detail)

    # 6. extension paired
    seen = last_ok_line(cfg["log_file"])
    add("extension paired and used", bool(ext) and seen is not None,
        f"load {REPO}/extension in chrome://extensions, run {REPO}/scripts/setup-token.sh, click Pair then Test",
        seen or "no successful request in the log yet")

    # 7. classifier backend
    for be in [b.strip() for b in cfg["backend"].split(",") if b.strip()]:
        if be == "claude-cli":
            found = env.get("CLAUDE_BIN") or which("claude") or ((home / ".local/bin/claude") if (home / ".local/bin/claude").exists() else None)
            add("backend claude-cli: `claude` installed", bool(found), "install Claude Code (https://claude.com/claude-code), then run `claude` once to log in")
        elif be in ("anthropic-api", "anthropic"):
            plist = home / "Library/LaunchAgents" / f"{LABEL_PREFIX}.enricher.plist"
            try:
                in_plist = "ANTHROPIC_API_KEY" in plist.read_text()
            except OSError:
                in_plist = False
            try:
                import anthropic  # noqa: F401
                has_pkg = True
            except ImportError:
                has_pkg = False
            add("backend anthropic-api: `anthropic` package", has_pkg, "rerun ./install.sh (it installs requirements.txt into the venv)")
            add("backend anthropic-api: ANTHROPIC_API_KEY", bool(env.get("ANTHROPIC_API_KEY")) or in_plist,
                "export ANTHROPIC_API_KEY in your shell and add it to EnvironmentVariables of the launchd plists (never in the repo)")
        elif be == "ollama":
            model = cfg["ollama_model"]
            try:
                status, body = http("GET", "http://localhost:11434/api/tags")
                names = [m.get("name", "") for m in json.loads(body).get("models", [])] if status == 200 else []
                ok = any(n == model or n.split(":")[0] == model for n in names)
                fix = f"ollama pull {model}"
            except Exception:
                ok, fix = False, "start Ollama (open the app or `ollama serve`)"
            add(f"backend ollama: model {model}", ok, fix)
        elif be == "gemini":
            add("backend gemini: GEMINI_API_KEY", bool(env.get("GEMINI_API_KEY") or env.get("GOOGLE_API_KEY")), "export GEMINI_API_KEY")
        else:
            add(f"backend {be}", False, "use claude-cli, anthropic-api or ollama in config.json")
    return out


def render(checks: list[Check]) -> str:
    lines = []
    for c in checks:
        tail = f"  ({c.detail})" if c.detail and c.ok else ""
        lines.append(f"OK       {c.name}{tail}" if c.ok else f"MISSING  {c.name}\n           fix: {c.fix}" + (f"\n           ({c.detail})" if c.detail else ""))
    bad = sum(1 for c in checks if not c.ok)
    lines.append("" if not bad else "")
    lines.append("All good." if not bad else f"{bad} item(s) missing.")
    return "\n".join(lines)


def main() -> int:
    cfg = _config.load()
    checks = run_checks(cfg)
    print(render(checks))
    return 1 if any(not c.ok for c in checks) else 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Install the Obsidian community plugins Wiki Inspi needs, from their official GitHub releases.

The repository of each plugin is looked up by id in Obsidian's official registry
(obsidianmd/obsidian-releases/community-plugins.json), so no repo name is hard-coded here.
Downloads main.js, manifest.json and (when present) styles.css of the latest release into
<vault>/.obsidian/plugins/<id>/ and lists the id in community-plugins.json.
The human still has to turn on "Community plugins" in Obsidian once.

    python3 scripts/install_plugins.py --vault PATH
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from pathlib import Path

REGISTRY = "https://raw.githubusercontent.com/obsidianmd/obsidian-releases/master/community-plugins.json"
PLUGIN_IDS = ["obsidian-excalidraw-plugin", "dataview", "note-gallery"]
ASSETS = [("main.js", True), ("manifest.json", True), ("styles.css", False)]


def fetch(url: str) -> bytes | None:
    """Bytes, or None on 404. Other errors raise."""
    try:
        with urllib.request.urlopen(url, timeout=30) as r:
            return r.read()
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise


def install(vault: Path, ids=PLUGIN_IDS, get=fetch) -> dict[str, str]:
    registry = json.loads((get(REGISTRY) or b"[]").decode())
    repos = {p["id"]: p["repo"] for p in registry}
    result: dict[str, str] = {}
    pdir = vault / ".obsidian" / "plugins"
    for pid in ids:
        repo = repos.get(pid)
        if not repo:
            result[pid] = "not in the official registry"
            continue
        files: dict[str, bytes] = {}
        for name, required in ASSETS:
            data = get(f"https://github.com/{repo}/releases/latest/download/{name}")
            if data is None and required:
                break
            if data is not None:
                files[name] = data
        else:
            dest = pdir / pid
            dest.mkdir(parents=True, exist_ok=True)
            for name, data in files.items():
                (dest / name).write_bytes(data)
            result[pid] = f"installed from {repo}"
            continue
        result[pid] = f"release of {repo} has no {name}"
    cp = vault / ".obsidian" / "community-plugins.json"
    try:
        listed = json.loads(cp.read_text())
    except (OSError, ValueError):
        listed = []
    for pid, status in result.items():
        if status.startswith("installed") and pid not in listed:
            listed.append(pid)
    cp.parent.mkdir(parents=True, exist_ok=True)
    cp.write_text(json.dumps(listed, indent=2) + "\n")
    return result


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--vault", required=True)
    a = ap.parse_args()
    res = install(Path(a.vault).expanduser())
    for pid, status in res.items():
        print(f"{pid}: {status}")
    return 0 if all(s.startswith("installed") for s in res.values()) else 1


if __name__ == "__main__":
    sys.exit(main())

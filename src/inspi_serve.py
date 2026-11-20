#!/usr/bin/env python3
"""inspi serve: the gallery page on http://127.0.0.1:18766, plus POST /board to write a moodboard.

Local only. Every request must carry Host == 127.0.0.1:PORT (DNS rebinding); POST also needs
Origin == http://127.0.0.1:PORT and Content-Type: application/json (other web pages are refused,
no CORS headers are ever sent). Files served: the map folder and the vault's _attachments only.
Writes: Moodboards/<safe name>.excalidraw.md (new, or appended after a backup in
~/.local/state/wiki-inspi/board-backups/); /delete moves Library/ or Inbox/ notes, and images
nothing else uses, to <vault>/.trash/wiki-inspi/<batch>/ (logged in .wiki-inspi/deletions.jsonl);
/restore moves a batch back without overwriting.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import time
import mimetypes
import os
import sys
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import inspi_board_add as BA  # noqa: E402
import inspilib as L  # noqa: E402

VAULT = Path(os.environ.get("INSPI_VAULT", str(L.VAULT)))
import config as _config  # noqa: E402

PORT = int(os.environ.get("INSPI_MAP_PORT") or _config.load().get("map_port", 18766))
HOST = f"127.0.0.1:{PORT}"
ORIGIN = f"http://{HOST}"
MAP = VAULT / ".wiki-inspi" / "map"
ATT = VAULT / "_attachments"
MAX_BODY = 200_000
MAX_IMAGES = 60
TRASH = VAULT / ".trash" / "wiki-inspi"
EMBED = re.compile(r"(?:thumbnail:\s*\"?|\[\[)(_attachments/[^\]\"\n|]+)")


def _rebuild():
    subprocess.Popen([sys.executable, str(Path(__file__).resolve().parents[1] / "bin" / "inspi"), "map", "--no-open"],
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _corpus(skip: set[Path]) -> list[str]:
    """Text of every note and moodboard that stays (not being deleted)."""
    out = []
    for f in VAULT.rglob("*.md"):
        if f in skip or ".trash" in f.parts or ".obsidian" in f.parts:
            continue
        try:
            out.append(f.read_text(errors="ignore"))
        except OSError:
            pass
    return out


def delete_notes(rels: list[str]) -> dict:
    batch = time.strftime("%Y%m%d-%H%M%S") + f"-{int(time.time() * 1000) % 1000:03d}"
    dest = TRASH / batch
    notes = []
    for r in rels:
        p = _inside(VAULT, r)
        if not p or p.suffix != ".md":
            continue
        r = str(p.relative_to(VAULT.resolve()))  # judged after resolving: Library/../x is refused
        if p.parts and r.split("/")[0] in ("Library", "Inbox"):
            notes.append((r, p))
    skip = {p for _, p in notes}
    corpus = _corpus(skip)
    moved = []
    for r, p in notes:
        atts = set(EMBED.findall(p.read_text(errors="ignore")))
        (dest / r).parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(p), str(dest / r))
        moved.append(r)
        for a in atts:
            ap = _inside(VAULT, a)
            if ap and not any(a in t for t in corpus):
                (dest / a).parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(ap), str(dest / a))
                moved.append(a)
    with (VAULT / ".wiki-inspi" / "deletions.jsonl").open("a") as fh:
        fh.write(json.dumps({"ts": time.time(), "batch": batch, "moved": moved}) + "\n")
    if moved:
        _rebuild()
    return {"batch": batch, "notes": len(notes), "moved": moved}


def restore(batch: str) -> dict:
    if not re.fullmatch(r"[0-9-]{19,24}", batch or ""):
        raise ValueError("bad batch")
    src = TRASH / batch
    back = []
    for f in sorted(src.rglob("*")):
        if f.is_file():
            rel = f.relative_to(src)
            target = VAULT / rel
            if target.exists():
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(f), str(target))
            back.append(str(rel))
    _rebuild()
    return {"restored": back}


def _inside(root: Path, rel: str) -> Path | None:
    p = (root / rel).resolve()
    try:
        p.relative_to(root.resolve())
    except ValueError:
        return None
    return p if p.is_file() else None


class H(BaseHTTPRequestHandler):
    server_version = "inspi"

    def log_message(self, fmt, *args):  # quiet: launchd log only gets errors
        pass

    def _send(self, code, body=b"", ctype="text/plain; charset=utf-8", extra=None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Content-Type-Options", "nosniff")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _host_ok(self):
        if self.headers.get("Host") != HOST:
            self._send(403, b"bad host")
            return False
        return True

    def do_GET(self):
        if not self._host_ok():
            return
        path = urllib.parse.unquote(urllib.parse.urlsplit(self.path).path).lstrip("/")
        if path == "boards":
            return self._send(200, json.dumps(BA.list_boards(VAULT)).encode(), "application/json")
        if path in ("", "familles"):
            path = "index.html"
        if path.startswith("_attachments/"):
            f = _inside(ATT, path[len("_attachments/") :])
        else:
            f = _inside(MAP, path)
        if not f:
            return self._send(404, b"not found")
        ctype = mimetypes.guess_type(f.name)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype.endswith("javascript"):
            ctype += "; charset=utf-8"
        self._send(200, f.read_bytes(), ctype)

    do_HEAD = do_GET

    def do_POST(self):
        if not self._host_ok():
            return
        if self.headers.get("Origin") != ORIGIN:
            return self._send(403, b"bad origin")
        if not (self.headers.get("Content-Type") or "").startswith("application/json"):
            return self._send(415, b"json only")
        route = urllib.parse.urlsplit(self.path).path
        if route not in ("/board", "/delete", "/restore"):
            return self._send(404, b"not found")
        try:
            n = int(self.headers.get("Content-Length") or 0)
            if n <= 0 or n > MAX_BODY:
                raise ValueError("bad body size")
            req = json.loads(self.rfile.read(n))
            name = L.safe_board_name(str(req.get("name") or "")) if route == "/board" else ""
            rels = [str(r) for r in req.get("notes") or []][:MAX_IMAGES]
            title = str(req.get("title") or name)[:120]
        except (ValueError, TypeError, json.JSONDecodeError) as e:
            return self._send(
                400, json.dumps({"error": str(e)}).encode(), "application/json"
            )
        if route == "/delete":
            return self._send(200, json.dumps(delete_notes([str(r) for r in req.get("notes") or []][:500])).encode(), "application/json")
        if route == "/restore":
            try:
                return self._send(200, json.dumps(restore(str(req.get("batch")))).encode(), "application/json")
            except ValueError as e:
                return self._send(400, json.dumps({"error": str(e)}).encode(), "application/json")
        by_rel = {n["rel"]: n for n in L.load_notes(VAULT, ("Library", "Inbox"))}
        notes = [by_rel[r] for r in rels if r in by_rel]
        if not notes:
            return self._send(
                400, b'{"error": "no known image in the request"}', "application/json"
            )
        try:
            if req.get("mode") == "add":
                out, placed, dup = BA.append(VAULT, name, notes)
                skipped = [None] * dup
            else:
                if (VAULT / "Moodboards" / f"{name}.excalidraw.md").exists() and not req.get("replace"):
                    return self._send(409, json.dumps({"error": f"a moodboard named {name} already exists: use Add to, or pick another name"}).encode(), "application/json")
                out, placed, skipped = L.write_board(VAULT, name, notes, title=title)
        except ValueError as e:
            return self._send(400, json.dumps({"error": str(e)}).encode(), "application/json")
        uri = L.obsidian_uri(VAULT.name, str(out.relative_to(VAULT)))
        body = {
            "file": str(out.relative_to(VAULT)),
            "placed": placed,
            "skipped": len(skipped),
            "open": uri,
        }
        self._send(200, json.dumps(body).encode(), "application/json")


def main():
    ThreadingHTTPServer(("127.0.0.1", PORT), H).serve_forever()


if __name__ == "__main__":
    main()

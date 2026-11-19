#!/usr/bin/env python3
"""
Wiki Inspi receiver: localhost-only HTTP endpoint for the right-click capture extension.

Threat model (see README): any web page can make the browser send requests to
127.0.0.1. So every request must pass, in this order:
  1. Host header == 127.0.0.1:<port>          (blocks DNS rebinding)
  2. Origin == chrome-extension://<paired id> (blocks web pages and other extensions)
  3. Authorization: Bearer <token>            (token read from the macOS Keychain per
                                               request, hmac.compare_digest, never printed)
No CORS headers are ever sent, so a page cannot read a response or pass preflight.
The server never fetches a URL given by the client, with ONE exception: a Pinterest pin
URL (https, Pinterest host, pinimg.com images only) fetched through pinlib to get the HD
original. Image bytes come from the extension (fetched in-page, with the user's cookies).

Every refusal is appended to <log_dir>/receiver.log as JSON lines (best-effort).
There is no bypass flag.

The extension id is not shipped: it is learned at pairing (trust on first use, inside the
60 s window opened by scripts/setup-token.sh) or set up front in the config file.

stdlib only; PIL is used for verification when importable.
"""

from __future__ import annotations

import email
import email.policy
import hashlib
import hmac
import json
import os
import re
import secrets as _secrets
import subprocess
import sys
import threading
import time
import unicodedata
import urllib.parse
from datetime import date, datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

try:  # optional
    from PIL import Image
except Exception:  # pragma: no cover
    Image = None

import config as _config  # noqa: E402

MAX_IMAGE_BYTES = 25 * 1024 * 1024
MAX_BODY_BYTES = MAX_IMAGE_BYTES + 256 * 1024  # image + text fields + multipart framing
MAX_PIXELS = 120_000_000
PAIR_WINDOW_SECONDS = 60
KEYCHAIN_SERVICE = _config.KEYCHAIN_SERVICE
GUARD = "wiki-inspi-receiver"
EXT_ORIGIN_RE = re.compile(r"^chrome-extension://([a-p]{32})$")

HOME = Path.home()
DEFAULTS = _config.DEFAULTS
CONFIG_PATH = _config.config_path()


def load_config(path: Path | None = None) -> dict:
    return _config.load(path)


# ---------------------------------------------------------------- helpers


def keychain_token(service: str = KEYCHAIN_SERVICE) -> str:
    """Read the token from the Keychain; the value is never printed or logged."""
    return _config.read_token({"token_store": "keychain", "keychain_service": service})


def ledger(path: str | None, decision: str, detail: str) -> None:
    try:
        if not path:
            return
        line = json.dumps(
            {
                "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "source": GUARD,
                "decision": decision,
                "detail": detail[:300],
            }
        )
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a") as f:
            f.write(line + "\n")
    except Exception:
        pass


def sniff_image(data: bytes) -> str | None:
    """Extension from magic bytes, never from anything the client says."""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if data.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    return None


def verify_image(data: bytes) -> str:
    ext = sniff_image(data)
    if not ext:
        raise Refusal(415, "not-an-image", "bytes are not PNG/JPEG/GIF/WEBP")
    if Image is not None:
        try:
            import io

            with Image.open(io.BytesIO(data)) as im:
                w, h = im.size
                if w * h > MAX_PIXELS:
                    raise Refusal(413, "too-many-pixels", f"{w}x{h}")
                im.verify()
        except Refusal:
            raise
        except Exception as exc:
            raise Refusal(415, "corrupt-image", type(exc).__name__)
    return ext


def clean_line(s: object, limit: int) -> str:
    """Page text -> single safe line (no control chars, no newlines)."""
    s = unicodedata.normalize("NFC", str(s or ""))
    s = "".join(
        " " if unicodedata.category(c) in ("Cc", "Cf", "Zl", "Zp") else c for c in s
    )
    s = re.sub(r"\s+", " ", s).strip()
    return s[:limit]


def md_text(s: str) -> str:
    """Defang Obsidian link/embed syntax and code fences in free text placed in the body."""
    return s.replace("[[", "[ [").replace("]]", "] ]").replace("```", "'''")


def clean_url(s: object) -> str:
    u = clean_line(s, 2048)
    p = urllib.parse.urlparse(u)
    return u if p.scheme in ("http", "https") and p.netloc else ""


def slugify(s: str) -> str:
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    s = re.sub(r"[^\w\s-]", "", s.lower()).strip()
    s = re.sub(r"[-\s_]+", "-", s).strip("-")
    return s[:60].strip("-") or "capture"


def q(s: str) -> str:
    """YAML double-quoted scalar. json.dumps output is valid YAML and escapes newlines/quotes."""
    return json.dumps(s, ensure_ascii=True)


class Refusal(Exception):
    def __init__(self, status: int, code: str, detail: str = ""):
        super().__init__(code)
        self.status, self.code, self.detail = status, code, detail


# ---------------------------------------------------------------- vault writing

_WRITE_LOCK = threading.Lock()


def confined(vault_real: str, *parts: str) -> Path:
    """Resolve a path and assert it stays under the vault's REAL path."""
    p = Path(os.path.realpath(os.path.join(vault_real, *parts)))
    if not (str(p) == vault_real or str(p).startswith(vault_real + os.sep)):
        raise Refusal(400, "path-escape", str(p))
    return p


def sha_index_path(vault_real: str) -> Path:
    return Path(vault_real) / ".wiki-inspi" / "sha-index.json"


def load_index(vault_real: str) -> dict:
    try:
        return json.loads(sha_index_path(vault_real).read_text())
    except Exception:
        return {}


def save_index(vault_real: str, idx: dict) -> None:
    p = sha_index_path(vault_real)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(idx, indent=0, sort_keys=True))
    os.replace(tmp, p)


def find_existing_note(vault_real: str, rel: str) -> Path | None:
    """The enricher moves notes Inbox -> Library, so a stale index path is searched by name."""
    cand = confined(vault_real, rel)
    if cand.is_file():
        return cand
    name = Path(rel).name
    for sub in ("Inbox", "Library"):
        base = Path(vault_real) / sub
        if base.is_dir():
            for hit in base.rglob(name):
                if hit.is_file():
                    return Path(os.path.realpath(hit))
    return None


def write_new(path: Path, data: bytes) -> None:
    """Create exclusively (never overwrite), atomically visible."""
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with open(tmp, "wb") as f:
        f.write(data)
    try:
        os.link(tmp, path)  # fails if path exists
    finally:
        os.unlink(tmp)


def unique_names(vault_real: str, slug: str, ext: str) -> tuple[Path, Path, str]:
    for n in range(1, 1000):
        s = slug if n == 1 else f"{slug}-{n}"
        att = confined(vault_real, "_attachments", f"{s}.{ext}")
        note = confined(vault_real, "Inbox", f"{s}.md")
        if not att.exists() and not note.exists():
            return att, note, s
    raise Refusal(500, "no-free-name")


def md_url(u: str) -> str:
    """http(s) URL -> safe inside a markdown ![](<...>) destination (no <, >, space, brackets, backtick, parens)."""
    return urllib.parse.quote(u, safe=":/?&=%#+@!$'*,;~")


def build_note(slug: str, ext: str, sha: str, meta: dict, pin: dict | None, remote: bool = False) -> str:
    title = meta["title"] or meta["alt"] or "Capture"
    fm = [
        "---",
        "type: image",
        f"captured: {date.today().isoformat()}",
        "ingest_source: extension",
        f"source_url: {q(meta['src_url'])}",
        f"page_url: {q(meta['page_url'])}",
        f"page_title: {q(meta['title'])}",
        f"alt: {q(meta['alt'])}",
        f"author: {q(meta['author'])}",
        f"site_name: {q(meta['site_name'])}",
        f"sha256: {q(sha)}",
        f"image_local: {'false' if remote else 'true'}",
    ]
    if remote:
        fm.append(f"image_url: {q(meta['src_url'])}")
    else:
        fm.append(f"thumbnail: {q(f'_attachments/{slug}.{ext}')}")
    if pin:
        for k in ("board_name", "board_url", "source_link", "pin_id"):
            if pin.get(k):
                fm.append(f"pinterest_{k}: {q(clean_line(pin[k], 2048))}")
    fm += ["---", ""]
    embed = f"![](<{md_url(meta['src_url'])}>)" if remote else f"![[_attachments/{slug}.{ext}]]"
    body = [f"# {md_text(title)}", "", embed, ""]
    if meta["alt"]:
        body += ["## Alt", "", md_text(meta["alt"]), ""]
    body += ["## Source", ""]
    if meta["page_url"]:
        body.append(f"- Page: {md_text(meta['page_url'])}")
    if meta["src_url"] and not meta["src_url"].startswith("data:"):
        body.append(f"- Image: {md_text(meta['src_url'])}")
    body.append("")
    return "\n".join(fm + body)


def append_source(note: Path, meta: dict) -> None:
    line = f"- Also captured {date.today().isoformat()}: {md_text(meta['page_url'] or '(no page url)')}"
    text = note.read_text(encoding="utf-8", errors="replace")
    if "\n## Also captured from\n" not in text:
        text = text.rstrip("\n") + "\n\n## Also captured from\n\n"
    else:
        text = text.rstrip("\n") + "\n"
    note.write_text(text + line + "\n", encoding="utf-8")


def pin_original(cfg: dict, pin_url: str) -> tuple[bytes, dict]:
    """HD original + pin metadata through pinlib."""
    import pinlib

    if pinlib.yaml is None:
        raise Refusal(500, "pinlib-needs-pyyaml")
    html, final = pinlib.fetch_pin_checked(pin_url)
    data = pinlib.extract_pin_data(html, final)
    if not data.get("image_url"):
        raise ValueError("no image on pin page")
    return pinlib.fetch_original_bytes(data["image_url"]), data


def process_capture(cfg: dict, fields: dict, image: bytes) -> dict:
    vault_real = cfg["_vault_real"]
    raw_src = str(fields.get("src_url", ""))
    src_url = "(inline image)" if raw_src.startswith(("data:", "blob:")) else clean_url(raw_src)
    meta = {
        "src_url": src_url,
        "page_url": clean_url(fields.get("page_url")),
        "title": clean_line(fields.get("page_title"), 300),
        "alt": clean_line(fields.get("alt"), 500),
        "author": clean_line(fields.get("author"), 200),
        "site_name": clean_line(fields.get("site_name"), 200),
    }
    display_url = clean_url(raw_src)  # http(s) only: the only thing display-only mode will ever embed
    display_only = fields.get("display_only") in ("1", "true", "True") and bool(display_url)
    pin = None
    pin_url = (
        clean_url(fields.get("pin_url"))
        if fields.get("pinterest") in ("1", "true", "True")
        else ""
    )
    if pin_url:
        try:
            image, pin = pin_original(cfg, pin_url)
            meta["page_url"] = meta["page_url"] or pin_url
            meta["title"] = meta["title"] or clean_line(pin.get("title"), 300)
        except Refusal:
            raise
        except Exception:
            if not image and not display_only:
                raise Refusal(502, "pin-fetch-failed")
            pin = None  # HD failed: keep the bytes the extension sent (or display-only)
    if not image and display_only:
        return process_remote(cfg, meta, display_url)
    if not image:
        raise Refusal(400, "no-image")
    if len(image) > MAX_IMAGE_BYTES:
        raise Refusal(413, "image-too-large", str(len(image)))
    ext = verify_image(image)
    sha = hashlib.sha256(image).hexdigest()

    with _WRITE_LOCK:
        idx = load_index(vault_real)
        if sha in idx:
            existing = find_existing_note(vault_real, idx[sha]["note"])
            if existing:
                append_source(existing, meta)
                return {
                    "status": "duplicate",
                    "mode": "local",
                    "note": str(existing.relative_to(vault_real)),
                    "sha256": sha,
                }
        slug = f"{slugify(meta['title'] or meta['alt'])}-{sha[:8]}"
        att, note, slug = unique_names(vault_real, slug, ext)
        att.parent.mkdir(parents=True, exist_ok=True)
        note.parent.mkdir(parents=True, exist_ok=True)
        write_new(att, image)
        try:
            write_new(note, build_note(slug, ext, sha, meta, pin).encode("utf-8"))
        except Exception:
            att.unlink(missing_ok=True)
            raise
        rel = str(note.relative_to(vault_real))
        idx[sha] = {"note": rel, "attachment": str(att.relative_to(vault_real))}
        save_index(vault_real, idx)
    trigger_enrich(cfg, note)
    return {"status": "created", "mode": "local", "note": rel, "sha256": sha}


def process_remote(cfg: dict, meta: dict, url: str) -> dict:
    """Display-only: the extension could not get the bytes. Note only, embed by remote URL.
    The receiver never fetches `url`; clean_url already guarantees http(s)."""
    vault_real = cfg["_vault_real"]
    key = "url:" + hashlib.sha256(url.encode()).hexdigest()
    with _WRITE_LOCK:
        idx = load_index(vault_real)
        if key in idx:
            existing = find_existing_note(vault_real, idx[key]["note"])
            if existing:
                append_source(existing, meta)
                return {"status": "duplicate", "mode": "remote",
                        "note": str(existing.relative_to(vault_real))}
        slug = f"{slugify(meta['title'] or meta['alt'])}-{key[4:12]}"
        for n in range(1, 1000):
            s = slug if n == 1 else f"{slug}-{n}"
            note = confined(vault_real, "Inbox", f"{s}.md")
            if not note.exists():
                break
        else:
            raise Refusal(500, "no-free-name")
        note.parent.mkdir(parents=True, exist_ok=True)
        write_new(note, build_note(s, "", key[4:], meta, None, remote=True).encode("utf-8"))
        rel = str(note.relative_to(vault_real))
        idx[key] = {"note": rel, "attachment": ""}
        save_index(vault_real, idx)
    trigger_enrich(cfg, note)
    return {"status": "created", "mode": "remote", "note": rel}


def trigger_enrich(cfg: dict, note: Path) -> None:
    """Fire-and-forget: `<enrich_cmd> --one <note>` (scripts/enrich.sh). Log: <log_dir>/enrich.log."""
    cmd = cfg.get("enrich_cmd")
    if not cmd:
        return
    try:
        log = Path(cfg["log_dir"]) / "receiver-enrich.log"
        log.parent.mkdir(parents=True, exist_ok=True)
        env = dict(os.environ, PYTHON=sys.executable, INSPI_VAULT=cfg["_vault_real"])
        with open(log, "ab") as lf:
            subprocess.Popen(
                [*cmd, "--one", str(note)],
                stdout=lf,
                stderr=lf,
                stdin=subprocess.DEVNULL,
                start_new_session=True,
                env=env,
            )
    except Exception as exc:
        ledger(cfg.get("log_file"), "error", f"enrich-trigger-failed:{type(exc).__name__}")


def parse_multipart(content_type: str, body: bytes) -> tuple[dict, bytes]:
    msg = email.message_from_bytes(
        b"Content-Type: "
        + content_type.encode("latin-1", "replace")
        + b"\r\nMIME-Version: 1.0\r\n\r\n"
        + body,
        policy=email.policy.HTTP,
    )
    if not msg.is_multipart():
        raise Refusal(400, "not-multipart")
    fields: dict[str, str] = {}
    image = b""
    for part in msg.iter_parts():
        name = part.get_param("name", header="content-disposition")
        payload = part.get_payload(decode=True) or b""
        if name == "image":
            image = payload
        elif name:
            fields[name] = payload.decode("utf-8", "replace")
    return fields, image


# ---------------------------------------------------------------- HTTP


class Handler(BaseHTTPRequestHandler):
    server_version = "wiki-inspi"
    sys_version = ""
    timeout = 30  # slow clients cannot hold a thread forever
    protocol_version = "HTTP/1.0"

    def log_message(self, fmt, *args):  # no request lines with page URLs on stderr
        pass

    cfg: dict
    token_provider = staticmethod(keychain_token)  # replaced per server in make_server

    def _send(self, status: int, obj: dict) -> None:
        body = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()  # deliberately NO Access-Control-* headers
        self.wfile.write(body)

    def _refuse(self, status: int, code: str, detail: str = "") -> None:
        ledger(
            self.cfg.get("log_file"),
            "block",
            f"{code} {self.command} {self.path.split('?')[0]} {detail}".strip(),
        )
        self._send(status, {"ok": False, "error": code})

    def _gate(self, need_token: bool = True, learn_id: bool = False) -> bool:
        cfg = self.cfg
        if self.headers.get("Host", "") != f"127.0.0.1:{cfg['port']}":
            self._refuse(403, "bad-host", clean_line(self.headers.get("Host", ""), 60))
            return False
        if learn_id and not cfg.get("extension_id"):
            return True  # first pairing: the id is learned by _pair, after the window check
        if not cfg.get("extension_id") or (
            self.headers.get("Origin", "")
            != f"chrome-extension://{cfg['extension_id']}"
        ):
            self._refuse(
                403, "bad-origin", clean_line(self.headers.get("Origin", ""), 80)
            )
            return False
        if need_token:
            expected = self.token_provider()
            if not expected:
                self._refuse(503, "token-unavailable")
                return False
            m = re.match(r"^Bearer (.+)$", self.headers.get("Authorization", ""))
            given = m.group(1) if m else ""
            if not hmac.compare_digest(given.encode(), expected.encode()):
                self._refuse(401, "bad-token")
                return False
        return True

    def do_OPTIONS(self):
        self._refuse(403, "preflight-refused")

    def do_GET(self):
        if self.path == "/health":
            if self._gate():
                ledger(self.cfg.get("log_file"), "ok", "health")
                self._send(200, {"ok": True})
            return
        self._refuse(404, "not-found")

    def do_POST(self):
        if self.path == "/pair":
            return self._pair()
        if self.path == "/health":  # POST: Brave sends no Origin on an extension GET
            return self.do_GET()
        if self.path != "/capture":
            return self._refuse(404, "not-found")
        if not self._gate():
            return
        try:
            if self.headers.get("Transfer-Encoding"):
                raise Refusal(411, "chunked-not-supported")
            try:
                length = int(self.headers.get("Content-Length", ""))
            except ValueError:
                raise Refusal(411, "length-required")
            if length < 0 or length > MAX_BODY_BYTES:
                raise Refusal(413, "body-too-large", str(length))
            ctype = self.headers.get("Content-Type", "")
            if not ctype.lower().startswith("multipart/form-data"):
                raise Refusal(415, "not-multipart")
            body = self.rfile.read(length)
            if len(body) != length:
                raise Refusal(400, "short-body")
            fields, image = parse_multipart(ctype, body)
            result = process_capture(self.cfg, fields, image)
            ledger(self.cfg.get("log_file"), "ok", f"capture {result.get('status')}")
            self._send(200, {"ok": True, **result})
        except Refusal as r:
            self._refuse(r.status, r.code, r.detail)
        except Exception as exc:
            self._refuse(500, "internal", type(exc).__name__)

    def _pair(self):
        """One-shot token hand-over, only inside the window opened by scripts/setup-token.sh.
        With no extension id configured yet, the id of the extension that pairs inside the
        window is stored (trust on first use) and is the only one accepted from then on."""
        if not self._gate(need_token=False, learn_id=True):
            return
        pf = Path(self.cfg["pair_file"])
        try:
            age = time.time() - pf.stat().st_mtime
        except OSError:
            return self._refuse(403, "pair-window-closed")
        if age > PAIR_WINDOW_SECONDS:
            pf.unlink(missing_ok=True)
            return self._refuse(403, "pair-window-expired")
        if not self.cfg.get("extension_id"):
            m = EXT_ORIGIN_RE.match(self.headers.get("Origin", ""))
            if not m:
                return self._refuse(403, "bad-origin", clean_line(self.headers.get("Origin", ""), 80))
            self.cfg["extension_id"] = m.group(1)
            try:
                _config.save_key("extension_id", m.group(1), self.cfg.get("_config_path"))
            except Exception as exc:
                ledger(self.cfg.get("log_file"), "error", f"config-save-failed:{type(exc).__name__}")
        token = self.token_provider()
        if not token:
            return self._refuse(503, "token-unavailable")
        pf.unlink(missing_ok=True)  # one-shot
        ledger(self.cfg.get("log_file"), "ok", "pair")
        self._send(200, {"ok": True, "token": token})


def make_server(cfg: dict, token_provider=None) -> ThreadingHTTPServer:
    if token_provider is None:
        full = dict(_config.DEFAULTS, **cfg)
        token_provider = lambda: _config.read_token(full)  # noqa: E731
    vault = os.path.normpath(cfg["vault"])
    real = os.path.realpath(vault)
    if real != vault:
        raise SystemExit(f"vault must be a real path (no symlink): {vault} -> {real}")
    if not os.path.isdir(real):
        raise SystemExit(f"vault not found: {real}")
    cfg = dict(cfg, _vault_real=real)
    cfg.setdefault("log_file", str(Path(cfg["log_dir"]) / "receiver.log"))
    handler = type(
        "BoundHandler",
        (Handler,),
        {"cfg": cfg, "token_provider": staticmethod(token_provider)},
    )
    ThreadingHTTPServer.allow_reuse_address = True
    srv = ThreadingHTTPServer(("127.0.0.1", int(cfg["port"])), handler)
    srv.daemon_threads = True
    return srv


def main() -> int:
    cfg = load_config()
    cfg["_config_path"] = str(_config.config_path())
    srv = make_server(cfg)
    print(
        f"wiki-inspi receiver on 127.0.0.1:{cfg['port']} vault={cfg['vault']} "
        f"extension={'paired' if cfg.get('extension_id') else 'not paired yet'}",
        flush=True,
    )
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())

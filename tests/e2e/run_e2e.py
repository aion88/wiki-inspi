#!/usr/bin/env python3
"""
Wiki Inspi e2e on live sites. Real Chromium + the real unpacked extension in a FRESH temp profile
(never a live profile), receiver in-process on a free port with a temp vault (path has a space),
test token, enrich trigger disabled. The context-menu click is not scriptable, so the SAME handler
the menu calls (handleClick in background.js) is invoked in the service worker for a real tab.

Run:  python3 tests/e2e/run_e2e.py [--site pinterest,behance,arena,commons,unsplash,hotlink-sim] [--headed]
Out:  tests/e2e/out/<date>/<site>/{page.png,image.*,note.md}  +  summary.md
"""
from __future__ import annotations

import argparse, hashlib, http.server, io, json, os, re, shutil, socket, sys, tempfile, threading, time
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
import receiver  # noqa: E402
from PIL import Image  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402

EXT_ID = ""  # filled once the unpacked extension is loaded (its id depends on the temp path)
TOKEN = "e2e-test-token-" + "x" * 24
OUT = Path(__file__).parent / "out" / date.today().isoformat()
UA_BLOCK = re.compile(r"just a moment|access denied|captcha|verify you are human|are you a robot|unusual traffic", re.I)


def free_port() -> int:
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p


class Rx:
    """Receiver on its own port + temp vault, one per site (clean counts)."""
    def __init__(self, name: str):
        self.base = Path(os.path.realpath(tempfile.mkdtemp(prefix="wiki-inspi-e2e-")))
        self.vault = self.base / f"Wiki Inspi E2E {name}"
        (self.vault / "Inbox").mkdir(parents=True)
        self.port = free_port()
        cfg = dict(receiver.DEFAULTS, port=self.port, vault=str(self.vault), log_dir=str(self.base),
                   pair_file=str(self.base / "pair-open"), enrich_cmd=None, extension_id=EXT_ID)
        self.srv = receiver.make_server(cfg, token_provider=lambda: TOKEN)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def notes(self): return sorted((self.vault / "Inbox").glob("*.md")) + sorted((self.vault / "Library").glob("*.md"))
    def atts(self): return sorted((self.vault / "_attachments").glob("*")) if (self.vault / "_attachments").is_dir() else []
    def close(self):
        self.srv.shutdown(); self.srv.server_close(); shutil.rmtree(self.base, ignore_errors=True)


def frontmatter(text: str) -> dict:
    fm = {}
    for line in text.split("---\n")[1].splitlines():
        k, _, v = line.partition(": ")
        try: fm[k] = json.loads(v)
        except Exception: fm[k] = v
    return fm


PICK_IMG = """() => {
  let best = null, area = 0;
  for (const im of document.images) {
    const r = im.getBoundingClientRect();
    const src = im.currentSrc || im.src;
    if (!/^https?:/.test(src) || !im.complete || r.width < 150 || r.height < 150 || !im.naturalWidth) continue;
    const a = r.width * r.height;
    if (a > area) { area = a; best = im; }
  }
  if (!best) return null;
  best.scrollIntoView({block: 'center'});
  return { src: best.currentSrc || best.src, w: best.naturalWidth, h: best.naturalHeight };
}"""

CLICK = """async ({srcUrl, pageUrl}) => {
  const tabs = await chrome.tabs.query({});
  const tab = tabs.find(t => t.url === pageUrl) || tabs.find(t => (t.url || '').split('#')[0] === pageUrl.split('#')[0]);
  if (!tab) return { ok: false, message: 'e2e: tab not found for ' + pageUrl };
  return await handleClick({ srcUrl, pageUrl, frameId: 0, menuItemId: 'wiki-inspi-send' }, tab);
}"""


class Blocked(Exception):
    pass


def goto(page, url):
    """goto that reports a site-side refusal (403/429/HTTP error page) as a bot wall. We never try to get around one."""
    try:
        r = page.goto(url, wait_until="domcontentloaded", timeout=45000)
    except Exception as e:
        if "ERR_HTTP_RESPONSE_CODE_FAILURE" in str(e): raise Blocked(f"HTTP error response from {url} (headless refused)")
        raise
    if r is not None and r.status in (401, 403, 429): raise Blocked(f"HTTP {r.status} from {url} (headless refused)")
    return r


def discover(page, listing: str, selector: str, must=None) -> str:
    goto(page, listing)
    page.wait_for_timeout(4000)
    for _ in range(3):
        hrefs = page.eval_on_selector_all(selector, "els => els.map(e => e.href)")
        hrefs = [h for h in hrefs if (not must or re.search(must, h))]
        if hrefs: return hrefs[0]
        page.mouse.wheel(0, 1500); page.wait_for_timeout(1500)
    raise RuntimeError(f"no link matching {selector} on {listing}")


def discover_pin(page) -> str:
    """Logged-out Pinterest has no /pin/ hrefs in the grid; the pin ids are in the page JSON."""
    page.goto(os.environ.get("E2E_PINTEREST_BOARD", "https://www.pinterest.com/ideas/"), wait_until="domcontentloaded", timeout=45000)
    page.wait_for_timeout(6000)
    ids = list(dict.fromkeys(re.findall(r'(?<!\d)(\d{15,19})(?!\d)', page.content())))[:8]
    for i in ids:  # board/user ids redirect away from /pin/: keep the first id that is a real pin page
        u = f"https://www.pinterest.com/pin/{i}/"
        page.goto(u, wait_until="domcontentloaded", timeout=45000); page.wait_for_timeout(3000)
        if "/pin/" in page.url and page.query_selector('meta[property="og:image"]'): return u
    raise RuntimeError("no real pin page found; set E2E_PINTEREST_BOARD to a public board URL")


# --------------------------------------------------------------- local hotlink simulation
class _Hot(http.server.BaseHTTPRequestHandler):
    img = b""
    def log_message(self, *a): pass
    def do_GET(self):
        if self.path == "/page":
            body = b"<html><title>Hotlink sim</title><body><img id=i src='http://127.0.0.1:%d/p.png' width=400></body></html>" % self.server.server_port_img
            self.send_response(200); self.send_header("Content-Type", "text/html"); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)
        elif self.path == "/p.png":
            # like a hotlink-protected CDN: only an <img> load is served, any fetch()/XHR gets 403
            if self.headers.get("Sec-Fetch-Dest") != "image":
                self.send_response(403); self.send_header("Content-Length", "0"); self.end_headers(); return
            self.send_response(200); self.send_header("Content-Type", "image/png"); self.send_header("Content-Length", str(len(self.img))); self.end_headers(); self.wfile.write(self.img)
        else:
            self.send_response(404); self.end_headers()


def start_hotlink_sim() -> str:
    buf = io.BytesIO(); Image.new("RGB", (800, 600), (30, 120, 200)).save(buf, "PNG")
    # page and image on two ports (cross-origin), both loopback
    imgsrv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), type("H", (_Hot,), {"img": buf.getvalue()}))
    pagesrv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), type("P", (_Hot,), {"img": buf.getvalue()}))
    pagesrv.server_port_img = imgsrv.server_port
    for s in (imgsrv, pagesrv): threading.Thread(target=s.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{pagesrv.server_port}/page"


# --------------------------------------------------------------- one site
def run_site(ctx, sw, name: str, resolve, row: dict, expect_remote=False):
    rx = Rx(name.replace("/", "-"))
    d = OUT / name; d.mkdir(parents=True, exist_ok=True)
    page = ctx.new_page()
    try:
        sw.evaluate("async ([t,p]) => chrome.storage.local.set({token: t, port: p})", [TOKEN, rx.port])
        url = resolve(page)
        row["page"] = url
        goto(page, url)
        page.wait_for_timeout(5000)
        title = page.title()
        if UA_BLOCK.search(title) or UA_BLOCK.search(page.inner_text("body")[:600] if page.query_selector("body") else ""):
            row["result"] = "BLOCKED (bot wall, not evaded)"; page.screenshot(path=str(d / "page.png")); return
        img = page.evaluate(PICK_IMG)
        page.screenshot(path=str(d / "page.png"))
        if not img: row["result"] = "FAIL: no usable <img> found on page"; return
        row["page_dims"] = f"{img['w']}x{img['h']}"
        info = {"srcUrl": img["src"], "pageUrl": page.url}
        page.bring_to_front()
        r1 = sw.evaluate(CLICK, info)
        row["toast"] = r1.get("message", "")
        if not r1.get("ok"): row["result"] = "FAIL: " + row["toast"]; return
        notes, atts = rx.notes(), rx.atts()
        errs = []
        if len(notes) != 1: errs.append(f"{len(notes)} notes (want 1)")
        if not notes: row["result"] = "FAIL: " + "; ".join(errs); return
        text = notes[0].read_text(); fm = frontmatter(text)
        (d / "note.md").write_text(text)
        mode = r1["result"].get("mode"); row["mode"] = mode
        row["downloaded"] = "Y" if mode == "local" else "N"; row["displays_remote"] = "Y" if mode == "remote" else "-"
        row["author"] = fm.get("author") or "-"
        if fm.get("page_url") != page.url: errs.append(f"page_url {fm.get('page_url')!r} != {page.url!r}")
        if fm.get("source_url") != img["src"] and "pinimg" not in img["src"]: errs.append(f"source_url {fm.get('source_url')!r}")
        if mode == "local":
            if len(atts) != 1: errs.append(f"{len(atts)} attachments (want 1)")
            else:
                shutil.copy(atts[0], d / ("image" + atts[0].suffix))
                with Image.open(atts[0]) as im: w, h = im.size; im.load()
                row["saved_dims"] = f"{w}x{h}"
                if w * h < img["w"] * img["h"]: errs.append(f"saved {w}x{h} smaller than page {img['w']}x{img['h']}")
                if hashlib.sha256(atts[0].read_bytes()).hexdigest() != fm.get("sha256"): errs.append("sha256 mismatch")
            if fm.get("image_local") is not True and fm.get("image_local") != "true": errs.append("image_local not true")
        else:
            row["saved_dims"] = "- (remote)"
            if atts: errs.append("attachment written in display-only mode")
            if str(fm.get("image_local")).lower() != "false": errs.append("image_local not false")
            if "![](<" not in text: errs.append("no remote embed")
            if not fm.get("image_url", "").startswith("http"): errs.append("image_url missing")
            if "display only" not in row["toast"]: errs.append("toast does not say display-only")
        if name.startswith("pinterest"):
            if "/pin/" not in page.url: errs.append("not on a /pin/ page: " + page.url)
            if not fm.get("pinterest_pin_id"): errs.append("no pinterest_pin_id (receiver did not use pinlib)")
            # Right-click on a 236px thumbnail of this same pin must still yield the HD original (same sha -> duplicate).
            thumb = re.sub(r"pinimg\.com/\d+x/", "pinimg.com/236x/", img["src"])
            r3 = sw.evaluate(CLICK, {"srcUrl": thumb, "pageUrl": page.url})
            if (r3.get("result") or {}).get("status") != "duplicate": errs.append("thumbnail-src capture did not resolve to the same HD image: " + json.dumps(r3)[:150])
            if mode == "local" and atts:
                with Image.open(atts[0]) as im0:
                    if im0.size[0] <= 236: errs.append(f"saved image not larger than the 236px thumbnail: {im0.size}")
        if expect_remote and mode != "remote": errs.append("expected display-only fallback, got " + str(mode))
        # same image again must not duplicate
        r2 = sw.evaluate(CLICK, info)
        if (r2.get("result") or {}).get("status") != "duplicate": errs.append("second capture not 'duplicate': " + json.dumps(r2)[:150])
        if len(rx.notes()) != 1 or len(rx.atts()) != len(atts): errs.append("second capture changed the vault")
        row["result"] = "PASS" + (" (fallback)" if mode == "remote" else "") if not errs else "FAIL: " + "; ".join(errs)
    except Blocked as e:
        row["result"] = f"BLOCKED (bot wall, not evaded): {e}"
    except Exception as e:
        row["result"] = f"FAIL: {type(e).__name__}: {str(e)[:200]}"
    finally:
        page.close(); rx.close()


def chromium_exe() -> str | None:
    """Python playwright here is older than the cached browser build: use the newest cached Chrome for Testing."""
    import glob
    c = sorted(glob.glob(str(Path.home() / "Library/Caches/ms-playwright/chromium-*/chrome-mac*/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing")))
    return os.environ.get("E2E_CHROMIUM") or (c[-1] if c else None)


def make_resolvers(over: dict, hot_url: str | None) -> dict:
    return {
        "pinterest": lambda p: over.get("pinterest") or discover_pin(p),
        # public pin whose /originals/ (2000x2000) is far larger than the 736px og:image / on-page image
        "pinterest-hd": lambda p: over.get("pinterest-hd") or "https://www.pinterest.com/pin/424605071146356953/",
        "behance": lambda p: over.get("behance") or discover(p, "https://www.behance.net/galleries", 'a[href*="/gallery/"]', r"/gallery/\d+"),
        "arena": lambda p: over.get("arena") or discover(p, "https://www.are.na/explore", 'a[href*="/block/"]', r"/block/\d+"),
        "commons": lambda p: over.get("commons") or "https://commons.wikimedia.org/wiki/File:Mona_Lisa,_by_Leonardo_da_Vinci,_from_C2RMF_retouched.jpg",
        "unsplash": lambda p: over.get("unsplash") or discover(p, "https://unsplash.com/s/photos/architecture", 'a[href^="/photos/"]', r"/photos/[\w-]{6,}"),
        "hotlink-sim": lambda p: hot_url,
    }


ALL_SITES = "pinterest,pinterest-hd,behance,arena,commons,unsplash,hotlink-sim"


def run_rightclick(sites: list[str], headed: bool, over: dict) -> list[dict]:
    OUT.mkdir(parents=True, exist_ok=True)
    hot_url = start_hotlink_sim() if "hotlink-sim" in sites else None
    resolvers = make_resolvers(over, hot_url)
    profile = tempfile.mkdtemp(prefix="wiki-inspi-e2e-profile-")  # fresh, never a real profile
    rows = []
    with sync_playwright() as pw:
        args = [f"--disable-extensions-except={ROOT/'extension'}", f"--load-extension={ROOT/'extension'}"]
        if not headed: args.append("--headless=new")
        ctx = pw.chromium.launch_persistent_context(profile, headless=False, executable_path=chromium_exe(), args=args, viewport={"width": 1366, "height": 900})
        sw = ctx.service_workers[0] if ctx.service_workers else ctx.wait_for_event("serviceworker", timeout=15000)
        ext_id = sw.url.split("/")[2]
        global EXT_ID
        EXT_ID = ext_id
        for s in sites:
            row = {"gesture": "right-click", "site": s, "mode": "-", "page_dims": "-", "saved_dims": "-", "author": "-", "toast": "", "page": "", "result": "?"}
            run_site(ctx, sw, s, resolvers[s], row, expect_remote=(s == "hotlink-sim"))
            rows.append(row); print(f"right-click {s}: {row['result']}", flush=True)
            time.sleep(2)
        ctx.close()
    shutil.rmtree(profile, ignore_errors=True)
    return rows


def write_summary(rows: list[dict], name: str = "summary.md") -> None:
    lines = ["| gesture | site | image found / mode | saved vs page dims | author | displays remote | downloaded | result |", "|---|---|---|---|---|---|---|---|"]
    for r in rows:
        lines.append(f"| {r['gesture']} | {r['site']} | {r.get('image_found', r.get('mode', '-'))} | {r['saved_dims']} vs {r['page_dims']} | {r['author']} | {r.get('displays_remote', '-')} | {r.get('downloaded', '-')} | {r['result']} |")
    lines += ["", *[f"- {r['gesture']} / {r['site']}: {r['page']}" for r in rows]]
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / name).write_text("\n".join(lines) + "\n")
    print("\n".join(lines))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--site", default=ALL_SITES)
    ap.add_argument("--headed", action="store_true")
    ap.add_argument("--url", action="append", default=[], help="override: site=URL (pin/page URL)")
    a = ap.parse_args()
    rows = run_rightclick([s.strip() for s in a.site.split(",") if s.strip()], a.headed, dict(u.split("=", 1) for u in a.url))
    write_summary(rows, "summary-rightclick.md")
    return 0 if all(r["result"].startswith(("PASS", "BLOCKED")) for r in rows) else 1


if __name__ == "__main__":
    sys.exit(main())

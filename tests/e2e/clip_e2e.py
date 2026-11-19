#!/usr/bin/env python3
"""
Obsidian Web Clipper e2e on live sites.

Pinned release 1.8.1 of obsidianmd/obsidian-clipper is cloned + built (Chrome target) into tests/e2e/.cache/,
loaded unpacked in a FRESH temp profile (never a live one). The Wiki Inspi template is imported through the
extension's own Settings > Import modal. For each site the clipper's embedded panel (the same popup UI, shown by
the extension's own "toggle-iframe") is opened on the real page and "Add to Obsidian" is clicked. The extension's
message to its background (openObsidianUrl) is intercepted in the panel, so nothing is opened: we read the
obsidian:// URI (legacy mode puts the note content in it) = exactly what Clipper would write.

Then, in a temp vault (path with a space): the note is dropped in Inbox and ONLY enrich.py's hydration path runs
(prepare_input -> fetch_og_image -> write_enriched_note) with the LLM stubbed by enrich's own model-free preflight.
enrich.py is imported, never edited.
"""
from __future__ import annotations

import io, json, os, re, shutil, subprocess, sys, tempfile, time, urllib.parse, importlib.util
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import run_e2e as R  # noqa: E402
from run_e2e import Blocked, goto, PICK_IMG, OUT, chromium_exe  # noqa: E402
from PIL import Image  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402

TAG = "1.8.1"
CACHE = HERE / ".cache" / "obsidian-clipper"
TEMPLATES = sorted((HERE.parents[1] / "clipper").glob("wiki-inspi*.json"))  # generic + per-site
ENRICH = Path(os.environ.get("E2E_ENRICH") or Path(__file__).resolve().parents[2] / "src" / "enrich.py")  # E2E_ENRICH: test a patched copy

INTERCEPT = """() => {
  const orig = chrome.runtime.sendMessage.bind(chrome.runtime);
  window.__obs = [];
  chrome.runtime.sendMessage = function (...a) {
    const m = a.find(x => x && typeof x === 'object' && x.action);
    if (m && m.action === 'openObsidianUrl') { window.__obs.push(m.url); return Promise.resolve({ success: true }); }
    return orig(...a);
  };
}"""


def ensure_clipper() -> Path:
    dist = CACHE / "dist"
    if (dist / "manifest.json").exists(): return dist
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    if not CACHE.exists():
        subprocess.run(["git", "clone", "-q", "--depth", "1", "--branch", TAG, "https://github.com/obsidianmd/obsidian-clipper", str(CACHE)], check=True)
    subprocess.run(["npm", "ci", "--silent"], cwd=CACHE, check=True)
    subprocess.run(["npx", "webpack", "--env", "BROWSER=chrome", "--mode", "production"], cwd=CACHE, check=True, capture_output=True)
    return dist


def load_enrich():
    spec = importlib.util.spec_from_file_location("enrich_under_test", ENRICH)
    m = importlib.util.module_from_spec(spec); sys.modules[spec.name] = m; spec.loader.exec_module(m)
    return m


def parse_uri(uri: str) -> dict:
    q = urllib.parse.urlparse(uri); qs = urllib.parse.parse_qs(q.query, keep_blank_values=True)
    return {"scheme": q.scheme, "file": qs.get("file", [""])[0], "vault": qs.get("vault", [""])[0], "content": qs.get("content", [""])[0]}


def fm_of(text: str) -> dict:
    m = re.match(r"^---\n(.*?)\n---\n", text, re.S)
    fm, key = {}, None
    for line in (m.group(1) if m else "").splitlines():
        li = re.match(r"^\s+-\s*(.*)$", line)
        if li and key:  # multitext value (Clipper writes author/tags as YAML lists)
            fm[key] = (fm[key] + ", " if fm[key] else "") + li.group(1).strip("\"'")
            continue
        k, _, v = line.partition(":")
        v = v.strip()
        if v[:1] in "\"'": v = v.strip("\"'")
        fm[k.strip()] = v; key = k.strip()
    return fm


def hydrate(enrich, name: str, note_text: str, d: Path) -> dict:
    """Run ONLY enrich.py's image hydration on a Clipper note in a temp vault; LLM stubbed (model-free preflight)."""
    base = Path(os.path.realpath(tempfile.mkdtemp(prefix="wiki-inspi-hyd-")))
    vault = base / f"Wiki Inspi Hyd {name}"
    for sub in ("Inbox", "Library", "_attachments"): (vault / sub).mkdir(parents=True)
    note = vault / "Inbox" / "clip-note.md"
    note.write_text(note_text)
    enrich._configure(vault)
    enrich.call_llm = lambda payload, mocs: enrich._preflight_enrichment(note)  # no LLM call
    out = {"downloaded": "N", "embed_local": "N", "saved_dims": "-", "hd": "-", "error": ""}
    try:
        dest, _ = enrich.process_item(note, [], False)
        text = dest.read_text(); (d / "enriched-note.md").write_text(text)
        atts = sorted((vault / "_attachments").glob("*"))
        out["downloaded"] = "Y" if atts else "N"
        if atts:
            with Image.open(atts[0]) as im: out["saved_dims"] = f"{im.size[0]}x{im.size[1]}"
            shutil.copy(atts[0], d / ("hydrated" + atts[0].suffix))
            out["embed_local"] = "Y" if re.search(r"!\[\[_attachments/" + re.escape(atts[0].name), text) or f"_attachments/{atts[0].name}" in text else "N"
    except Exception as e:
        out["error"] = f"{type(e).__name__}: {e}"[:200]
    finally:
        shutil.rmtree(base, ignore_errors=True)
    return out


def run_clipper(sites: list[str], headed: bool, over: dict) -> list[dict]:
    dist = ensure_clipper()
    enrich = load_enrich()
    hot_url = R.start_hotlink_sim() if "hotlink-sim" in sites else None
    resolvers = R.make_resolvers(over, hot_url)
    profile = tempfile.mkdtemp(prefix="wiki-inspi-clip-profile-")  # fresh, never a real profile
    rows = []
    with sync_playwright() as pw:
        args = [f"--disable-extensions-except={dist}", f"--load-extension={dist}"] + ([] if headed else ["--headless=new"])
        ctx = pw.chromium.launch_persistent_context(profile, headless=False, executable_path=chromium_exe(), args=args, viewport={"width": 1366, "height": 900})
        sw = ctx.service_workers[0] if ctx.service_workers else ctx.wait_for_event("serviceworker", timeout=15000)
        ext = sw.url.split("/")[2]
        st = ctx.new_page(); st.goto(f"chrome-extension://{ext}/settings.html"); st.wait_for_timeout(1500)
        st.evaluate("() => document.getElementById('legacy-mode-toggle').click()")  # content in the URI instead of the clipboard
        assert st.evaluate("() => document.getElementById('legacy-mode-toggle').checked"), "legacy mode not set"
        for tf in TEMPLATES:  # each file through the extension's own import modal
            st.evaluate("() => document.querySelector('.import-template-btn').click()")
            st.wait_for_selector("#import-modal .import-json-textarea", state="attached"); st.wait_for_timeout(400)
            st.fill("#import-modal .import-json-textarea", tf.read_text())
            st.evaluate("() => document.querySelector('#import-modal .import-confirm-btn').click()")
            st.wait_for_timeout(1200)
        listed = st.evaluate("() => [...document.querySelectorAll('#template-list li, #template-list .template-list-item, #template-list > *')].map(e => e.textContent.trim())")
        for tf in TEMPLATES:
            nm = __import__("json").loads(tf.read_text())["name"]
            assert any(nm in t for t in listed), f"template {nm!r} not imported: {listed}"
        for s in sites:
            row = {"gesture": "web-clipper", "site": s, "image_found": "-", "page_dims": "-", "saved_dims": "-", "author": "-", "displays_remote": "-", "downloaded": "-", "page": "", "result": "?"}
            d = OUT / "clipper" / s; d.mkdir(parents=True, exist_ok=True)
            page = ctx.new_page()
            try:
                url = resolvers[s](page); row["page"] = url
                goto(page, url); page.wait_for_timeout(5000)
                if R.UA_BLOCK.search(page.title()): raise Blocked("bot-wall title: " + page.title()[:60])
                img = page.evaluate(PICK_IMG); page.screenshot(path=str(d / "page.png"))
                if not img: row["result"] = "FAIL: no usable <img> on page"; continue
                row["page_dims"] = f"{img['w']}x{img['h']}"
                page.bring_to_front()
                st.evaluate("async () => await chrome.runtime.sendMessage({action: 'getActiveTabAndToggleIframe'})")
                fr = None
                for _ in range(40):
                    fr = next((f for f in page.frames if "context=iframe" in f.url), None)
                    if fr and fr.query_selector("#clip-btn"): break
                    page.wait_for_timeout(250)
                if not fr: row["result"] = "FAIL: clipper panel did not open"; continue
                page.wait_for_timeout(2500)  # template auto-select + field extraction
                tmpl = fr.evaluate("() => (document.querySelector('.template-name, #template-select') || {}).value || ''")
                fr.evaluate(INTERCEPT)
                fr.evaluate("() => document.getElementById('clip-btn').click()")
                for _ in range(30):
                    uris = fr.evaluate("() => window.__obs || []")
                    if uris: break
                    page.wait_for_timeout(300)
                if not uris: row["result"] = "FAIL: no obsidian:// URI produced"; page.screenshot(path=str(d / "panel-fail.png")); continue
                u = parse_uri(uris[-1]); text = u["content"]; (d / "clipper-note.md").write_text(text)
                page.screenshot(path=str(d / "panel.png"))
                fm = fm_of(text); errs = []
                if u["scheme"] != "obsidian" or not u["file"].startswith("Inbox/"): errs.append(f"unexpected target {u['file'][:60]!r}")
                if u["vault"] != "Wiki Inspi": errs.append(f"vault {u['vault']!r}")
                if fm.get("ingest_source") != "webclipper": errs.append("ingest_source")
                if fm.get("source_url", "").split("#")[0] != page.url.split("#")[0]: errs.append("source_url != page url")
                iu = fm.get("image_url", ""); row["image_found"] = ("Y " + iu[:60]) if iu else "N"
                row["author"] = fm.get("author") or "-"
                if s.startswith(("pinterest", "arena", "commons")) and not fm.get("author"): errs.append("author empty (site template selector)")
                if not iu.startswith("http"): errs.append("image_url missing")
                if not re.search(r"^!\[\]\(" + re.escape(iu), text.split("\n---\n", 1)[-1].lstrip("\n"), re.M) if iu else True: errs.append("body does not start with the image embed")
                # does the remote embed display (what Obsidian would show before hydration)? load it bare, no Referer.
                disp = ctx.new_page()
                try:
                    disp.set_content(f'<img id=i referrerpolicy="no-referrer" src="{iu}">'); disp.wait_for_timeout(3500)
                    nw = disp.evaluate("() => document.getElementById('i').naturalWidth"); nh = disp.evaluate("() => document.getElementById('i').naturalHeight")
                finally: disp.close()
                row["displays_remote"] = "Y" if nw else "N"; row["remote_dims"] = f"{nw}x{nh}"
                if not nw: errs.append("image_url does not display when loaded bare")
                pin_note = ""
                if "pinimg.com" in iu:
                    m = re.search(r"pinimg\.com/(\d+x|originals)/", iu); pin_note = f" pinimg:{m.group(1) if m else '?'}"
                row["image_found"] += pin_note
                if nw and nw * nh < img["w"] * img["h"] * 0.9: errs.append(f"image_url {nw}x{nh} smaller than the page's main image {img['w']}x{img['h']}")
                # hydration via enrich.py (LLM stubbed)
                h = hydrate(enrich, s, text, d)
                row["downloaded"] = h["downloaded"]; row["saved_dims"] = h["saved_dims"]
                if h["error"]: errs.append("hydration: " + h["error"])
                elif h["downloaded"] != "Y": errs.append("enrich did not download the image")
                elif h["embed_local"] != "Y": errs.append("enriched note does not embed the local file")
                if h["saved_dims"] != "-":
                    w, hh = map(int, h["saved_dims"].split("x"))
                    if w * hh < img["w"] * img["h"] * 0.9: errs.append(f"hydrated {h['saved_dims']} smaller than page image {img['w']}x{img['h']}")
                    if "pinimg.com" in iu:
                        sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src")); import pinlib
                        with Image.open(io.BytesIO(pinlib.fetch_original_bytes(iu))) as oi: ow, oh = oi.size
                        row["originals_dims"] = f"{ow}x{oh}"
                        if w * hh < ow * oh: errs.append(f"pinterest: hydrated {w}x{hh} but pinimg originals is {ow}x{oh} (enrich keeps the og:image variant)")
                row["result"] = "PASS" if not errs else "FAIL: " + "; ".join(errs)
            except Blocked as e:
                row["result"] = f"BLOCKED (bot wall, not evaded): {e}"
            except Exception as e:
                row["result"] = f"FAIL: {type(e).__name__}: {str(e)[:200]}"
            finally:
                page.close(); rows.append(row); print(f"web-clipper {s}: {row['result']}", flush=True); time.sleep(2)
        ctx.close()
    shutil.rmtree(profile, ignore_errors=True)
    return rows


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--site", default="pinterest,pinterest-hd,behance,arena,commons,unsplash"); ap.add_argument("--headed", action="store_true")
    ap.add_argument("--url", action="append", default=[])
    a = ap.parse_args()
    rows = run_clipper([s.strip() for s in a.site.split(",") if s.strip()], a.headed, dict(u.split("=", 1) for u in a.url))
    R.write_summary(rows, "summary-webclipper.md")
    return 0 if all(r["result"].startswith(("PASS", "BLOCKED")) for r in rows) else 1


if __name__ == "__main__":
    sys.exit(main())

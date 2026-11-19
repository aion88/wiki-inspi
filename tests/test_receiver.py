import hashlib, http.client, io, json, os, socket, sys, tempfile, threading, unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
import receiver  # noqa: E402

try:
    from PIL import Image
except Exception:
    Image = None

EXT = "abcdefghijklmnopabcdefghijklmnop"
TOKEN = "t0ken-" + "x" * 30


def png(color=(200, 10, 10), size=(8, 8)):
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, "PNG")
    return buf.getvalue()


# minimal valid PNG when PIL is absent
RAW_PNG = bytes.fromhex("89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4890000000d49444154789c6360f8cfc0f01f0005000201a5f645400000000049454e44ae426082")


def make_png(color=(200, 10, 10)):
    return png(color) if Image else RAW_PNG + bytes(color)  # trailing bytes: distinct sha


def multipart(fields, image=None, boundary="XBOUNDARYX"):
    out = b""
    for k, v in fields.items():
        out += f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n'.encode() + v.encode() + b"\r\n"
    if image is not None:
        out += (f'--{boundary}\r\nContent-Disposition: form-data; name="image"; filename="x"\r\n'
                f'Content-Type: application/octet-stream\r\n\r\n').encode() + image + b"\r\n"
    out += f"--{boundary}--\r\n".encode()
    return out, f"multipart/form-data; boundary={boundary}"


def free_port():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(os.path.realpath(self.tmp.name))
        self.vault = base / "Wiki Inspi Test"  # contains a space
        (self.vault / "Inbox").mkdir(parents=True)
        self.ledger = base / "receiver.log"
        self.pair = base / "pair-open"
        self.port = free_port()
        cfg = dict(receiver.DEFAULTS, port=self.port, vault=str(self.vault), extension_id=EXT,
                   log_file=str(self.ledger), log_dir=str(base), pair_file=str(self.pair), enrich_cmd=None)
        self.srv = receiver.make_server(cfg, token_provider=lambda: TOKEN)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def tearDown(self):
        self.srv.shutdown(); self.srv.server_close(); self.tmp.cleanup()

    def req(self, method="POST", path="/capture", body=b"", headers=None, host=None):
        h = {"Host": host or f"127.0.0.1:{self.port}", "Origin": f"chrome-extension://{EXT}",
             "Authorization": f"Bearer {TOKEN}"}
        h.update(headers or {})
        h = {k: v for k, v in h.items() if v is not None}
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        c.putrequest(method, path, skip_host=True, skip_accept_encoding=True)
        for k, v in h.items():
            c.putheader(k, v)
        c.putheader("Content-Length", str(len(body)))
        c.endheaders(body)
        r = c.getresponse(); data = r.read(); hdrs = dict(r.getheaders()); c.close()
        return r.status, (json.loads(data) if data else {}), hdrs

    def capture(self, fields=None, image=None, **kw):
        f = {"page_url": "https://example.com/p", "page_title": "A title", "alt": "alt", "src_url": "https://example.com/i.png"}
        f.update(fields or {})
        body, ct = multipart(f, make_png() if image is None else image)
        hdrs = {"Content-Type": ct}; hdrs.update(kw.pop("headers", {}) or {})
        return self.req(body=body, headers=hdrs, **kw)

    def ledger_lines(self):
        return [json.loads(l) for l in self.ledger.read_text().splitlines()] if self.ledger.exists() else []

    def vault_files(self):
        return sorted(str(p.relative_to(self.vault)) for p in self.vault.rglob("*") if p.is_file() and ".wiki-inspi" not in p.parts)


class TestGate(Base):
    def test_ok(self):
        st, js, hdrs = self.capture()
        self.assertEqual(st, 200, js); self.assertEqual(js["status"], "created")
        self.assertFalse([k for k in hdrs if k.lower().startswith("access-control")])

    def test_health_post(self):
        # Brave sends no Origin on an extension GET, so the options page Test button uses POST.
        self.assertEqual(self.req(path="/health")[0], 200)
        self.assertEqual(self.req(path="/health", headers={"Origin": None})[0], 403)
        self.assertEqual(self.req(path="/health", headers={"Authorization": "Bearer nope"})[0], 401)

    def test_bad_host(self):
        for host in ("localhost:%d" % self.port, "evil.com", f"127.0.0.1:{self.port + 1}"):
            st, js, _ = self.capture(host=host)
            self.assertEqual(st, 403, host)
        self.assertEqual(self.vault_files(), [])

    def test_bad_origin(self):
        for origin in ("https://evil.com", "chrome-extension://" + "z" * 32, None):
            st, _, _ = self.capture(headers={"Origin": origin})
            self.assertEqual(st, 403, origin)
        self.assertEqual(self.vault_files(), [])

    def test_bad_token_and_missing(self):
        self.assertEqual(self.capture(headers={"Authorization": "Bearer nope"})[0], 401)
        self.assertEqual(self.capture(headers={"Authorization": None})[0], 401)
        self.assertEqual(self.vault_files(), [])

    def test_valid_token_wrong_origin_still_refused(self):
        self.assertEqual(self.capture(headers={"Origin": "https://evil.com"})[0], 403)

    def test_refusals_ledgered_without_secrets(self):
        self.capture(headers={"Authorization": "Bearer nope-secret-attempt"})
        self.capture(host="evil.com")
        lines = self.ledger_lines()
        self.assertEqual([l["decision"] for l in lines], ["block", "block"])
        self.assertTrue(all(l["source"] == "wiki-inspi-receiver" and l["decision"] == "block" for l in lines))
        self.assertNotIn("nope-secret-attempt", self.ledger.read_text()); self.assertNotIn(TOKEN, self.ledger.read_text())

    def test_preflight_has_no_cors(self):
        st, _, hdrs = self.req(method="OPTIONS", body=b"")
        self.assertEqual(st, 403); self.assertFalse([k for k in hdrs if k.lower().startswith("access-control")])

    def test_oversize(self):
        st, js, _ = self.req(headers={"Content-Type": "multipart/form-data; boundary=x", "Content-Length": None}, body=b"")
        # real oversize: declare a huge length, send little (receiver must refuse before reading)
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        c.putrequest("POST", "/capture", skip_host=True)
        for k, v in {"Host": f"127.0.0.1:{self.port}", "Origin": f"chrome-extension://{EXT}",
                     "Authorization": f"Bearer {TOKEN}", "Content-Type": "multipart/form-data; boundary=x",
                     "Content-Length": str(receiver.MAX_BODY_BYTES + 1)}.items():
            c.putheader(k, v)
        c.endheaders()
        r = c.getresponse(); self.assertEqual(r.status, 413); c.close()
        self.assertEqual(self.vault_files(), [])

    def test_image_part_over_cap(self):
        big = b"\x89PNG\r\n\x1a\n" + b"0" * (receiver.MAX_IMAGE_BYTES + 10)
        with mock.patch.object(receiver, "MAX_BODY_BYTES", len(big) + 10000):
            st, js, _ = self.capture(image=big)
        self.assertEqual(st, 413); self.assertEqual(self.vault_files(), [])

    def test_non_image_bytes(self):
        for blob in (b"<html>hi</html>", b"<svg xmlns='http://www.w3.org/2000/svg'/>", b"MZ\x90\x00", b""):
            st, js, _ = self.capture(image=blob)
            self.assertIn(st, (400, 415), blob)
        self.assertEqual(self.vault_files(), [])

    def test_corrupt_png_header_only(self):
        if not Image:
            self.skipTest("PIL absent")
        st, _, _ = self.capture(image=b"\x89PNG\r\n\x1a\n" + b"garbage" * 10)
        self.assertEqual(st, 415)


class TestWrite(Base):
    def test_path_traversal_attempt(self):
        # client-controlled strings never reach a path
        st, js, _ = self.capture({"page_title": "../../../../etc/passwd", "alt": "..\\..\\x", "src_url": "file:///etc/passwd"})
        self.assertEqual(st, 200, js)
        for rel in self.vault_files():
            p = (self.vault / rel).resolve()
            self.assertTrue(str(p).startswith(str(self.vault) + os.sep), rel)
            self.assertNotIn("..", rel)
        self.assertTrue(any(f.startswith("_attachments/") for f in self.vault_files()))
        self.assertFalse((self.vault.parent / "etc").exists())

    def test_confined_rejects_escape(self):
        with self.assertRaises(receiver.Refusal):
            receiver.confined(str(self.vault), "..", "x")
        with self.assertRaises(receiver.Refusal):
            receiver.confined(str(self.vault), "/etc/passwd")

    def test_symlink_vault_refused(self):
        link = self.vault.parent / "link"
        link.symlink_to(self.vault)
        with self.assertRaises(SystemExit):
            receiver.make_server(dict(self.srv.RequestHandlerClass.cfg, vault=str(link), port=free_port()))

    def test_frontmatter_injection(self):
        evil = 'x"\n---\ninjected: true\n---\n# pwned'
        st, js, _ = self.capture({"page_title": evil, "alt": evil, "author": evil, "page_url": "https://e.com/?q=1\ninjected: 1"})
        self.assertEqual(st, 200, js)
        text = (self.vault / js["note"]).read_text()
        head, fm, body = text.split("---\n", 2)
        self.assertEqual(head, "")
        self.assertEqual(text.count("\n---\n"), 1)  # only the real frontmatter terminator
        import yaml
        data = yaml.safe_load(fm)
        self.assertNotIn("injected", data)
        self.assertEqual(set(data), {"type","captured","ingest_source","source_url","page_url","page_title","alt","author","site_name","sha256","thumbnail","image_local"})
        self.assertEqual(data["ingest_source"], "extension")
        self.assertEqual(data["sha256"], js["sha256"])
        self.assertNotIn("\n", data["page_title"])
        self.assertTrue(data["thumbnail"].startswith("_attachments/"))

    def test_obsidian_embed_defanged(self):
        st, js, _ = self.capture({"page_title": "![[secret]] [[x]]"})
        body = (self.vault / js["note"]).read_text().split("---\n", 2)[2]
        self.assertNotIn("![[secret]]", body)

    def test_dedupe_appends_source(self):
        img = make_png((1, 2, 3))
        a = self.capture(image=img)[1]
        n_files = self.vault_files()
        b = self.capture({"page_url": "https://other.example/x", "page_title": "other"}, image=img)[1]
        self.assertEqual(a["status"], "created"); self.assertEqual(b["status"], "duplicate")
        self.assertEqual(a["note"], b["note"]); self.assertEqual(self.vault_files(), n_files)
        self.assertIn("https://other.example/x", (self.vault / a["note"]).read_text())

    def test_dedupe_after_enricher_moved_note(self):
        img = make_png((9, 9, 9))
        a = self.capture(image=img)[1]
        (self.vault / "Library").mkdir()
        (self.vault / a["note"]).rename(self.vault / "Library" / Path(a["note"]).name)
        b = self.capture(image=img)[1]
        self.assertEqual(b["status"], "duplicate")
        self.assertEqual(b["note"], "Library/" + Path(a["note"]).name)

    def test_extension_from_magic_not_client(self):
        st, js, _ = self.capture({"src_url": "https://e.com/x.svg"})
        self.assertTrue(any(f.endswith(".png") for f in self.vault_files()))

    def test_inline_src_not_stored(self):
        st, js, _ = self.capture({"src_url": "data:image/png;base64," + "A" * 500})
        self.assertNotIn("base64", (self.vault / js["note"]).read_text())


class TestPinterest(Base):
    HTML = (b'<html><head><title>Cool | My Board | Pinterest</title>'
            b'<meta property="og:image" content="https://i.pinimg.com/736x/aa/bb/cc.jpg">'
            b'<meta property="og:title" content="Cool Pin">'
            b'<meta name="pinterestapp:pinboard" content="https://www.pinterest.com/u/my-board/">'
            b'<meta name="pinterestapp:pinid" content="123"></head></html>')

    def setUp(self):
        super().setUp()
        self.hd = make_png((50, 60, 70))
        self.calls = []

    def _fake_fetch(self, url):
        self.calls.append(url)
        if "/pin/" in url:
            return self.HTML, url
        if "/originals/" in url:
            return self.hd, url
        return b"lowres", url

    def _pinlib(self):
        import pinlib
        return pinlib

    def test_pin_uses_pinlib_hd(self):
        pinlib = self._pinlib()
        with mock.patch.object(pinlib, "fetch", self._fake_fetch):
            st, js, _ = self.capture({"pinterest": "1", "pin_url": "https://www.pinterest.com/pin/123/"}, image=make_png((1, 1, 1)))
        self.assertEqual(st, 200, js)
        self.assertEqual(js["sha256"], hashlib.sha256(self.hd).hexdigest())  # HD bytes, not the uploaded thumb
        self.assertTrue(any("/originals/" in c for c in self.calls))
        text = (self.vault / js["note"]).read_text()
        self.assertIn('pinterest_pin_id: "123"', text); self.assertIn("My Board", text)

    def test_pin_ssrf_refused(self):
        pinlib = self._pinlib()
        with mock.patch.object(pinlib, "fetch", self._fake_fetch):
            st, js, _ = self.capture({"pinterest": "1", "pin_url": "http://127.0.0.1:11434/pin/1"}, image=make_png((2, 2, 2)))
        self.assertEqual(st, 200)               # falls back to the extension's bytes
        self.assertEqual(self.calls, [])        # and never fetched the local URL

    def test_pin_image_host_restricted(self):
        pinlib = self._pinlib()
        evil = self.HTML.replace(b"i.pinimg.com", b"127.0.0.1:8188")
        seen = []
        def f(url):
            seen.append(url); return (evil, url) if "/pin/" in url else (b"x", url)
        with mock.patch.object(pinlib, "fetch", f):
            with self.assertRaises(ValueError):
                pinlib.fetch_original_bytes("https://127.0.0.1:8188/736x/a.jpg")
        self.assertEqual(seen, [])


class TestPair(Base):
    def do_pair(self):
        return self.req(path="/pair", headers={"Authorization": None}, body=b"")

    def test_closed_by_default(self):
        self.assertEqual(self.do_pair()[0], 403)

    def test_one_shot_within_window(self):
        self.pair_file = self.pair
        Path(self.srv.RequestHandlerClass.cfg["pair_file"]).write_text("")
        st, js, _ = self.do_pair()
        self.assertEqual((st, js["token"]), (200, TOKEN))
        self.assertEqual(self.do_pair()[0], 403)  # one-shot

    def test_expired(self):
        p = Path(self.srv.RequestHandlerClass.cfg["pair_file"]); p.write_text("")
        old = p.stat().st_mtime - 120; os.utime(p, (old, old))
        self.assertEqual(self.do_pair()[0], 403)

    def test_pair_wrong_origin(self):
        Path(self.srv.RequestHandlerClass.cfg["pair_file"]).write_text("")
        st, _, _ = self.req(path="/pair", headers={"Authorization": None, "Origin": "https://evil.com"})
        self.assertEqual(st, 403)
        self.assertTrue(Path(self.srv.RequestHandlerClass.cfg["pair_file"]).exists())  # window not burned




class TestDisplayOnly(Base):
    def remote(self, src, extra=None, **kw):
        f = {"display_only": "1", "src_url": src}
        f.update(extra or {})
        f.setdefault("page_url", "https://example.com/p"); f.setdefault("page_title", "Remote one")
        body, ct = multipart(f, None)
        return self.req(body=body, headers={"Content-Type": ct}, **kw)

    def test_note_without_attachment(self):
        st, js, _ = self.remote("https://img.example.com/a b.jpg?x=1&y=2")
        self.assertEqual(st, 200); self.assertEqual(js["status"], "created"); self.assertEqual(js["mode"], "remote")
        files = self.vault_files()
        self.assertEqual([f for f in files if f.startswith("_attachments")], [])
        text = (self.vault / js["note"]).read_text()
        fm = text.split("---\n")[1]
        self.assertIn("image_local: false", fm)
        self.assertIn('image_url: "https://img.example.com/a b.jpg?x=1&y=2"', fm)
        self.assertNotIn("thumbnail:", fm)
        self.assertIn("![](<https://img.example.com/a%20b.jpg?x=1&y=2>)", text)
        self.assertNotIn("![[", text)

    def test_local_note_says_image_local_true(self):
        js = self.capture()[1]
        self.assertIn("image_local: true", (self.vault / js["note"]).read_text())
        self.assertEqual(js["mode"], "local")

    def test_only_http_https(self):
        for bad in ("javascript:alert(1)", "data:image/png;base64,AAAA", "blob:https://x/1", "file:///etc/passwd", "ftp://x/y.png", ""):
            st, js, _ = self.remote(bad)
            self.assertEqual(st, 400, bad); self.assertEqual(js["error"], "no-image")
        self.assertEqual(self.vault_files(), [])

    def test_markdown_injection_in_url(self):
        evil = "https://e.com/x.png>)\n![[secret]] [[x]] ```"
        st, js, _ = self.remote(evil)
        self.assertEqual(st, 200)
        text = (self.vault / js["note"]).read_text()
        body = text.split("---\n", 2)[2]
        self.assertNotIn("![[", body); self.assertNotIn("[[", body); self.assertNotIn("```", body)
        self.assertEqual(body.count("\n![]("), 1)
        self.assertNotIn(">)\n![[", text)

    def test_never_fetches(self):
        with mock.patch("urllib.request.urlopen", side_effect=AssertionError("fetch")), \
             mock.patch.object(receiver, "pin_original", side_effect=AssertionError("pin fetch")):
            self.assertEqual(self.remote("https://img.example.com/a.jpg")[0], 200)

    def test_dedupe_by_url(self):
        a = self.remote("https://img.example.com/a.jpg")[1]
        n = self.vault_files()
        b = self.remote("https://img.example.com/a.jpg", {"page_url": "https://o.example/z"})[1]
        self.assertEqual(b["status"], "duplicate"); self.assertEqual(a["note"], b["note"])
        self.assertEqual(self.vault_files(), n)
        self.assertIn("https://o.example/z", (self.vault / a["note"]).read_text())

    def test_image_bytes_win_over_flag(self):
        st, js, _ = self.capture({"display_only": "1"})
        self.assertEqual(js["mode"], "local")

    def test_missing_flag_still_refused(self):
        body, ct = multipart({"src_url": "https://img.example.com/a.jpg"}, None)
        st, js, _ = self.req(body=body, headers={"Content-Type": ct})
        self.assertEqual(st, 400)

    def test_pinterest_hd_failure_falls_back_to_display(self):
        with mock.patch.object(receiver, "pin_original", side_effect=ValueError("x")):
            st, js, _ = self.remote("https://i.pinimg.com/236x/aa/bb/cc.jpg",
                                    {"pinterest": "1", "pin_url": "https://www.pinterest.com/pin/123/"})
        self.assertEqual(st, 200); self.assertEqual(js["mode"], "remote")


class TestExtensionId(Base):
    """No id ships with the extension: it is learned at pairing, or set in the config."""

    def test_manifest_has_no_pinned_key(self):
        manifest = json.loads((ROOT / "extension" / "manifest.json").read_text())
        self.assertNotIn("key", manifest)

    def test_unpaired_receiver_refuses_everything_but_pair(self):
        self.srv.RequestHandlerClass.cfg["extension_id"] = ""
        st, js, _ = self.req("POST", "/health")
        self.assertEqual((st, js["error"]), (403, "bad-origin"))

    def test_first_pairing_learns_id_then_only_that_id(self):
        cfgfile = Path(self.tmp.name) / "config.json"
        cfg = self.srv.RequestHandlerClass.cfg
        cfg["extension_id"] = ""; cfg["_config_path"] = str(cfgfile)
        self.pair.write_text("")
        learned = "p" * 32
        st, js, _ = self.req("POST", "/pair", headers={"Origin": f"chrome-extension://{learned}", "Authorization": None})
        self.assertEqual(st, 200); self.assertEqual(js["token"], TOKEN)
        self.assertEqual(json.loads(cfgfile.read_text())["extension_id"], learned)
        # the learned id works, any other origin is now refused
        st, _, _ = self.req("POST", "/health", headers={"Origin": f"chrome-extension://{learned}"})
        self.assertEqual(st, 200)
        st, _, _ = self.req("POST", "/health")  # Origin of EXT
        self.assertEqual(st, 403)

    def test_learning_needs_open_window_and_extension_origin(self):
        cfg = self.srv.RequestHandlerClass.cfg
        cfg["extension_id"] = ""
        st, js, _ = self.req("POST", "/pair", headers={"Origin": "chrome-extension://" + "a" * 32, "Authorization": None})
        self.assertEqual((st, js["error"]), (403, "pair-window-closed"))
        self.pair.write_text("")
        st, js, _ = self.req("POST", "/pair", headers={"Origin": "https://evil.example", "Authorization": None})
        self.assertEqual((st, js["error"]), (403, "bad-origin"))
        self.assertEqual(cfg["extension_id"], "")


if __name__ == "__main__":
    unittest.main()

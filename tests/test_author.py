"""Author heuristics of extension/background.js pageMetadata(), run in real Chromium on fixtures served at the real hostnames."""
import re, sys, unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tests" / "e2e"))
try:
    from playwright.sync_api import sync_playwright
    import run_e2e
    EXE = run_e2e.chromium_exe()
except Exception:  # playwright missing
    sync_playwright = None

SRC = (ROOT / "extension" / "background.js").read_text()
FN = SRC[SRC.index("function pageMetadata()"): SRC.index("function pinUrlOf")]

ARENA = """<html><head><title>IMG_2777.JPG | Are.na</title></head><body>
<header><a href="/explore">Explore</a><a href="/login?x=1">Log in</a><a href="/sign_up">Sign up</a><a href="/about">About</a></header>
<main><img src="x.jpg"><div><a href="/jane-doe">Jane Doe</a> added this <a href="/jane-doe/channel-x">chan</a></div></main></body></html>"""
ARENA_NO_OWNER = """<html><body><header><a href="/explore">Explore</a></header><main><a href="/login">Log in</a></main></body></html>"""
COMMONS = """<html><head><title>File:X.jpg</title></head><body><table id="fileinfotable"><tr>
<td id="fileinfotpl_aut" class="fileinfo-paramfield">Artist</td><td><div class="fn value"><style>.mw-parser-output .x{color:red}</style>
<table><tbody><tr><td><div class="commons-creator vcard"><table><tr><th><span class="fn" id="creator"><bdi><a href="https://en.wikipedia.org/wiki/L"><span>Leonardo da Vinci</span></a></bdi></span>&nbsp;(1452)</th></tr>
<tr><td>Alternative names Leonardo di ser Piero</td></tr></table></div></td></tr></tbody></table></div></td></tr>
<tr><td class="fileinfo-paramfield">Date</td><td>1503</td></tr></table></body></html>"""
COMMONS_PLAIN = """<html><body><table id="fileinfotable"><tr><td id="fileinfotpl_aut" class="fileinfo-paramfield">Author</td><td>Jane <a href="/u">Doe</a> via Flickr</td></tr></table></body></html>"""
PIN = """<html><head><script type="application/ld+json">{"@type":"SocialMediaPosting","author":{"name":"Pinterest"}}</script></head><body><div data-test-id="creator-profile-name">  Alex   Martin </div></body></html>"""
PIN_GENERIC = """<html><head><meta name="author" content="Pinterest"><script type="application/ld+json">{"@type":"SocialMediaPosting","author":{"name":"Pinterest"}}</script></head><body></body></html>"""
LD = """<html><head><script type="application/ld+json">{"@type":"CreativeWork","author":{"name":"LD Person"}}</script></head><body><main><a href="/someone-else">Someone Else</a></main></body></html>"""


@unittest.skipUnless(sync_playwright and EXE, "needs python playwright + cached Chrome for Testing")
class TestAuthor(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pw = sync_playwright().start()
        cls.br = cls.pw.chromium.launch(executable_path=EXE, headless=True)

    @classmethod
    def tearDownClass(cls):
        cls.br.close(); cls.pw.stop()

    def author(self, url, html):
        page = self.br.new_page()
        try:
            page.route("**/*", lambda r: r.fulfill(status=200, content_type="text/html", body=html))
            page.goto(url)
            return page.evaluate("(" + FN.replace("function pageMetadata()", "function()", 1) + ")")["author"]
        finally:
            page.close()

    def test_arena_owner(self):
        self.assertEqual(self.author("https://www.are.na/block/4889382", ARENA), "Jane Doe")

    def test_arena_no_owner_stays_empty(self):
        self.assertEqual(self.author("https://www.are.na/block/1", ARENA_NO_OWNER), "")

    def test_commons_artist_cell(self):
        self.assertEqual(self.author("https://commons.wikimedia.org/wiki/File:X.jpg", COMMONS), "Leonardo da Vinci")

    def test_commons_plain_author_text(self):
        self.assertEqual(self.author("https://commons.wikimedia.org/wiki/File:Y.jpg", COMMONS_PLAIN), "Doe")

    def test_pinterest_creator_beats_ldjson(self):
        self.assertEqual(self.author("https://www.pinterest.com/pin/1/", PIN), "Alex Martin")

    def test_pinterest_generic_value_never_used(self):
        self.assertEqual(self.author("https://www.pinterest.com/pin/2/", PIN_GENERIC), "")

    def test_jsonld_still_wins(self):
        self.assertEqual(self.author("https://www.are.na/block/2", LD), "LD Person")

    def test_other_sites_untouched(self):
        self.assertEqual(self.author("https://example.com/", ARENA), "")


if __name__ == "__main__":
    unittest.main()

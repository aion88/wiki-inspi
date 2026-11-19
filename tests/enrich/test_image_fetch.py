"""URL-only stub hydration: image_url first, og:image fallback, pinimg HD, safe downloads."""
import io
import sys
import types

import pytest
from PIL import Image


def png_bytes():
    b = io.BytesIO()
    Image.new("RGB", (8, 8), (1, 2, 3)).save(b, "PNG")
    return b.getvalue()


def jpg_bytes():
    b = io.BytesIO()
    Image.new("RGB", (8, 8), (1, 2, 3)).save(b, "JPEG")
    return b.getvalue()


def stub(vault, extra):
    p = vault / "Inbox" / "cap.md"
    p.write_text(f'---\nsource_url: "https://site.test/page"\ncaptured: 2026-11-18\n{extra}---\n\n# cap\n')
    return p


def test_image_url_preferred_over_og_image(enrich, vault, monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(enrich, "_http_get", lambda url, referer, cap=25_000_000: calls.append(url) or jpg_bytes())
    monkeypatch.setattr(enrich, "fetch_og_image", lambda *a, **k: pytest.fail("og:image must not be fetched"))
    p = stub(vault, 'image_url: "https://cdn.test/hero.jpg"\n')
    kind, payload, meta, prefetched = enrich.prepare_input(p, tmp_path, dry_run=False)
    assert calls == ["https://cdn.test/hero.jpg"]
    assert prefetched is not None and prefetched.parent == vault / "_attachments" and payload["type"] == "image"


def test_falls_back_to_og_when_image_url_fails(enrich, vault, monkeypatch, tmp_path):
    def boom(url, referer, cap=25_000_000):
        raise OSError("404")

    monkeypatch.setattr(enrich, "_http_get", boom)
    called = {}

    def og(url, dest_slug):
        called["og"] = url
        out = vault / "_attachments" / f"{dest_slug}.jpg"
        out.write_bytes(jpg_bytes())
        return out

    monkeypatch.setattr(enrich, "fetch_og_image", og)
    p = stub(vault, 'image_url: "https://cdn.test/hero.jpg"\n')
    enrich.prepare_input(p, tmp_path, dry_run=False)
    assert called["og"] == "https://site.test/page"


@pytest.mark.parametrize("bad", ["file:///etc/passwd", "ftp://x/y.jpg", "javascript:alert(1)"])
def test_non_http_image_url_rejected(enrich, vault, monkeypatch, bad):
    monkeypatch.setattr(enrich, "_http_get", lambda *a, **k: pytest.fail("must not download"))
    assert enrich.fetch_image_url(bad, "x", "https://site.test") is None


def test_non_image_bytes_rejected(enrich, vault, monkeypatch):
    monkeypatch.setattr(enrich, "_http_get", lambda url, referer, cap=25_000_000: b"<html>not an image</html>")
    assert enrich.fetch_image_url("https://cdn.test/a.jpg", "x", "") is None
    assert not list((vault / "_attachments").glob("x*"))


def test_size_cap_enforced_by_http_get(enrich, monkeypatch):
    class R:
        headers = types.SimpleNamespace(get_content_charset=lambda: None)

        def read(self, n):
            return b"\xff\xd8\xff" + b"0" * (n - 3 + 1)  # one byte over the cap

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(enrich.urllib.request, "urlopen", lambda *a, **k: R())
    with pytest.raises(ValueError):
        enrich._http_get("https://cdn.test/a.jpg", "", cap=1000)


def test_extension_follows_magic_bytes(enrich, vault, monkeypatch):
    monkeypatch.setattr(enrich, "_http_get", lambda url, referer, cap=25_000_000: png_bytes())
    out = enrich.fetch_image_url("https://cdn.test/a.jpg", "x", "")
    assert out.suffix == ".png" and out.read_bytes().startswith(b"\x89PNG")


def test_pinimg_uses_pinlib_original(enrich, vault, monkeypatch):
    seen = {}
    fake = types.ModuleType("pinlib")
    fake.fetch_original_bytes = lambda u: seen.setdefault("u", u) and png_bytes()
    monkeypatch.setitem(sys.modules, "pinlib", fake)
    monkeypatch.setattr(enrich, "_http_get", lambda *a, **k: pytest.fail("pinimg must go through pinlib"))
    out = enrich.fetch_image_url("https://i.pinimg.com/736x/aa/bb/cc.jpg", "pin", "https://www.pinterest.com/pin/1/")
    assert seen["u"].endswith("cc.jpg") and out.suffix == ".png"


def test_pinimg_falls_back_to_plain_download_when_pinlib_fails(enrich, vault, monkeypatch):
    fake = types.ModuleType("pinlib")

    def bad(u):
        raise ValueError("pin image download failed")

    fake.fetch_original_bytes = bad
    monkeypatch.setitem(sys.modules, "pinlib", fake)
    monkeypatch.setattr(enrich, "_http_get", lambda url, referer, cap=25_000_000: jpg_bytes())
    out = enrich.fetch_image_url("https://i.pinimg.com/736x/aa/bb/cc.jpg", "pin", "")
    assert out is not None and out.suffix == ".jpg"

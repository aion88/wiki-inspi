import os
from pathlib import Path

import pytest

PIL = pytest.importorskip("PIL")
from PIL import Image


def make_big_png(path: Path):
    # Random noise does not compress: ~3000x2500 RGB lands well over 10 MB as PNG.
    w, h = 3000, 2500
    im = Image.frombytes("RGB", (w, h), os.urandom(w * h * 3))
    im.save(path, "PNG", compress_level=1)
    assert path.stat().st_size > 10 * 1024 * 1024
    return path


def test_oversized_png_is_downscaled_under_limits(enrich, tmp_path):
    big = make_big_png(tmp_path / "big image.png")  # space in name on purpose
    work = tmp_path / "work dir"
    work.mkdir()
    out = enrich.downscale_image(big, work)
    assert out.stat().st_size <= 5 * 1024 * 1024
    with Image.open(out) as im:
        assert im.format == "JPEG"
        assert max(im.size) <= 1568
    assert big.stat().st_size > 10 * 1024 * 1024, "original must stay untouched"


def test_payload_never_exceeds_anthropic_10mb(enrich, tmp_path):
    big = make_big_png(tmp_path / "big.png")
    payload = enrich.image_payload(big, tmp_path, {})
    assert len(payload["source"]["data"]) < 10 * 1024 * 1024
    assert payload["source"]["media_type"] == "image/jpeg"
    assert Path(payload["path"]).is_file()


def test_small_image_is_not_upscaled(enrich, tmp_path):
    p = tmp_path / "s.png"
    Image.new("RGB", (200, 100), (10, 20, 30)).save(p)
    out = enrich.downscale_image(p, tmp_path)
    with Image.open(out) as im:
        assert im.size == (200, 100)


def test_alpha_png_flattened(enrich, tmp_path):
    p = tmp_path / "a.png"
    Image.new("RGBA", (50, 50), (255, 0, 0, 0)).save(p)
    out = enrich.downscale_image(p, tmp_path)
    with Image.open(out) as im:
        assert im.mode == "RGB"

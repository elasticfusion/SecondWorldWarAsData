"""Tier-1: images width/height/encodingFormat populated deterministically from the downloaded bytes."""

import io
import pathlib

from PIL import Image

import src.extraction.images as IM


def _png(w, h):
    buf = io.BytesIO()
    Image.new("RGB", (w, h), (10, 20, 30)).save(buf, format="PNG")
    return buf.getvalue()


def test_download_returns_dimensions_and_mime(tmp_path, monkeypatch):
    monkeypatch.setattr(IM, "fetch_image_bytes", lambda url: (_png(200, 150), "png"))
    lc, ext, w, h, mime = IM._download_image(
        "https://x/a.png", "01HXIMGAAAAAAAAAAAAAAAAAAA", tmp_path
    )
    assert ext == "png" and (w, h) == (200, 150) and mime == "image/png"
    assert lc and (tmp_path / "01HXIMGAAAAAAAAAAAAAAAAAAA.png").exists()


def test_dimensions_failsafe_on_bad_bytes(tmp_path, monkeypatch):
    monkeypatch.setattr(IM, "fetch_image_bytes", lambda url: (b"not-an-image", "jpg"))
    lc, ext, w, h, mime = IM._download_image(
        "https://x/b.jpg", "01HXIMGBBBBBBBBBBBBBBBBBBB", tmp_path
    )
    assert w is None and h is None  # dims unavailable -> null, no crash
    assert mime == "image/jpeg"  # mime still derived from extension


def test_ext_to_mime():
    assert IM._ext_to_mime("JPG") == "image/jpeg"
    assert IM._ext_to_mime("tiff") == "image/tiff"
    assert IM._ext_to_mime("webp") == "image/webp"
    assert IM._ext_to_mime(None) is None
    assert IM._ext_to_mime("xyz") is None


def test_image_dimensions_helper():
    assert IM._image_dimensions(_png(64, 48)) == (64, 48)
    assert IM._image_dimensions(b"junk") == (None, None)

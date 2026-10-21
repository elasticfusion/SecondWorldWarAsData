"""Decompression-bomb guard SCOPE regression — §1.0b.

The image decode cap (Image.MAX_IMAGE_PIXELS = 50M) was restored RIGHT AFTER verify(), so the
rasterizing convert()/resize() ran at Pillow's default cap — a bomb that only expands during
convert/resize slipped the guard. The cap is now held across the ENTIRE decode block, and a
DecompressionBombError there is caught -> image REJECTED (not an opaque 'Invalid image'). Also
_compute_image_hash now caps the average_hash decode.
"""

from unittest import mock

from PIL import Image


def _bomb_img():
    """A fake PIL image that verifies OK but raises DecompressionBombError when RASTERIZED
    (convert/resize) — a bomb that only expands during the convert/resize step."""
    m = mock.Mock()
    m.format = "BMP"  # not in the keep-list -> forces the convert() path
    m.verify = lambda: None
    m.width = 10000
    m.height = 10000
    m.convert.side_effect = Image.DecompressionBombError("bomb during convert")
    m.resize.side_effect = Image.DecompressionBombError("bomb during resize")
    return m


def test_equipment_verify_media_rejects_bomb_in_convert(monkeypatch):
    from src.extraction import equipment

    monkeypatch.setattr("PIL.Image.open", lambda *a, **k: _bomb_img())
    ok, msg = equipment._verify_media_with_vision(
        b"\x00" * 16, "Tiger", "tank", "a title", mock.Mock()
    )
    assert ok is False
    assert "decompression bomb" in msg.lower()


def test_maps_verify_rejects_bomb_in_convert(monkeypatch):
    from src.extraction import grok_search_maps

    monkeypatch.setattr("PIL.Image.open", lambda *a, **k: _bomb_img())
    ok, msg = grok_search_maps.verify_map_with_vision(
        b"\x00" * 16, "Caen", "1944-07", "context", "map title", mock.Mock()
    )
    assert ok is False
    assert "decompression bomb" in msg.lower()


def test_compute_image_hash_caps_and_survives_bomb(monkeypatch, tmp_path):
    from src.extraction import equipment

    p = tmp_path / "x.png"
    p.write_bytes(b"\x00" * 16)

    class _Ctx:
        def __enter__(self):
            raise Image.DecompressionBombError("bomb on hash decode")

        def __exit__(self, *a):
            return False

    monkeypatch.setattr("PIL.Image.open", lambda *a, **k: _Ctx())
    # must not crash; returns None (bomb rejected, OBSERV logged)
    assert equipment._compute_image_hash(p) is None


def test_max_image_pixels_restored_after_decode(monkeypatch):
    """The cap is restored (finally) even when the block raises — no global leak across calls."""
    from src.extraction import equipment

    before = Image.MAX_IMAGE_PIXELS
    monkeypatch.setattr("PIL.Image.open", lambda *a, **k: _bomb_img())
    equipment._verify_media_with_vision(b"\x00" * 16, "T", "tank", "title", mock.Mock())
    assert Image.MAX_IMAGE_PIXELS == before

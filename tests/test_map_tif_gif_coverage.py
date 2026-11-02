"""TIF/GIF map coverage (owner: other sources — NARA/archival — serve maps as TIFF/GIF, not jpg).
Guards that no map extension gate silently drops or mislabels tif/tiff/gif.
"""

import pytest

from src.extraction.images import _ext_for, _is_valid_image_body
from src.extraction.maps import _determine_map_extension


class _Resp:
    def __init__(self, ct):
        self.headers = {"content-type": ct}


def test_fetch_validates_both_tiff_byte_orders():
    assert _is_valid_image_body(b"II*\x00rest", "")  # little-endian TIFF
    assert _is_valid_image_body(b"MM\x00*rest", "")  # big-endian TIFF


def test_fetch_validates_both_gif_variants():
    assert _is_valid_image_body(b"GIF87arest", "")
    assert _is_valid_image_body(b"GIF89arest", "")


@pytest.mark.parametrize(
    "ct,url,expected",
    [
        ("image/tiff", "x", "tif"),
        ("", "http://a/m.tif", "tif"),
        ("", "http://a/m.tiff", "tif"),
        ("image/gif", "x", "gif"),
        ("", "http://a/m.gif", "gif"),
    ],
)
def test_ext_for_maps_tif_gif(ct, url, expected):
    assert _ext_for(ct, url) == expected


@pytest.mark.parametrize(
    "ct,url,expected",
    [
        (_Resp("image/tiff"), "http://a/m.tif", "tif"),
        (_Resp(""), "http://a/m.tiff", "tif"),
        (_Resp("image/gif"), "http://a/m.gif", "gif"),
        (_Resp(""), "http://a/m.gif", "gif"),
    ],
)
def test_determine_map_extension_tif_gif(ct, url, expected):
    assert _determine_map_extension(ct, url) == expected

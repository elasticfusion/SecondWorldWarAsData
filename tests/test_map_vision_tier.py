"""Maps 5c-ii: tiered classification + idempotent Grok-vision backstop. Tier 1 (map-word//maps/) and
Tier 2 (photo/document) are deterministic (NO vision); Tier 3 (ambiguous) falls back to the vision
classifier ONCE; a re-run never re-submits (don't-resubmit-to-Grok)."""

import pytest

import src.extraction.grok_search_maps as g
from src.extraction.map_common import classify_image_tier, maybe_vision_classify_map


def test_tiers():
    assert classify_image_tier("Map VIII", "http://x/maps/m8.jpg") == "map"
    assert (
        classify_image_tier("9th Division troops advance", "http://x/img/p.jpg")
        == "not_map"
    )
    assert classify_image_tier("Figure 3", "http://x/fig/f3.jpg") == "ambiguous"


def test_tier1_no_vision(monkeypatch):
    n = {"v": 0}
    monkeypatch.setattr(
        g,
        "verify_map_with_vision",
        lambda *a, **k: (n.__setitem__("v", n["v"] + 1) or (True, "")),
    )
    rec = {
        "MapID": "m",
        "common_name": "Map VIII",
        "source_url": "http://x/maps/m8.jpg",
    }
    maybe_vision_classify_map(rec, lambda: b"img", object())
    assert rec["is_map"] is True and n["v"] == 0


def test_tier2_no_vision(monkeypatch):
    n = {"v": 0}
    monkeypatch.setattr(
        g,
        "verify_map_with_vision",
        lambda *a, **k: (n.__setitem__("v", n["v"] + 1) or (True, "")),
    )
    rec = {
        "MapID": "m",
        "common_name": "troops photo",
        "source_url": "http://x/img/p.jpg",
    }
    maybe_vision_classify_map(rec, lambda: b"img", object())
    assert rec["is_map"] is False and n["v"] == 0


def test_tier3_vision_once_then_idempotent(monkeypatch):
    n = {"v": 0}
    monkeypatch.setattr(
        g,
        "verify_map_with_vision",
        lambda *a, **k: (n.__setitem__("v", n["v"] + 1) or (True, "is a map")),
    )
    rec = {"MapID": "m", "common_name": "Figure 3", "source_url": "http://x/fig/f3.jpg"}
    maybe_vision_classify_map(rec, lambda: b"img", object())
    assert rec["is_map"] is True and rec.get("map_vision_checked_at") and n["v"] == 1
    maybe_vision_classify_map(rec, lambda: b"img", object())  # re-run
    assert n["v"] == 1  # idempotent — no second vision call


def test_tier3_no_image_skips_vision(monkeypatch):
    n = {"v": 0}
    monkeypatch.setattr(
        g,
        "verify_map_with_vision",
        lambda *a, **k: (n.__setitem__("v", n["v"] + 1) or (True, "")),
    )
    rec = {"MapID": "m", "common_name": "Figure 3", "source_url": "http://x/fig/f3.jpg"}
    maybe_vision_classify_map(rec, lambda: None, object())  # no image bytes
    # No image -> vision was NOT attempted: is_map stays unknown (honest) AND checked_at is NOT
    # stamped, so a later pass once the image exists can still classify it (the backstop must not
    # self-disable for its own target case).
    assert n["v"] == 0 and rec.get("is_map") is None
    assert not rec.get("map_vision_checked_at")


def test_tier3_vision_exception_with_image_stamps_checked_at(monkeypatch):
    """A tier-3 image WHERE vision was attempted but Grok raised still stamps checked_at (vision
    was attempted on an available image) so it doesn't retry Grok forever; is_map stays unknown.
    """

    def _boom(*a, **k):
        raise RuntimeError("grok down")

    monkeypatch.setattr(g, "verify_map_with_vision", _boom)
    rec = {"MapID": "m", "common_name": "Figure 9", "source_url": "http://x/fig/f9.jpg"}
    maybe_vision_classify_map(rec, lambda: b"imgbytes", object())
    assert rec.get("is_map") is None  # honest unknown (vision raised)
    assert rec.get(
        "map_vision_checked_at"
    )  # attempted on an available image -> stamped


def test_tier3_loader_exception_stays_reclassifiable(monkeypatch):
    """If the image loader itself raises (image not retrievable yet), vision was NOT attempted:
    checked_at is NOT stamped so a later pass can still classify it."""
    n = {"v": 0}
    monkeypatch.setattr(
        g,
        "verify_map_with_vision",
        lambda *a, **k: (n.__setitem__("v", n["v"] + 1) or (True, "")),
    )

    def _loader_boom():
        raise OSError("not downloaded yet")

    rec = {
        "MapID": "m",
        "common_name": "Figure 10",
        "source_url": "http://x/fig/f10.jpg",
    }
    maybe_vision_classify_map(rec, _loader_boom, object())
    assert n["v"] == 0 and rec.get("is_map") is None
    assert not rec.get("map_vision_checked_at")  # not attempted -> re-classifiable

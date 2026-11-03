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
    assert (
        n["v"] == 0 and rec.get("is_map") is None
    )  # unknown (honest), checked_at stamped
    assert rec.get("map_vision_checked_at")

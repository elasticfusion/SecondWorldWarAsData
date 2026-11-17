"""Maps 5c-ii: tiered classification + idempotent Grok-vision backstop.

Tier 1/2 come from the shared rule (media_classify): an ibiblio URL path decides deterministically
(NO vision). Every other host is tier 3 (ambiguous) -> the shared vision_is_map, ONCE; a re-run
never re-submits. An undecided vision result is NOT stamped (retried, never recorded as a verdict).
"""

import src.extraction.media_classify as mc
from src.extraction.map_common import classify_image_tier, maybe_vision_classify_map

IB = "https://www.ibiblio.org/hyperwar/USA/USA-E-XChannel"


def _count_vision(monkeypatch, verdict):
    n = {"v": 0}

    def _fake(body, ctx, client):
        n["v"] += 1
        return verdict, None

    monkeypatch.setattr(mc, "vision_is_map", _fake)
    return n


def test_tiers_ibiblio_url_rule_and_other_hosts_ambiguous():
    assert classify_image_tier("", f"{IB}/maps/USA-E-XChannel-VII.jpg") == "map"
    assert classify_image_tier("", f"{IB}/img/USA-E-XChannel-40.jpg") == "not_map"
    assert classify_image_tier("", f"{IB}/icn/USA-E-XChannel-40.jpg") == "not_map"
    # Non-ibiblio: URL patterns and captions are not trusted -> vision decides.
    assert classify_image_tier("Map VIII", "http://x/maps/m8.jpg") == "ambiguous"
    assert classify_image_tier("troops photo", "http://x/img/p.jpg") == "ambiguous"


def test_tier1_ibiblio_map_no_vision(monkeypatch):
    n = _count_vision(monkeypatch, True)
    rec = {"MapID": "m", "common_name": "Map VII", "source_url": f"{IB}/maps/X-VII.jpg"}
    maybe_vision_classify_map(rec, lambda: b"img", object())
    assert rec["is_map"] is True and n["v"] == 0


def test_tier2_ibiblio_image_no_vision(monkeypatch):
    n = _count_vision(monkeypatch, True)
    rec = {"MapID": "m", "common_name": "", "source_url": f"{IB}/img/X-40.jpg"}
    maybe_vision_classify_map(rec, lambda: b"img", object())
    assert rec["is_map"] is False and n["v"] == 0


def test_tier3_vision_once_then_idempotent(monkeypatch):
    n = _count_vision(monkeypatch, True)
    rec = {"MapID": "m", "common_name": "Figure 3", "source_url": "http://x/fig/f3.jpg"}
    maybe_vision_classify_map(rec, lambda: b"img", object())
    assert rec["is_map"] is True and rec.get("map_vision_checked_at") and n["v"] == 1
    maybe_vision_classify_map(rec, lambda: b"img", object())  # re-run
    assert n["v"] == 1  # idempotent — no second vision call


def test_tier3_no_image_skips_vision(monkeypatch):
    n = _count_vision(monkeypatch, True)
    rec = {"MapID": "m", "common_name": "Figure 3", "source_url": "http://x/fig/f3.jpg"}
    maybe_vision_classify_map(rec, lambda: None, object())
    assert n["v"] == 0 and rec.get("is_map") is None
    assert not rec.get("map_vision_checked_at")


def test_tier3_undecided_vision_is_not_stamped(monkeypatch):
    """Vision error / 'unknown' must NOT be recorded as checked: it is retried next run instead of
    being silently treated as decided (map vision may be deferred, never ignored)."""
    n = _count_vision(monkeypatch, None)
    rec = {"MapID": "m", "common_name": "Figure 9", "source_url": "http://x/fig/f9.jpg"}
    maybe_vision_classify_map(rec, lambda: b"imgbytes", object())
    assert n["v"] == 1 and rec.get("is_map") is None
    assert not rec.get("map_vision_checked_at")


def test_tier3_loader_exception_stays_reclassifiable(monkeypatch):
    n = _count_vision(monkeypatch, True)

    def _loader_boom():
        raise OSError("not downloaded yet")

    rec = {"MapID": "m", "common_name": "Figure 10", "source_url": "http://x/f10.jpg"}
    maybe_vision_classify_map(rec, _loader_boom, object())
    assert n["v"] == 0 and rec.get("is_map") is None
    assert not rec.get("map_vision_checked_at")

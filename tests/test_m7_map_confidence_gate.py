"""M7: shared map-as-source legibility/confidence gate — a too-illegible map does not inject a
low-confidence event. Benefits ALL maps (the map-wide gate the spec deferred)."""

from src.extraction import map_source_adapter as M

_BASE = {
    "description": "Tactical map near Schmidt.",
    "units": ["28th Infantry Division"],
}


def _dep(conf=None):
    d = dict(_BASE)
    if conf is not None:
        d["legibility_confidence"] = conf
    return d


def test_high_confidence_builds():
    p = M.build_map_parsed_source(
        map_id="m1", map_title="M", approved_image_url="u", depicted=_dep(0.85)
    )
    assert p is not None and p["source_type"] == "map"


def test_low_confidence_skipped():
    assert (
        M.build_map_parsed_source(
            map_id="m2", map_title="M", approved_image_url="u", depicted=_dep(0.15)
        )
        is None
    )
    assert M.render_map_markdown("M", _dep(0.15)) == ""


def test_absent_confidence_unchanged():
    # external/older callers whose vision result omits the field are NOT gated
    p = M.build_map_parsed_source(
        map_id="m3", map_title="M", approved_image_url="u", depicted=_dep(None)
    )
    assert p is not None


def test_boundary_at_threshold():
    thr = M._MIN_LEGIBILITY_CONFIDENCE
    # exactly at threshold passes (gate is strictly-below)
    assert M._render_depicted_text(_dep(thr)) != ""
    # just below is gated
    assert M._render_depicted_text(_dep(thr - 0.01)) == ""


def test_malformed_confidence_not_gated():
    # a non-numeric confidence must not gate (fail-open to current behavior), never crash
    d = dict(_BASE)
    d["legibility_confidence"] = "blurry"
    assert M._render_depicted_text(d) != ""


def test_helper_parses_confidence():
    assert M._legibility_confidence({"legibility_confidence": 0.5}) == 0.5
    assert M._legibility_confidence({}) is None
    assert M._legibility_confidence({"legibility_confidence": "x"}) is None


def test_gate_decision_is_observable(caplog):
    """Observability: both the PASS (built ... legibility=) and SKIP (low_legibility confidence=)
    gate decisions emit canonical OBSERV lines carrying the confidence."""
    import logging

    with caplog.at_level(logging.INFO, logger="src.extraction.map_source_adapter"):
        M.build_map_parsed_source(
            map_id="hi", map_title="M", approved_image_url="u", depicted=_dep(0.85)
        )
        M.build_map_parsed_source(
            map_id="lo", map_title="M", approved_image_url="u", depicted=_dep(0.10)
        )
    text = caplog.text
    assert "OBSERV map_source.built map=hi" in text and "legibility=0.85" in text
    assert "OBSERV map_source.skipped reason=low_legibility confidence=0.10" in text

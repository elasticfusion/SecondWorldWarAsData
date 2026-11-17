"""Phase 2 places carry geo_missing_coords until Phase 3 geocodes them: no alert at Phase 2."""

from src.utils import validation_stats as V
from src.utils.semantic_flags import RESOLVED_IN_PHASE3


def _geo(clusters):
    return [c for c in clusters if c["keyword"] == "geo_missing_coords"]


def _places(monkeypatch, flagged, clean):
    V.reset_stats()
    for _ in range(flagged):
        V.record_validation("places", V.OUTCOME_ALLOW)
        V.record_record_quality("places", {"quality_flags": ["geo_missing_coords"]})
    for _ in range(clean):
        V.record_validation("places", V.OUTCOME_ALLOW)
    sent = []
    monkeypatch.setattr(V, "_alert_systematic", lambda c, r: sent.append(c))
    return sent


def test_phase2_does_not_alert_on_a_flag_phase3_resolves(tmp_path, monkeypatch):
    sent = _places(monkeypatch, 33, 33)
    snap = V.write_validation_stats(tmp_path, deferred_flags=RESOLVED_IN_PHASE3)
    assert not _geo(snap["systematic_clusters"]) and not any(map(_geo, sent))
    assert snap["deferred_flags"] == ["geo_missing_coords"]
    # still counted in the breakdown
    assert any(
        r["keyword"] == "geo_missing_coords" for r in snap["by_entity"]["places"]
    )


def test_after_phase3_the_same_flag_still_alerts(tmp_path, monkeypatch):
    sent = _places(monkeypatch, 33, 33)
    snap = V.write_validation_stats(tmp_path)
    assert _geo(snap["systematic_clusters"]) and _geo(sent[0])

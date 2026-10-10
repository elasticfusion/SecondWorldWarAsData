"""Tests for the validation statistics catcher (data-quality telemetry)."""

from unittest.mock import MagicMock, patch

import src.utils.validation_stats as vs


def setup_function(_):
    vs.reset_stats()


def test_counts_and_block_rate():
    for _ in range(90):
        vs.record_validation("people", "allow")
    for _ in range(10):
        vs.record_validation("people", "block", "required", "ok")
    snap = vs.get_stats()
    assert snap["totals"] == {"allow": 90, "block": 10}
    assert snap["block_rate"] == 0.1


def test_systematic_cluster_detected_above_thresholds():
    for _ in range(88):
        vs.record_validation("people", "allow")
    for _ in range(12):  # 12/100 = 12% >= 5%, count 12 >= 10 -> systematic
        vs.record_validation("people", "block", "required", "ok")
    clusters = vs.systematic_clusters()
    assert len(clusters) == 1
    assert clusters[0]["entity"] == "people" and clusters[0]["keyword"] == "required"
    assert clusters[0]["rate"] == 0.12


def test_populated_content_count_ignores_metadata_pk_and_placeholders():
    # PK (PersonID), metadata, quality_flags, and empty/null/0.0 values do not count as content.
    rec = {
        "PersonID": "01ABC",
        "_schema_version": "2.3",
        "quality_flags": ["x"],
        "name": "Smith",  # populated content (1)
        "rank": "",  # empty -> not counted
        "nationality": None,  # null -> not counted
    }
    assert vs._populated_content_count("people", rec) == 1


def test_places_zero_coords_not_counted_as_content():
    rec = {"PlaceID": "01P", "latitude": 0.0, "longitude": 0.0, "name": "X"}
    # name counts (1); 0.0/0.0 coords are the no-geocode placeholder -> not content.
    assert vs._populated_content_count("places", rec) == 1


def test_record_record_quality_emits_allow_empty_below_floor():
    # people floor is 2; a bare record has <2 populated content fields.
    vs.record_record_quality("people", {"PersonID": "01A", "name": "Smith"})
    snap = vs.get_stats()
    assert snap["totals"].get("allow_empty") == 1


def test_record_record_quality_no_empty_at_or_above_floor():
    vs.record_record_quality(
        "people", {"PersonID": "01A", "name": "Smith", "rank": "Capt"}
    )
    assert "allow_empty" not in vs.get_stats()["totals"]


def test_record_record_quality_emits_allow_flagged_per_code():
    vs.record_record_quality(
        "places",
        {
            "PlaceID": "01P",
            "name": "X",
            "country": "FR",
            "quality_flags": ["geo_missing_coords"],
        },
    )
    snap = vs.get_stats()
    assert snap["totals"].get("allow_flagged") == 1


def test_systematic_cluster_detects_flagged_outcome():
    for _ in range(88):
        vs.record_validation("places", "allow")
    for _ in range(12):
        vs.record_validation("places", "allow_flagged", "geo_missing_coords", "ok")
    clusters = {c["keyword"]: c for c in vs.systematic_clusters()}
    assert "geo_missing_coords" in clusters
    assert clusters["geo_missing_coords"]["outcome"] == "allow_flagged"
    assert clusters["geo_missing_coords"]["rate"] == 0.12


def test_systematic_cluster_detects_empty_outcome():
    for _ in range(80):
        vs.record_validation("people", "allow")
    for _ in range(20):
        vs.record_validation("people", "allow_empty", "below_floor", "ok")
    clusters = {c["outcome"] for c in vs.systematic_clusters()}
    assert "allow_empty" in clusters


def test_entity_without_floor_never_emits_empty():
    # 'maps' has no floor entry -> never allow_empty even for a bare record.
    vs.record_record_quality("maps", {"MapID": "01M"})
    assert "allow_empty" not in vs.get_stats()["totals"]


def test_isolated_failures_not_systematic():
    # Below BOTH count and rate thresholds -> not a cluster (transient; auto-reprocess handles).
    for _ in range(200):
        vs.record_validation("places", "allow")
    for _ in range(3):
        vs.record_validation("places", "block", "pattern", "ok")
    assert vs.systematic_clusters() == []


def test_count_high_but_rate_low_not_systematic():
    # 12 blocks but out of 100000 writes -> 0.012% -> not systematic (noise at scale).
    for _ in range(100000):
        vs.record_validation("dates", "allow")
    for _ in range(12):
        vs.record_validation("dates", "block", "required", "ok")
    assert vs.systematic_clusters() == []


def test_alert_fires_on_systematic(monkeypatch):
    monkeypatch.setenv(
        "NOTIFICATION_TOPIC_ARN", "arn:aws:sns:us-east-1:1:dev-wwii-phase2-complete"
    )
    for _ in range(88):
        vs.record_validation("people", "allow")
    for _ in range(12):
        vs.record_validation("people", "block", "required", "ok")
    sns = MagicMock()
    with patch("boto3.client", return_value=sns):
        vs.write_validation_stats("/tmp/vstest_alert")
    sns.publish.assert_called_once()
    body = sns.publish.call_args.kwargs["Message"]
    assert "systematic validation failures" in body.lower()
    assert "people" in body and "required" in body
    # PII-safe: no record values, only aggregate counts
    assert "12/100" in body


def test_no_alert_when_no_systematic(monkeypatch):
    monkeypatch.setenv("NOTIFICATION_TOPIC_ARN", "arn:aws:sns:us-east-1:1:topic")
    for _ in range(50):
        vs.record_validation("places", "allow")
    sns = MagicMock()
    with patch("boto3.client", return_value=sns):
        vs.write_validation_stats("/tmp/vstest_noalert")
    sns.publish.assert_not_called()


def test_record_is_failsafe():
    # Must never raise even on bad input.
    vs.record_validation(None, None)  # type: ignore[arg-type]
    assert isinstance(vs.get_stats(), dict)


def test_file_lock_integration_records_allow_flagged(tmp_path):
    """INTEGRATION: an allowed write carrying a semantic flag records allow_flagged via the guard,
    and the record_stats=False pre-check path does NOT (record-once contract)."""
    import src.utils.file_lock as fl

    vs.reset_stats()
    d = tmp_path / "places"
    d.mkdir()
    rec = {
        "PlaceID": "01HX7YZABCDEFGHJKMNPQRSTVW",
        "name": "X",
        "latitude": 0.0,
        "longitude": 0.0,  # -> semantic flag geo_missing_coords
    }
    # Real write (record_stats defaults True): annotate -> validate(allow) -> record quality.
    assert fl._validate_entity(d / "a.json", dict(rec), record_stats=True) is True
    totals = vs.get_stats()["totals"]
    assert totals.get("allow_flagged") == 1  # geo_missing_coords recorded once

    # Pre-check path (record_stats=False) must NOT add another datapoint.
    before = vs.get_stats()["totals"].get("allow_flagged")
    fl._validate_entity(d / "b.json", dict(rec), record_stats=False)
    assert vs.get_stats()["totals"].get("allow_flagged") == before

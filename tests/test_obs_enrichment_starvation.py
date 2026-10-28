"""PR-C (enrichment starvation) — observability-hardening regression tests.

Covers TOP-10 P1 items #4, #5, #6 from OBSERVABILITY_SWALLOWED_ALERTS:

  #4  openserp write_metrics: expected-yield baseline `eligible`; WARN when
      entities_searched << eligible (searched=1-of-14 no longer silent).
  #5  openserp anomalies are INDEPENDENT (not elif): a breaker-open run can ALSO
      fire the 0-enriched starvation anomaly instead of masking it.
      + the aggregate skip-no-name counter WARN (6/6 groups enrich_skip_no_name class).
  #6  bibliography_resolver: a not_found produced while OpenSERP was disabled for the
      run is tagged `backend_down` (transient), never an authoritative "no copy exists".

All new observability is fail-safe (must never raise into the enrichment phase).
"""

import logging

import src.enrichment.bibliography_resolver as br
import src.enrichment.openserp_enrichment as oe


# --------------------------------------------------------------------------- #4 eligible baseline
def test_eligible_baseline_in_snapshot_and_search_rate():
    oe.reset_metrics()
    oe._metric("entities_eligible", 14)
    oe._metric("entities_searched", 1)
    snap = oe.get_metrics()
    assert snap["entities_eligible"] == 14
    assert snap["entities_searched"] == 1
    # search_rate = searched / eligible = 1/14 ≈ 0.071
    assert snap["search_rate"] == round(1 / 14, 3)


def test_search_starvation_warns_when_searched_far_below_eligible(tmp_path, caplog):
    oe.reset_metrics()
    oe._metric("entities_eligible", 14)
    oe._metric("entities_searched", 1)
    oe._metric(
        "entities_enriched", 1
    )  # avoid the 0-enriched anomaly; isolate the starvation one
    with caplog.at_level(logging.WARNING, logger=oe.__name__):
        oe.write_metrics(tmp_path)
    anomalies = [
        r.getMessage() for r in caplog.records if "openserp.anomaly" in r.getMessage()
    ]
    assert any("searched=1-of-eligible=14" in m for m in anomalies), anomalies


def test_healthy_search_rate_does_not_warn(tmp_path, caplog):
    oe.reset_metrics()
    oe._metric("entities_eligible", 10)
    oe._metric("entities_searched", 10)
    oe._metric("entities_enriched", 6)
    with caplog.at_level(logging.WARNING, logger=oe.__name__):
        oe.write_metrics(tmp_path)
    assert not [r for r in caplog.records if "openserp.anomaly" in r.getMessage()]


def test_eligible_persisted_to_metrics_json(tmp_path):
    import json

    oe.reset_metrics()
    oe._metric("entities_eligible", 5)
    oe._metric("entities_searched", 5)
    oe._metric("entities_enriched", 2)
    oe.write_metrics(tmp_path)
    data = json.loads((tmp_path / "metrics" / "openserp_metrics.json").read_text())
    assert data["entities_eligible"] == 5
    assert data["search_rate"] == 1.0


# ----------------------------------------------------------- #5 independent anomaly (not elif)
def test_breaker_open_does_not_mask_zero_enriched(tmp_path, caplog):
    """The 0-enriched starvation anomaly must fire EVEN WHEN the breaker is open (was an `elif`,
    so breaker-open silently masked the 0-of-N enriched signal)."""
    oe.reset_metrics()
    oe._metric("entities_eligible", 8)
    oe._metric("entities_searched", 8)
    oe._metric("entities_enriched", 0)
    oe._metric("breaker_opened", 1)
    with caplog.at_level(logging.WARNING, logger=oe.__name__):
        oe.write_metrics(tmp_path)
    msgs = [
        r.getMessage() for r in caplog.records if "openserp.anomaly" in r.getMessage()
    ]
    # BOTH the breaker anomaly AND the 0-enriched anomaly must be present.
    assert any("circuit_breaker_opened" in m for m in msgs), msgs
    assert any("0-of-8 entities enriched" in m for m in msgs), msgs


def test_skip_no_name_fraction_warns(tmp_path, caplog):
    oe.reset_metrics()
    oe._metric("entities_eligible", 0)
    oe._metric("entities_skipped_no_name", 6)  # the 6/6 groups class
    with caplog.at_level(logging.WARNING, logger=oe.__name__):
        oe.write_metrics(tmp_path)
    msgs = [
        r.getMessage() for r in caplog.records if "openserp.anomaly" in r.getMessage()
    ]
    assert any("skipped_no_name=6-of-6" in m for m in msgs), msgs


def test_usable_entity_name_counts_skips():
    oe.reset_metrics()
    assert oe._usable_entity_name("Patton") is True
    assert oe._usable_entity_name("") is False
    assert oe._usable_entity_name(None) is False
    assert oe._usable_entity_name("ab", min_len=3) is False  # too short
    assert oe._usable_entity_name("abc", min_len=3) is True
    # Only the 3 unusable calls incremented the skip counter.
    assert oe.get_metrics()["entities_skipped_no_name"] == 3


def test_observability_is_fail_safe_on_bad_snapshot(tmp_path, monkeypatch):
    """write_metrics must never raise even if the snapshot is malformed — the try/except boundary
    inside write_metrics is the fail-safe contract (observability can't halt the phase).
    """
    monkeypatch.setattr(
        oe, "get_metrics", lambda: {"generated_at": None}
    )  # missing every key
    # Must return the (broken) snapshot WITHOUT raising.
    result = oe.write_metrics(tmp_path)
    assert isinstance(result, dict)


# ------------------------------------------------------- #6 bibliography backend_down tagging
def test_backend_down_not_persisted_and_remapped_to_accounting_status():
    """A not_found produced while OpenSERP is down is accounted as `backend_down`, and the
    internal marker is stripped so the on-disk record stays schema-valid."""
    data = {"search_status": "not_found", "_backend_down": True}
    account = br._consume_backend_down(data, "not_found")
    assert account == "backend_down"
    assert "_backend_down" not in data  # marker never persists (schema is strict)


def test_backend_down_only_remaps_not_found():
    # A resolved/ref_identified record is NOT downgraded even if the marker was set.
    data = {"_backend_down": True}
    assert br._consume_backend_down(data, "resolved") == "resolved"
    assert "_backend_down" not in data


def test_no_marker_leaves_status_unchanged():
    assert br._consume_backend_down({}, "not_found") == "not_found"


def test_mark_backend_down_fail_safe():
    # Non-dict / None must not raise.
    br._mark_backend_down(None)
    br._mark_backend_down("not a dict")  # type: ignore[arg-type]
    d: dict = {}
    br._mark_backend_down(d)
    assert d["_backend_down"] is True


def test_openserp_disabled_tags_entry_backend_down(monkeypatch):
    """When OpenSERP was disabled for the run, _search_openserp_archive returns None AND tags the
    entry so the resulting miss is recorded as a transient outage, not an authoritative absence.
    """
    monkeypatch.setattr(br, "_openserp_down", True)
    entry: dict = {}
    result = br._search_openserp_archive(
        "some ref", "http://localhost:7001", None, entry
    )
    assert result is None
    assert entry.get("_backend_down") is True


def test_backend_down_excluded_from_human_queue():
    """A backend_down (transient) status must NOT be queued for human 'not found online'
    disposition (a later run retries it)."""
    stats = {k: 0 for k in ("not_found", "backend_down", "deduped", "resolved")}
    needs_queue = br._account_bib_status(stats, "backend_down", was_deduped=False)
    assert needs_queue is False
    assert stats["backend_down"] == 1
    # A genuine not_found still needs human disposition.
    assert br._account_bib_status(stats, "not_found", was_deduped=False) is True


def test_backend_down_summary_warns(caplog):
    stats = {
        "resolved": 0,
        "reference_identified": 0,
        "not_found": 0,
        "backend_down": 3,
        "not_citation": 0,
        "queued": 0,
        "held_unprocessed": 0,
        "deduped": 0,
        "skipped": 0,
        "errors": 0,
    }
    with caplog.at_level(logging.WARNING, logger=br.__name__):
        br._log_bib_resolution_summary(stats, processed=3, not_citation_samples=[])
    msgs = [r.getMessage() for r in caplog.records]
    assert any("backend_down=3" in m for m in msgs), msgs

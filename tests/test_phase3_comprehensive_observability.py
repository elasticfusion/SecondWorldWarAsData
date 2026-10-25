"""Comprehensive Phase-3 observability: each enricher (+ the central validation guard) emits a
canonical OBSERV <name>.summary and an anomaly on 0-of-N / systematic-block. Anomalies are gated so
a benign re-run of an already-enriched corpus does NOT false-fire (groups/places use a 'needed'
pre-count; bibliography excludes not_citation from the denominator)."""

import json
import logging


def _msgs(caplog):
    return "\n".join(r.getMessage() for r in caplog.records)


# --- central validation guard ---------------------------------------------------------------
def test_validation_summary_and_systematic_observ(tmp_path, caplog):
    import src.utils.validation_stats as V

    V.reset_stats()
    for _ in range(20):
        V.record_validation(
            "bibliography", V.OUTCOME_BLOCK, "additionalProperties", "ok"
        )
    for _ in range(5):
        V.record_validation("people", V.OUTCOME_ALLOW, None, "ok")
    with caplog.at_level(logging.INFO):
        V.write_validation_stats(tmp_path)
    m = _msgs(caplog)
    assert "OBSERV validation.summary" in m and "block=20" in m
    assert "OBSERV validation.systematic entity=bibliography" in m
    assert "keyword=additionalProperties" in m
    V.reset_stats()


def test_validation_clean_no_systematic(tmp_path, caplog):
    import src.utils.validation_stats as V

    V.reset_stats()
    for _ in range(10):
        V.record_validation("people", V.OUTCOME_ALLOW, None, "ok")
    with caplog.at_level(logging.INFO):
        V.write_validation_stats(tmp_path)
    m = _msgs(caplog)
    assert "OBSERV validation.summary" in m and "block=0" in m
    assert "validation.systematic" not in m
    V.reset_stats()


# --- groups (1/6) ----------------------------------------------------------------------------
def test_groups_summary_and_anomaly(tmp_path, monkeypatch, caplog):
    import src.extraction.enrich_groups as G

    (tmp_path / "g1.json").write_text(json.dumps({"group_name": "28th Division"}))
    (tmp_path / "g2.json").write_text(json.dumps({"group_name": "4th Division"}))
    monkeypatch.setattr(
        G, "enrich_group", lambda f, c: False
    )  # needed but none enriched
    with caplog.at_level(logging.INFO):
        G.enrich_all_groups(tmp_path, object(), max_workers=1)
    m = _msgs(caplog)
    assert "OBSERV groups.summary total=2 needed=2 enriched=0" in m
    assert "OBSERV groups.anomaly 0-of-2" in m


def test_groups_no_anomaly_on_already_enriched_rerun(tmp_path, monkeypatch, caplog):
    """Already-enriched corpus: _load_enrichable_group -> None (needed=0) so no false anomaly."""
    import src.extraction.enrich_groups as G

    (tmp_path / "g1.json").write_text(
        json.dumps(
            {
                "group_name": "28th Division",
                "enrichment_status": "enriched",
                "wikipedia_url": "x",
            }
        )
    )
    monkeypatch.setattr(G, "enrich_group", lambda f, c: False)
    monkeypatch.setattr(G, "_load_enrichable_group", lambda f: None)  # all already-done
    with caplog.at_level(logging.INFO):
        G.enrich_all_groups(tmp_path, object(), max_workers=1)
    m = _msgs(caplog)
    assert "needed=0" in m
    assert "groups.anomaly" not in m


# --- places (2/6) ----------------------------------------------------------------------------
def test_places_summary_and_anomaly(tmp_path, monkeypatch, caplog):
    import src.extraction.enrich_places as P

    (tmp_path / "p1.json").write_text(
        json.dumps({"name": "Aachen"})
    )  # named, no hierarchy -> needed
    monkeypatch.setattr(P, "enrich_place", lambda f, c: False)
    with caplog.at_level(logging.INFO):
        P.enrich_all_places(tmp_path, object(), max_workers=1)
    m = _msgs(caplog)
    assert "OBSERV places.summary total=1 needed=1 enriched=0" in m
    assert "OBSERV places.anomaly 0-of-1" in m


def test_places_no_anomaly_on_already_enriched_rerun(tmp_path, monkeypatch, caplog):
    import src.extraction.enrich_places as P

    # already has hierarchy -> _needs_enrichment False -> needed=0
    (tmp_path / "p1.json").write_text(
        json.dumps(
            {
                "name": "Aachen",
                "hierarchy": {"continent": "Europe", "country": "Germany"},
            }
        )
    )
    monkeypatch.setattr(P, "enrich_place", lambda f, c: False)
    with caplog.at_level(logging.INFO):
        P.enrich_all_places(tmp_path, object(), max_workers=1)
    m = _msgs(caplog)
    assert "needed=0" in m
    assert "places.anomaly" not in m


# --- openserp (5/6) --------------------------------------------------------------------------
def test_openserp_summary_and_breaker_anomaly(tmp_path, caplog):
    import src.enrichment.openserp_enrichment as O

    O.reset_metrics()
    O._metric("entities_searched", 3)
    O._metric("breaker_opened", 1)
    with caplog.at_level(logging.INFO):
        O.write_metrics(tmp_path)
    m = _msgs(caplog)
    assert "OBSERV openserp.summary" in m and "searched=3" in m
    assert "OBSERV openserp.anomaly circuit_breaker_opened=1" in m
    O.reset_metrics()


def test_openserp_zero_enriched_anomaly(tmp_path, caplog):
    import src.enrichment.openserp_enrichment as O

    O.reset_metrics()
    O._metric("entities_searched", 4)
    with caplog.at_level(logging.INFO):
        O.write_metrics(tmp_path)
    m = _msgs(caplog)
    assert "OBSERV openserp.anomaly 0-of-4 entities enriched" in m
    O.reset_metrics()


def test_openserp_healthy_no_anomaly(tmp_path, caplog):
    import src.enrichment.openserp_enrichment as O

    O.reset_metrics()
    O._metric("entities_searched", 5)
    O._metric("entities_enriched", 4)
    with caplog.at_level(logging.INFO):
        O.write_metrics(tmp_path)
    m = _msgs(caplog)
    assert "OBSERV openserp.summary" in m
    assert "openserp.anomaly" not in m
    O.reset_metrics()


# --- bibliography-resolve (4/6b) -------------------------------------------------------------
def _bib_stats(**kw):
    base = {
        "resolved": 0,
        "reference_identified": 0,
        "not_found": 0,
        "not_citation": 0,
        "deduped": 0,
        "queued": 0,
        "held_unprocessed": 0,
        "skipped": 0,
        "errors": 0,
    }
    base.update(kw)
    return base


def test_bibliography_resolve_summary_and_anomaly(caplog):
    import src.enrichment.bibliography_resolver as B

    with caplog.at_level(logging.INFO):
        B._log_bib_resolution_summary(
            _bib_stats(not_found=5, not_citation=1, queued=5), 6, []
        )
    m = _msgs(caplog)
    assert "OBSERV bibliography.resolve_summary in=6" in m
    assert (
        "OBSERV bibliography.resolve_anomaly 0-of-5" in m
    )  # 6 - 1 not_citation = 5 resolvable


def test_bibliography_all_not_citation_no_anomaly(caplog):
    """All-prose corpus: resolvable = processed - not_citation = 0 -> NO anomaly (headline safety)."""
    import src.enrichment.bibliography_resolver as B

    with caplog.at_level(logging.INFO):
        B._log_bib_resolution_summary(_bib_stats(not_citation=4), 4, [])
    m = _msgs(caplog)
    assert "OBSERV bibliography.resolve_summary in=4" in m
    assert "resolve_anomaly" not in m


def test_bibliography_errors_anomaly(caplog):
    import src.enrichment.bibliography_resolver as B

    with caplog.at_level(logging.INFO):
        B._log_bib_resolution_summary(_bib_stats(resolved=3, errors=2), 5, [])
    m = _msgs(caplog)
    assert "OBSERV bibliography.resolve_anomaly errors=2 of in=5" in m


def test_bibliography_resolve_no_anomaly_when_resolved(caplog):
    import src.enrichment.bibliography_resolver as B

    with caplog.at_level(logging.INFO):
        B._log_bib_resolution_summary(
            _bib_stats(resolved=3, reference_identified=1, not_found=1, not_citation=1),
            6,
            [],
        )
    m = _msgs(caplog)
    assert "OBSERV bibliography.resolve_summary in=6" in m
    assert "resolve_anomaly" not in m

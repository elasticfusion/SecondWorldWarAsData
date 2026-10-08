"""Tests for OpenSERP effectiveness/health metrics."""

import json

import src.enrichment.openserp_enrichment as oe


def test_metrics_counters_and_rates():
    oe.reset_metrics()
    oe._metric("entities_searched", 4)
    oe._metric("entities_enriched", 2)
    oe._metric("queries_issued", 10)
    oe._metric("zero_result_queries", 3)
    oe._metric("verify_yes", 8)
    oe._metric("verify_no", 2)
    snap = oe.get_metrics()
    assert snap["enrichment_rate"] == 0.5  # 2/4
    assert snap["verify_pass_rate"] == 0.8  # 8/10
    assert snap["zero_result_rate"] == 0.3  # 3/10


def test_metrics_zero_safe():
    oe.reset_metrics()
    snap = oe.get_metrics()  # no division-by-zero on an empty run
    assert snap["enrichment_rate"] == 0.0
    assert snap["verify_pass_rate"] == 0.0


def test_write_metrics_produces_json(tmp_path):
    oe.reset_metrics()
    oe._metric("entities_enriched", 1)
    oe.write_metrics(tmp_path)
    f = tmp_path / "metrics" / "openserp_metrics.json"
    assert f.exists()
    data = json.loads(f.read_text())
    assert data["entities_enriched"] == 1
    assert "generated_at" in data and "verify_pass_rate" in data


def test_reset_metrics():
    oe._metric("queries_issued", 5)
    oe.reset_metrics()
    assert oe.get_metrics()["queries_issued"] == 0


def test_apply_path_counts_items_and_fetches(monkeypatch):
    """The people apply path must feed items_added (image + award) and urls_fetched
    (regression for the gap where enriched>0 but items_added/urls_fetched stayed 0)."""
    oe.reset_metrics()
    # Accept every verification; fetch returns a usable summary (counts a real fetch).
    monkeypatch.setattr(oe, "_verify_result", lambda *a, **k: True)
    monkeypatch.setattr(oe, "_summarize_url_page", lambda *a, **k: "summary text")
    monkeypatch.setattr(oe, "_url_verdict_cached", lambda url: None)
    monkeypatch.setattr(oe, "_cache_url_verdict", lambda *a, **k: None)
    monkeypatch.setattr(oe, "_name_initial_matches", lambda *a, **k: True)
    data: dict = {}
    candidate = {
        "image_results": [{"url": "http://img/1", "title": "John Doe"}],
        "web_results": [
            {"url": "http://bio/1", "title": "John Doe", "description": "x"}
        ],
    }
    changed = oe._verify_and_apply(
        candidate, data, "John Doe", grok_client=object(), max_images=5, max_web=5
    )
    assert changed is True
    snap = oe.get_metrics()
    assert snap["items_added"] == 2  # one image + one award
    assert snap["urls_fetched"] == 1  # the award URL was actually fetched

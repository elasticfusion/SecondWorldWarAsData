"""``openserp.results_per_query`` sets how many results each OpenSERP query asks for (it was
ignored: every query asked for 5)."""

import logging
from unittest import mock

import pytest

import src.enrichment.openserp_enrichment as oe


def _limit_sent(monkeypatch, cfg):
    monkeypatch.setattr(
        "src.utils.config.load_config", lambda *a, **k: {"openserp": cfg}
    )
    monkeypatch.setattr("time.sleep", lambda *_: None)
    oe.reset_circuit()
    sent = {}

    def fetch(*a, **k):
        sent.update(k["params"])
        return mock.Mock(status_code=200, json=lambda: [])

    monkeypatch.setattr("src.utils.fetch_provenance.recorded_fetch", fetch)
    oe._search_openserp("q", "http://serp")
    return sent["limit"]


def test_the_configured_number_of_results_is_asked_for(monkeypatch):
    assert _limit_sent(monkeypatch, {"results_per_query": 8}) == "8"


def test_the_default_is_five(monkeypatch):
    assert _limit_sent(monkeypatch, {}) == "5"


@pytest.mark.parametrize("bad", [0, 101, "10", 2.5, True, None])
def test_a_bad_value_is_said_and_the_default_used(monkeypatch, caplog, bad):
    from src.utils import observe

    monkeypatch.setattr(observe, "_counts", {})
    with caplog.at_level(logging.WARNING):
        assert _limit_sent(monkeypatch, {"results_per_query": bad}) == "5"
        assert _limit_sent(monkeypatch, {"results_per_query": bad}) == "5"
    assert caplog.text.count("bad_results_per_query") == 1  # said once, not per query


@pytest.mark.parametrize("edge", [1, 100])
def test_the_range_edges_are_accepted(monkeypatch, edge):
    assert _limit_sent(monkeypatch, {"results_per_query": edge}) == str(edge)


def test_the_shipped_configs_keep_five():
    from pathlib import Path

    import yaml

    root = Path(__file__).resolve().parents[1]
    for path in (
        "config.yaml.example",
        "config/profiles/balanced.yaml",
        "config/profiles/performance-optimized.yaml",
        "config/profiles/review-all-data.yaml",
    ):
        with open(root / path, encoding="utf-8") as f:
            assert yaml.safe_load(f)["openserp"]["results_per_query"] == 5, path

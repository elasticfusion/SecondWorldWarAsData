"""Task 1.2f: a citation whose search sources are down (`backend_down`) is retried next run, never
queued as "not found online". After BACKEND_DOWN_RUN_CAP such runs (each within 30 days of the last) it is ALSO queued for a
human (with the reason), so a source that stays down is not retried silently forever. The count
lives in the shared search cache (the bibliography schema has no room for it)."""

import json

import pytest

from src.enrichment import bibliography_resolver as br
from src.utils import search_cache


@pytest.fixture
def cache(monkeypatch, tmp_path):
    backend = search_cache._LocalBackend(tmp_path / "cache")
    monkeypatch.setattr(search_cache, "_get_backend", lambda: backend)
    return backend


def _run(monkeypatch, tmp_path, status="backend_down", deduped=False):
    f = tmp_path / "bib_abc.json"
    f.write_text(json.dumps({"citation": {"title": "T"}, "verbatim_reference": "V"}))
    monkeypatch.setattr(br, "_process_bib_file", lambda *a: (status, deduped, ""))
    stats = {
        k: 0 for k in ("resolved", "not_found", "queued", "backend_down", "deduped")
    }
    queue: list = []
    br._resolve_one_bib_file(f, None, {}, {}, stats, [], queue)
    return stats, queue


def test_an_outage_is_retried_until_the_cap_then_queued_for_a_human(
    cache, monkeypatch, tmp_path
):
    for _ in range(br.BACKEND_DOWN_RUN_CAP - 1):
        stats, queue = _run(monkeypatch, tmp_path)
        assert queue == [] and stats["backend_down"] == 1
    stats, queue = _run(monkeypatch, tmp_path)
    [entry] = queue
    assert f"{br.BACKEND_DOWN_RUN_CAP} runs" in entry["reason"]
    assert entry["file"] == "bib_abc.json" and stats["outage_capped"] == 1


def test_a_clean_miss_is_not_counted(cache, monkeypatch, tmp_path):
    for _ in range(br.BACKEND_DOWN_RUN_CAP + 1):
        _, queue = _run(monkeypatch, tmp_path, status="resolved")
        assert queue == []
    assert br._count_outage_run("bib_abc.json") == 1  # nothing counted before


def test_a_deduped_citation_is_not_counted_twice(cache, monkeypatch, tmp_path):
    for _ in range(br.BACKEND_DOWN_RUN_CAP + 1):
        _, queue = _run(monkeypatch, tmp_path, deduped=True)
        assert queue == []


def test_a_cache_failure_falls_back_to_retrying(monkeypatch, caplog):
    def broken():
        raise RuntimeError("ddb")

    monkeypatch.setattr(search_cache, "_get_backend", broken)
    assert br._count_outage_run("bib_abc.json") == 0
    assert "OBSERV bibliography.outage_count_failed" in caplog.text


def test_a_garbled_count_starts_again(cache):
    key = search_cache._make_key("bibliography_outage_runs", "bib_abc.json")
    cache.put(key, "not-a-number")
    assert br._count_outage_run("bib_abc.json") == 1


def test_max_items_counts_resolved_records_not_skipped_ones(tmp_path, monkeypatch):
    """Task 1.2f: already-resolved (skipped) records used to use up the cap, so a capped run
    could end having worked on nothing."""
    for i in range(6):
        (tmp_path / f"bib_{i}.json").write_text("{}")
    order = []

    def fake(f, grok, cfg, by_key, stats, samples, queue):
        order.append(f.name)
        stats["skipped" if f.name < "bib_4.json" else "resolved"] += 1
        if f.name == "bib_5.json":
            queue.append({"file": f.name})

    monkeypatch.setattr(br, "_resolve_one_bib_file", fake)
    monkeypatch.setattr(br, "build_holdings_index", lambda: {})
    out = br.resolve_bibliography_dir(tmp_path, None, {}, max_items=2)
    assert order == [f"bib_{i}.json" for i in range(6)] and out["resolved"] == 2
    # the review queue was written as soon as it grew (not only at the end)
    assert (tmp_path / "review_queue.json").exists()

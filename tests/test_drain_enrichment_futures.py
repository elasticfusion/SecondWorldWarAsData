"""DRY P2.3 guard: the identical future-draining loop in enrich_groups/enrich_places is now the
shared _entity_common.drain_enrichment_futures. Pin the drain contract (count successes, tolerate
one failure, log progress) + that both collectors delegate.
"""

import logging
from concurrent.futures import ThreadPoolExecutor

from src.extraction._entity_common import drain_enrichment_futures


def _run(results):
    # results: list of callables -> bool|raise. Build a {future: fake-file} map.
    class _F:
        def __init__(self, stem):
            self.stem = stem

    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {pool.submit(r): _F(f"item{i}") for i, r in enumerate(results)}
        return drain_enrichment_futures(futures, len(results), "widget")


def test_counts_successes_only():
    assert _run([lambda: True, lambda: False, lambda: True]) == 2


def test_one_failure_does_not_abort(caplog):
    def boom():
        raise RuntimeError("x")

    with caplog.at_level(logging.WARNING):
        n = _run([lambda: True, boom, lambda: True])
    assert n == 2  # the 2 successes still counted despite the raise
    assert "Failed to enrich widget" in caplog.text


def test_both_collectors_delegate(monkeypatch):
    import src.extraction._entity_common as ec
    import src.extraction.enrich_groups as eg
    import src.extraction.enrich_places as ep

    calls = []
    monkeypatch.setattr(
        ec, "drain_enrichment_futures", lambda fut, tot, noun: calls.append(noun) or 0
    )
    eg._collect_group_enrichments({}, 0)
    ep._collect_place_enrichments({}, 0)
    assert calls == ["group", "place"]

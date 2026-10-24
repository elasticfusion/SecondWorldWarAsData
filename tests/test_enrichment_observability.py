"""Observability: Phase-2 people-enrichment coverage summary + 0-of-N anomaly (task-local).

The people-enrichment-never-ran bug hid because a wholesale no-op produced ZERO aggregate signal.
`enrich.summary` + `enrich.anomaly` make a 0-of-N coverage gap LOUD. Counters are TASK-LOCAL
(ContextVar) so concurrent chapters don't interleave.
"""

import asyncio
import logging

import src.extraction.people as P


def _set_counts(counts):
    P._reset_enrich_counts()
    for key, n in counts.items():
        for _ in range(n):
            P._bump_enrich(key)


def _summary(caplog, counts):
    _set_counts(counts)
    with caplog.at_level(logging.INFO, logger="src.extraction.people"):
        P._emit_enrich_summary("batch")
    return caplog.text


def test_summary_always_emitted_with_counts(caplog):
    text = _summary(caplog, {"total": 5, "enriched": 4, "skipped": 1})
    assert "OBSERV enrich.summary" in text
    assert "total=5 enriched=4 skipped_already=1 collecting=0 errors=0" in text


def test_anomaly_fires_on_zero_of_n(caplog):
    text = _summary(caplog, {"total": 7})
    assert "OBSERV enrich.anomaly" in text
    assert "7 people extracted but 0 enriched" in text


def test_no_anomaly_when_enriched(caplog):
    assert "enrich.anomaly" not in _summary(caplog, {"total": 7, "enriched": 7})


def test_no_anomaly_when_collecting(caplog):
    assert "enrich.anomaly" not in _summary(caplog, {"total": 7, "collecting": 7})


def test_no_anomaly_when_zero_people(caplog):
    text = _summary(caplog, {"total": 0})
    assert "enrich.anomaly" not in text
    assert "OBSERV enrich.summary" in text


def test_bump_without_reset_is_noop():
    """_bump_enrich before any reset must not raise (fail-safe) — unset context."""
    import contextvars

    # Run in a fresh context where _enrich_counts is unset.
    def _run():
        P._bump_enrich("total")  # no reset -> no-op, must not raise
        P._emit_enrich_summary("sync")  # no context -> returns silently

    contextvars.copy_context().run(_run)


def test_counts_are_task_local_under_concurrency():
    """Concurrent async tasks must NOT share counters: each task's reset/bumps stay isolated,
    so one chapter's reset can't zero another's in-flight counts (the reviewer's concurrency bug).
    """
    results = {}

    async def worker(name, n_enriched):
        P._reset_enrich_counts()
        for _ in range(n_enriched):
            P._bump_enrich("enriched")
            await asyncio.sleep(0)  # yield -> interleave with the other task
        P._bump_enrich("total")
        c = P._enrich_counts.get()
        results[name] = (c["enriched"], c["total"])

    async def main():
        # Mirror the PRODUCTION scheduling exactly: gather of wait_for(coro) (NOT create_task).
        # wait_for wraps each coroutine in a Task, which copies the context -> per-chapter isolation.
        await asyncio.gather(
            asyncio.wait_for(worker("A", 3), timeout=5),
            asyncio.wait_for(worker("B", 5), timeout=5),
        )

    asyncio.run(main())
    # If counters were shared, interleaving would corrupt these; task-local keeps them exact.
    assert results["A"] == (3, 1)
    assert results["B"] == (5, 1)

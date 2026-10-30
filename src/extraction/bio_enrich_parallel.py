"""Bounded-parallel Phase-2 bio enrichment (HIGH throughput fix).

Phase-2 people enrichment is dominated by *fixed network timeouts*
(Grokipedia timeout=30 x retries, Wikipedia timeout+retries + a shared
courtesy delay, photo timeout=30), so it runs ~3 min/person. Done serially
that is ~3h for ~60 people.

The per-person fetch ``enrich_person_from_sources`` does **no file I/O** — it
mutates only its own ``person_data`` dict via independent network calls — so the
FETCH is safe to run concurrently. The PERSIST
(``write_json_with_lock`` + the shared people index mutation in ``people.py``)
must stay SERIAL/locked, so this module ONLY parallelizes the fetch:

    1. collect the people needing enrichment,
    2. run ``enrich_person_from_sources`` across a small, bounded thread pool,
       mutating each person's own dict in place,
    3. return to the caller, which then persists serially (unchanged).

Thread-safety (verified before shipping — see PR body):
  * each worker touches only its own ``person_data`` dict;
  * the module caches ``_wikipedia_images`` / ``_grokipedia_slugs`` in
    ``enrich_biographies`` are keyed per person and guarded there;
  * ``GrokClient`` already has a thread-safe ``_RateLimiter`` + ``_cache_lock``
    and shares a pooled ``requests.Session`` (urllib3 pool, maxsize 20 >> pool);
  * the Wikipedia courtesy delay is a shared ``_wikipedia_rate_limit`` lock, so
    total request rate stays <= the same politeness bound across threads.

Fail-safe: one person's fetch raising must NOT abort the others. Batch-collecting
mode is never parallelized (the request is queued, not answered).
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Conservative built-in default used when config is absent/invalid. Small and
# capped so politeness + account rate limits are preserved; tune via
# ``concurrency.max_enrichment_workers`` in config.yaml.
_DEFAULT_WORKERS = 4
# Hard cap regardless of config — a runaway value must never fan out unbounded.
_MAX_WORKERS_CAP = 16


def _resolve_worker_count(config: Optional[Dict[str, Any]]) -> int:
    """Resolve the bounded pool size from config, conservatively.

    ``concurrency.max_enrichment_workers`` (positive int, already validated by
    ``_validate_concurrency_section``). Falls back to the conservative default
    and is hard-capped.
    """
    conc = (config or {}).get("concurrency", {}) or {}
    workers = conc.get("max_enrichment_workers", _DEFAULT_WORKERS)
    try:
        workers = int(workers)
    except (TypeError, ValueError):
        workers = _DEFAULT_WORKERS
    if workers < 1:
        workers = 1
    return min(workers, _MAX_WORKERS_CAP)


def _parallel_enabled(config: Optional[Dict[str, Any]]) -> bool:
    """Parallel bio enrichment is opt-OUTable via ``concurrency.bio_enrichment_parallel``.

    Defaults to True (the fix is the intended behavior) but any explicit falsey
    config value restores the fully-serial path — a safe kill switch.
    """
    conc = (config or {}).get("concurrency", {}) or {}
    return bool(conc.get("bio_enrichment_parallel", True))


def should_parallelize(
    people: List[dict], grok_client: Any, config: Optional[Dict[str, Any]]
) -> bool:
    """Gate: only the REAL-TIME SYNC path with >1 person and workers>1 parallelizes.

    * batch-collecting mode is never parallelized (requests are queued, not answered);
    * a single person has nothing to parallelize;
    * the config kill switch / worker=1 forces the serial path.
    """
    if grok_client is None:
        return False
    if getattr(grok_client, "batch_mode", False):
        return False
    if not _parallel_enabled(config):
        return False
    if len([p for p in people if p]) < 2:
        return False
    return _resolve_worker_count(config) > 1


def _fetch_one(
    person: dict, grok_client: Any, enrich_fn: Any
) -> tuple[dict, bool, Optional[BaseException]]:
    """Run the pure, no-file-I/O network fetch for ONE person in a worker thread.

    Returns ``(person, enriched_now, error)``. Never raises: a failing person's
    error is captured and returned so it cannot abort sibling fetches.
    ``BatchModeCollecting`` is captured like any other error (defensive — the
    caller already excludes batch mode, so this should not occur).
    """
    try:
        enriched_now = bool(enrich_fn(person, grok_client))
        return person, enriched_now, None
    except (
        BaseException
    ) as exc:  # noqa: BLE001 - enrichment is additive; isolate failures
        return person, False, exc


def prefetch_enrichment(
    people: List[dict],
    grok_client: Any,
    config: Optional[Dict[str, Any]],
    enrich_fn: Any,
    bump: Any,
    observe: Any,
) -> None:
    """Concurrently run ``enrich_fn`` (the per-person network fetch) over ``people``.

    Mutates each person's OWN dict in place; does NO file I/O and NO index
    mutation — the caller persists serially afterwards. Counters/OBSERV are
    bumped HERE on the calling thread (the ``_enrich_counts`` ContextVar is not
    inherited by worker threads), mirroring the serial ``_enrich_person_phase2``
    accounting so the ``enrich.summary`` totals are identical to the serial path.

    Args:
        people: raw person dicts needing enrichment (same objects the caller persists).
        grok_client: shared Grok client (thread-safe rate limiter + pooled session).
        config: loaded config (for worker count).
        enrich_fn: ``enrich_person_from_sources`` (fetch only; caller owns save path).
        bump: ``_bump_enrich`` counter hook (called on this thread).
        observe: per-person OBSERV hook ``observe(person, enriched_now)`` (this thread).
    """
    targets = [p for p in people if p]
    if not targets:
        return
    workers = min(_resolve_worker_count(config), len(targets))
    logger.info(
        "Phase-2 bio enrichment: parallel fetch of %d people across %d workers",
        len(targets),
        workers,
    )

    with ThreadPoolExecutor(
        max_workers=workers, thread_name_prefix="bio-enrich"
    ) as pool:
        futures = {
            pool.submit(_fetch_one, person, grok_client, enrich_fn): person
            for person in targets
        }
        for fut in as_completed(futures):
            person, enriched_now, error = fut.result()
            bump("total")
            if error is not None:
                bump("errors")
                logger.warning(
                    "  Phase-2 enrichment skipped for %s: %s",
                    person.get("name", "?"),
                    error,
                )
                continue
            bump("enriched" if enriched_now else "skipped")
            observe(person, enriched_now)

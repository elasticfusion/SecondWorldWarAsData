"""OpenSERP-based enrichment for Phase 3 — images, academic sources, event content.

Searches for:
  - People: portraits, academic papers, oral histories, video interviews
  - Equipment: photos, technical drawings
  - Events: primary sources, veteran interviews, documentary footage (multi-language)

All results verified by Grok before acceptance.
Requires OpenSERP service running (ECS Fargate or localhost:7001).
"""

import json
import logging
import threading
import datetime as _dt
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests

from src.utils.http_pool import get_session
from src.utils.file_lock import write_json_with_lock

# Circuit breaker: skip all OpenSERP searches after N consecutive failures
_CIRCUIT_BREAKER_THRESHOLD = 5
_consecutive_failures = 0
_circuit_open = False
# H3: the breaker globals are mutated from the enrichment thread pool and persist
# across books in one process. Guard them with a lock and reset per run so an
# opened breaker from one book doesn't poison the next.
_circuit_lock = threading.Lock()


def reset_circuit() -> None:
    """Reset the OpenSERP circuit breaker (call at the start of an enrichment run
    so breaker state never leaks across books/runs in a long-lived process)."""
    global _consecutive_failures, _circuit_open
    with _circuit_lock:
        _consecutive_failures = 0
        _circuit_open = False


def _breaker_is_open() -> bool:
    with _circuit_lock:
        return _circuit_open


def _breaker_record_failure() -> bool:
    """Count a failure; open the breaker at threshold. Returns True if now open."""
    global _consecutive_failures, _circuit_open
    with _circuit_lock:
        _consecutive_failures += 1
        if _consecutive_failures >= _CIRCUIT_BREAKER_THRESHOLD:
            if not _circuit_open:
                logger.warning(
                    "OpenSERP circuit breaker OPEN — %d consecutive empty/failed "
                    "responses, skipping remaining searches",
                    _consecutive_failures,
                )
                _metric("breaker_opened")
            _circuit_open = True
        return _circuit_open


def _breaker_record_success() -> None:
    global _consecutive_failures
    with _circuit_lock:
        _consecutive_failures = 0


logger = logging.getLogger(__name__)


def _validate_before_write(data: Dict, entity: str) -> bool:
    """Validate a record via the CENTRAL guard (single source of truth) WITHOUT recording a
    stat — this is a pre-write check (the real write records its own stat), so recording here
    too would double-count. Returns True if the record may be written. Never raises."""
    try:
        from pathlib import Path as _P

        from src.utils.file_lock import _validate_entity

        return _validate_entity(
            _P(f"output/{entity}/_precheck.json"), data, record_stats=False
        )
    except Exception:  # noqa: BLE001 - guard unavailable -> don't block the write
        return True


# --- OpenSERP effectiveness/health metrics (per Phase-3 run) ---------------------------------
_metrics_lock = threading.Lock()
_METRIC_KEYS = (
    # entities_eligible is the expected-yield BASELINE: how many records were real
    # candidates for search (loadable, not recently-searched, with a usable name). Without
    # it, `entities_searched=1` collapsing from an eligible pool of 14 reads as "ok".
    "entities_eligible",
    "entities_searched",
    "entities_enriched",
    # entities_skipped_no_name counts the per-entity silent `continue` on missing/too-short
    # name (the 6/6 groups enrich_skip_no_name class) so a mass skip is no longer invisible.
    "entities_skipped_no_name",
    "queries_issued",
    "results_returned",
    "zero_result_queries",
    "verify_yes",
    "verify_no",
    "breaker_opened",
    "urls_fetched",
    "items_added",
)
_metrics: Dict[str, int] = {k: 0 for k in _METRIC_KEYS}


def _metric(key: str, n: int = 1) -> None:
    """Increment an OpenSERP metric (thread-safe)."""
    with _metrics_lock:
        _metrics[key] = _metrics.get(key, 0) + n


def _record_eligible() -> None:
    """Count a record that IS a candidate for search (passed the name/recency gate). This is the
    expected-yield baseline used by write_metrics to flag `searched << eligible` starvation.
    """
    _metric("entities_eligible")


def _record_skip_no_name() -> None:
    """Count a record skipped because it has no usable name (missing/too short). Fail-safe: a
    large skip fraction is surfaced as a WARN in write_metrics instead of a silent `continue`.
    """
    _metric("entities_skipped_no_name")


def _usable_entity_name(name: Any, min_len: int = 1) -> bool:
    """True if `name` is a usable search anchor. Counts the skip as a metric side effect when it
    is NOT usable, so every driver's name-gate feeds the aggregate skip counter. Never raises.
    """
    try:
        ok = bool(name) and len(str(name).strip()) >= min_len
    except Exception:  # noqa: BLE001 - treat any oddity as unusable, fail-safe
        ok = False
    if not ok:
        _record_skip_no_name()
    return ok


def reset_metrics() -> None:
    with _metrics_lock:
        for k in _METRIC_KEYS:
            _metrics[k] = 0


def get_metrics() -> Dict[str, Any]:
    """Snapshot of the metrics + derived rates (hit rate, verify pass rate)."""
    with _metrics_lock:
        m: Dict[str, Any] = dict(_metrics)
    searched = m["entities_searched"] or 1
    verified = (m["verify_yes"] + m["verify_no"]) or 1
    queries = m["queries_issued"] or 1
    eligible = m.get("entities_eligible", 0) or 1
    m["enrichment_rate"] = round(m["entities_enriched"] / searched, 3)
    m["verify_pass_rate"] = round(m["verify_yes"] / verified, 3)
    m["zero_result_rate"] = round(m["zero_result_queries"] / queries, 3)
    # search_rate = searched / eligible. A value far below 1.0 means most candidates were
    # never searched (the searched=1-of-14 starvation this baseline exists to catch).
    m["search_rate"] = round(m["entities_searched"] / eligible, 3)
    return m


def write_metrics(output_dir: Any) -> Dict[str, Any]:
    """Write the OpenSERP metrics snapshot to output/metrics/openserp_metrics.json. Returns the
    snapshot (also suitable for embedding in .phase_results.json). Fail-safe."""
    import json as _json
    from datetime import datetime, timezone

    snap = get_metrics()
    snap["generated_at"] = datetime.now(timezone.utc).isoformat()
    try:
        mdir = output_dir / "metrics"
        mdir.mkdir(parents=True, exist_ok=True)
        (mdir / "openserp_metrics.json").write_text(
            _json.dumps(snap, indent=2), encoding="utf-8"
        )
        logger.info(
            "OpenSERP metrics: searched=%d enriched=%d (%.0f%% hit) | queries=%d results=%d "
            "zero=%d | verify %d/%d (%.0f%% pass) | breaker_open=%d | items_added=%d",
            snap["entities_searched"],
            snap["entities_enriched"],
            snap["enrichment_rate"] * 100,
            snap["queries_issued"],
            snap["results_returned"],
            snap["zero_result_queries"],
            snap["verify_yes"],
            snap["verify_yes"] + snap["verify_no"],
            snap["verify_pass_rate"] * 100,
            snap["breaker_opened"],
            snap["items_added"],
        )
        # Canonical, greppable rollup + anomaly (the human "OpenSERP metrics" line above is not
        # OBSERV-keyed). Rates as int permille to keep the line int-only (no log-forging surface).
        _emit_openserp_summary(snap)
        _emit_openserp_anomalies(snap)
    except Exception as e:  # noqa: BLE001 - metrics are best-effort
        logger.debug("Could not write OpenSERP metrics: %s", e)
    return snap


def _emit_openserp_summary(snap: Dict[str, Any]) -> None:
    """Emit the canonical OBSERV openserp.summary rollup (now carrying the `eligible` baseline
    so `searched` can be read against the candidate pool, not in isolation). Never raises.
    """
    logger.info(
        "OBSERV openserp.summary eligible=%d searched=%d enriched=%d skipped_no_name=%d "
        "queries=%d results=%d zero=%d verify_yes=%d verify_no=%d breaker_opened=%d "
        "items_added=%d hit_permille=%d search_permille=%d",
        snap.get("entities_eligible", 0),
        snap["entities_searched"],
        snap["entities_enriched"],
        snap.get("entities_skipped_no_name", 0),
        snap["queries_issued"],
        snap["results_returned"],
        snap["zero_result_queries"],
        snap["verify_yes"],
        snap["verify_no"],
        snap["breaker_opened"],
        snap["items_added"],
        int(snap["enrichment_rate"] * 1000),
        int(snap.get("search_rate", 0) * 1000),
    )


# A run where fewer than this fraction of eligible candidates were actually searched is treated
# as starvation (e.g. searched=1 of eligible=14 -> 0.07). Independent of the breaker signal.
_SEARCH_STARVATION_RATIO = 0.5
# A run where this fraction (or more) of candidates were dropped for a missing/too-short name is
# surfaced as its own anomaly (the 6/6 groups enrich_skip_no_name class).
_SKIP_NO_NAME_RATIO = 0.5


def _emit_openserp_anomalies(snap: Dict[str, Any]) -> None:
    """Emit OBSERV openserp.anomaly WARNINGs. Every check is INDEPENDENT (no elif): a breaker-open
    run can ALSO be 0-enriched and/or starved, and masking one behind another is exactly how the
    searched=1-of-14 signal was lost. Never raises."""
    if snap["breaker_opened"]:
        logger.warning(
            "OBSERV openserp.anomaly circuit_breaker_opened=%d "
            "(consecutive empty/failed searches — OpenSERP degraded/unreachable)",
            snap["breaker_opened"],
        )
    # INDEPENDENT (was elif): breaker-open must no longer mask the 0-enriched starvation signal.
    if snap["entities_searched"] > 0 and snap["entities_enriched"] == 0:
        logger.warning(
            "OBSERV openserp.anomaly 0-of-%d entities enriched "
            "(service down, or every result rejected by verify?)",
            snap["entities_searched"],
        )
    eligible = snap.get("entities_eligible", 0)
    searched = snap["entities_searched"]
    if eligible > 0 and searched < eligible * _SEARCH_STARVATION_RATIO:
        logger.warning(
            "OBSERV openserp.anomaly searched=%d-of-eligible=%d "
            "(mass search starvation — candidates present but not searched)",
            searched,
            eligible,
        )
    skipped = snap.get("entities_skipped_no_name", 0)
    considered = skipped + eligible
    if considered > 0 and skipped >= considered * _SKIP_NO_NAME_RATIO:
        logger.warning(
            "OBSERV openserp.anomaly skipped_no_name=%d-of-%d "
            "(large fraction of records dropped for missing/too-short name)",
            skipped,
            considered,
        )


def _render_queries(query_file: str, category: str, **kwargs) -> List[str]:
    """Render search-query templates and squeeze whitespace, so an empty {facts} (or any empty
    variable) collapses cleanly instead of leaving double spaces / a dangling literal.
    """
    import re as _re

    from src.utils.search_query_loader import render_search_queries

    out = []
    for q in render_search_queries(query_file, category, **kwargs):
        q = _re.sub(r"\{[a-z_]+\}", "", q)  # drop any unfilled placeholder
        out.append(_re.sub(r"\s+", " ", q).strip())
    return out


SKIP_FILES = {
    "index.json",
    "duplicate_report.json",
    "not_duplicates.json",
    "not_related.json",
    ".processed_events.json",
}


def _openserp_reachable(openserp_url: str) -> bool:
    """Check if OpenSERP is reachable with a FAST, single-shot probe.

    Uses a plain requests.get (NOT the shared session, which carries a urllib3 Retry adapter)
    so an unreachable OpenSERP fails in one short attempt instead of 3 retries + backoff per
    call — important because this is probed once per entity step (local/dev runs with OpenSERP
    down otherwise spent ~30s retrying)."""
    try:
        resp = requests.get(f"{openserp_url}/health", timeout=3)
        return resp.status_code == 200
    except Exception:
        return False


def _drop_excluded_domains(results: List[Dict]) -> List[Dict]:
    """Remove results whose host is on the persistent site-level denylist (operator/Grok exclusions).
    Fail-safe: if the registry is unavailable, return results unchanged (never block a search).
    """
    try:
        from src.utils.url_registry import is_domain_excluded
    except Exception:  # noqa: BLE001
        return results
    kept: List[Dict] = []
    dropped = 0
    for r in results:
        url = r.get("url", "")
        try:
            if url and is_domain_excluded(url):
                dropped += 1
                continue
        except (
            Exception
        ):  # noqa: BLE001 - a filter error must never drop the whole result set
            pass
        kept.append(r)
    if dropped:
        _metric("results_site_excluded", dropped)
        logger.info(
            "OBSERV openserp.site_excluded dropped=%d kept=%d", dropped, len(kept)
        )
    return kept


def _search_openserp(query: str, openserp_url: str, limit: int = 5) -> List[Dict]:
    """Run an OpenSERP search. Returns list of {url, title, description}."""
    if _breaker_is_open():
        logger.info("OpenSERP circuit breaker SKIP: %s", query[:60])
        return []

    _metric("queries_issued")
    try:
        import time

        from src.utils.config import load_config

        cfg = load_config().get("openserp", {})
        time.sleep(cfg.get("rate_limit_seconds", 5))
        # Browser-mode OpenSERP (headless Chromium across several engines) can be slow;
        # the per-request timeout is config-driven so it can absorb browser latency
        # without tripping the breaker on a slow-but-valid query.
        req_timeout = cfg.get("request_timeout_seconds", 60)
        from src.utils.fetch_provenance import TEXTUAL, recorded_fetch

        resp = recorded_fetch(
            "openserp",
            f"{openserp_url}/mega/search",
            kind=TEXTUAL,
            method="GET",
            params={
                "text": query,
                "limit": str(limit),
                "mode": "any",
            },
            timeout=req_timeout,
            result_summary_fn=lambda r: f"status={r.status_code} q={query[:40]!r}",
        )
        if resp.status_code == 200:
            data = resp.json()
            if not data:
                _breaker_record_failure()
                _metric("zero_result_queries")
                return []
            # Handle both flat list and {"results": [...]} formats
            results = data if isinstance(data, list) else data.get("results", [])
            if results:
                _breaker_record_success()
            else:
                _breaker_record_failure()
                _metric("zero_result_queries")
                return []
            logger.info("OpenSERP [%s]: %d results", query[:60], len(results))
            _metric("results_returned", len(results))
            mapped = [
                {
                    "url": r.get("url", ""),
                    "title": r.get("title", ""),
                    "description": r.get("snippet", ""),
                }
                for r in results
                if r
            ]
            return _drop_excluded_domains(mapped)
        logger.warning("OpenSERP [%s]: HTTP %d", query[:60], resp.status_code)
        _breaker_record_failure()
    except Exception as e:
        logger.warning("OpenSERP [%s]: %s", query[:60], e)
        _breaker_record_failure()
    return []


def _verify_result(
    candidate_title: str,
    expected_context: str,
    grok_client: Any,
    snippet: str = "",
    url: str = "",
) -> bool:
    """Use Grok to verify a search result is relevant (cached, batch-friendly).

    FAIL-CLOSED: with no grok_client, or on any Grok error, return False (reject) rather than
    accepting an unverified result. The judgment uses the title, the result snippet, AND the
    URL (not the title alone) for a stronger signal."""
    if not grok_client:
        # No verifier available -> cannot confirm relevance -> reject (fail-closed).
        return False
    from src.utils.search_cache import cache_result, get_cached

    cache_key = f"{expected_context[:50]}|{candidate_title[:50]}|{url[:60]}"
    cached = get_cached("openserp_verify", cache_key)
    if cached == "YES":
        return True
    if cached == "NO" or cached == "NOT_FOUND":
        return False

    try:
        response = grok_client.chat_completion(
            prompt=(
                "Is this search result relevant to the context?\n"
                f'Context: "{expected_context[:200]}"\n'
                f'Result title: "{candidate_title[:200]}"\n'
                f'Result snippet: "{snippet[:300]}"\n'
                f"Result URL: {url[:200]}\n"
                'Return ONLY "YES" or "NO".'
            ),
            system_prompt="You verify search result relevance.",
            temperature=0.0,
            use_cache=True,
            cache_type="openserp_verify",
        )
        answer = "YES" if response.strip().upper().startswith("YES") else "NO"
        cache_result("openserp_verify", cache_key, answer)
        _metric("verify_yes" if answer == "YES" else "verify_no")
        logger.info(
            "Grok verify [%s]: %s — '%s'",
            answer,
            expected_context[:40],
            candidate_title[:50],
        )
        return answer == "YES"
    except Exception:
        # Verifier errored -> cannot confirm -> reject (fail-closed). NOT cached, so a later
        # run with a healthy Grok can still verify it.
        logger.warning(
            "Grok verify errored (fail-closed reject): %s", candidate_title[:50]
        )
        return False


def _prefilter_verify_cache(
    results: "List[Dict[str, Any]]",
    expected_context: str,
    title_key: str,
    url_key: str,
    verdicts: "List[bool]",
) -> "tuple[List[str], List[int]]":
    """Compute per-result cache keys (EXACTLY as _verify_result) and consult the cache. Fills
    ``verdicts`` for already-decided results and returns (cache_keys, indices_still_needing_Grok).
    """
    from src.utils.search_cache import get_cached

    cache_keys: List[str] = []
    to_verify: List[int] = []
    for i, r in enumerate(results):
        title = str(r.get(title_key, "") or "")
        url = str(r.get(url_key, "") or "")
        cache_key = f"{expected_context[:50]}|{title[:50]}|{url[:60]}"
        cache_keys.append(cache_key)
        cached = get_cached("openserp_verify", cache_key)
        if cached == "YES":
            verdicts[i] = True
        elif cached in ("NO", "NOT_FOUND"):
            verdicts[i] = False
        else:
            to_verify.append(i)
    return cache_keys, to_verify


def _build_batch_verify_prompt(
    results: "List[Dict[str, Any]]",
    to_verify: "List[int]",
    expected_context: str,
    title_key: str,
    snippet_key: str,
    url_key: str,
) -> str:
    """Build the single prompt enumerating the uncached results; expects a YES/NO array back."""
    lines = []
    for n, idx in enumerate(to_verify):
        r = results[idx]
        title = str(r.get(title_key, "") or "")
        snippet = str(r.get(snippet_key, "") or "")
        url = str(r.get(url_key, "") or "")
        lines.append(
            f'[{n}] title: "{title[:200]}" | snippet: "{snippet[:300]}" | url: {url[:200]}'
        )
    return (
        "For EACH numbered search result below, decide if it is relevant to the context.\n"
        f'Context: "{expected_context[:200]}"\n\n'
        "Results:\n" + "\n".join(lines) + "\n\n"
        f'Return ONLY a JSON array of exactly {len(to_verify)} strings, each "YES" or "NO", '
        "in the same order as the numbered results (index 0 first). "
        'Example for 2 results: ["YES", "NO"]'
    )


def _verify_results_batch(
    expected_context: str,
    results: List[Dict[str, Any]],
    grok_client: Any,
    snippet_key: str = "description",
    url_key: str = "url",
    title_key: str = "title",
) -> List[bool]:
    """Prompt-level batched verifier (design doc §10/§10A): verify ALL ``results`` in ONE Grok
    call that returns a per-result YES/NO array, instead of one Grok call per result.

    Returns a list of bools aligned 1:1 with ``results`` (index i -> result i relevant?).

    Behaviour mirrors _verify_result exactly, just amortised across a call:
      * FAIL-CLOSED: no grok_client -> all False; any missing/malformed/short array index -> that
        index is False (reject); a Grok error -> uncached indices are False (and NOT cached, so a
        healthy later run can re-verify).
      * CACHE PRESERVED: each result is keyed EXACTLY as _verify_result
        (``f"{expected_context[:50]}|{title[:50]}|{url[:60]}"``). Already-cached results are
        pre-filtered out of the batch (so repeat runs issue no Grok call); per-result cache
        entries are written from the batched response (90-day positive / negative TTL via
        cache_result).
      * METRICS PRESERVED: _metric("verify_yes"/"verify_no") is incremented per NEWLY-verified
        result exactly as _verify_result does (cache hits do not re-increment, matching the
        single-item path).

    _verify_result remains the single-item path; this is purely an additive batched variant and
    is NOT yet wired into the batch drivers."""
    verdicts: List[bool] = [False] * len(results)
    if not results:
        return verdicts
    if not grok_client:
        # No verifier available -> cannot confirm any -> reject all (fail-closed).
        return verdicts

    from src.utils.search_cache import cache_result, get_cached

    # Pre-compute per-result cache keys (EXACTLY as _verify_result) and consult the cache so
    # already-decided results stay free (no Grok call) on repeat runs.
    cache_keys, to_verify = _prefilter_verify_cache(
        results, expected_context, title_key, url_key, verdicts
    )

    if not to_verify:
        return verdicts  # fully served from cache — no Grok call

    # Build ONE prompt enumerating the uncached results; expect a YES/NO per line/index.
    prompt = _build_batch_verify_prompt(
        results, to_verify, expected_context, title_key, snippet_key, url_key
    )

    try:
        response = grok_client.chat_completion(
            prompt=prompt,
            system_prompt="You verify search result relevance. Reply with a JSON YES/NO array.",
            temperature=0.0,
            use_cache=True,
            cache_type="openserp_verify_batch",
        )
        answers = _parse_batch_verdicts(response)
    except Exception:
        # Verifier errored -> cannot confirm -> reject uncached (fail-closed). NOT cached, so a
        # later run with a healthy Grok can still verify them.
        logger.warning(
            "Grok batch verify errored (fail-closed reject %d results): %s",
            len(to_verify),
            expected_context[:40],
        )
        return verdicts

    for n, idx in enumerate(to_verify):
        # Fail-closed on a missing/short/malformed index.
        answer = answers[n] if n < len(answers) else "NO"
        verdicts[idx] = answer == "YES"
        cache_result("openserp_verify", cache_keys[idx], answer)
        _metric("verify_yes" if answer == "YES" else "verify_no")
    logger.info(
        "Grok batch verify: %d results for '%s' -> %d YES",
        len(to_verify),
        expected_context[:40],
        sum(1 for n in range(len(to_verify)) if verdicts[to_verify[n]]),
    )
    return verdicts


def _parse_batch_verdicts(response: Any) -> List[str]:
    """Parse a batched-verify Grok response into a list of "YES"/"NO" strings (fail-closed).

    ONLY a well-formed JSON array of verdicts is accepted; any entry that is not an unambiguous
    YES becomes "NO". Any non-JSON/malformed response returns [] (so the caller treats EVERY
    result as NO). We deliberately do not token-scan prose — out-of-order YES/NO tokens could
    misalign verdicts to results, and a verifier must never accept on ambiguous output.
    """
    if not response:
        return []
    text = str(response).strip()
    # ONLY accept a well-formed JSON array of verdicts. Anything else is fail-closed:
    # return [] so the caller treats every result as NO (reject). We deliberately do NOT
    # token-scan prose for YES/NO — out-of-order tokens could misalign verdicts to results,
    # and a verifier must never accept on ambiguous output.
    try:
        import re as _re

        m = _re.search(r"\[.*\]", text, _re.DOTALL)
        if m:
            arr = json.loads(m.group(0))
            if isinstance(arr, list):
                return [
                    "YES" if str(x).strip().upper().startswith("YES") else "NO"
                    for x in arr
                ]
    except Exception:  # noqa: BLE001 - malformed -> fail-closed ([] => all NO)
        pass
    return []


# --- Positive-URL page processing (fetch -> summarize -> 90-day retention) ---------------

_URL_FETCH_RETENTION_DAYS = (
    90  # matches DEFAULT_RECHECK_SECONDS / openserp_searched gate
)

# Sentinel returned by _summarize_url_page when the central registry says SKIP (already
# searched -> reuse) or DEFERRED (retry later). The caller must NOT cache REJECT for these
# (unlike a genuine fetch-miss which returns None): REJECT would poison cross-entity
# positive reuse / permanently deny a legitimate deferred retry.
_SKIP_SENTINEL = "\x00__SKIP__\x00"


def _grok_summarize_html(
    html: str,
    url: str,
    context: str,
    grok_client: Any,
    result: Any,
    provenance: Optional[Dict[str, Any]],
) -> Optional[str]:
    """Strip HTML to text and Grok-summarize it (1-2 sentences, cached). Surfaces Wayback archive
    provenance from the central FetchResult into ``provenance`` when the content was archived.
    """
    import re as _re
    from src.utils import url_fetch

    text = _re.sub(r"<[^>]+>", " ", html)
    text = _re.sub(r"\s+", " ", text).strip()[:6000]
    if not text:
        return None
    resp = grok_client.chat_completion(
        prompt=(
            f"Summarize this web page in 1-2 sentences, focused on its relevance "
            f'to: "{context[:150]}".\n\nURL: {url}\n\nContent:\n{text}'
        ),
        system_prompt="You summarize web pages concisely.",
        temperature=0.1,
        use_cache=True,
        cache_type="openserp_url_summary",
    )
    summary = (resp or "").strip() or None
    if result.source == url_fetch.WAYBACK and provenance is not None:
        provenance["archived_url"] = result.archived_url or ""
        provenance["wayback_capture_timestamp"] = result.wayback_ts or ""
    return summary


def _summarize_url_page(
    url: str,
    context: str,
    grok_client: Any,
    provenance: Optional[Dict[str, Any]] = None,
) -> Optional[str]:
    """Fetch a discovered page and return a short Grok summary of its content, or None.

    90-day retention: a URL already processed within the window returns its cached summary and
    is NOT re-fetched (handles both a prior hit and a prior empty result). Requires a
    grok_client (fail-closed: no client -> no summary). Fail-safe: fetch/summarize errors
    return None without raising.

    If ``provenance`` is supplied and the content had to be recovered from the Internet
    Archive Wayback Machine (live page WAF-blocked/dead), it is populated with
    ``archived_url`` + ``wayback_capture_timestamp`` so the caller can record the
    archive retrieval alongside the original URL in the bibliographical record."""
    if not grok_client or not url:
        return None
    # Route through the CENTRAL fetch (url_fetch): go/no-go on the shared DynamoDB
    # registry (cross-phase dedup — a URL Phase 2 already fetched is skipped here),
    # SSRF-safe per-hop redirects + DNS-rebinding, per-domain politeness, and
    # live->Wayback fallback. Replaces the ad-hoc _fetch_url_content + bespoke
    # search_cache (URL_SOURCED_ENRICHMENT_DESIGN.md).
    from src.utils import url_fetch, url_registry

    result = url_fetch.fetch(url)
    if result.source in (url_fetch.SKIP, url_fetch.DEFERRED):
        # SKIP = already searched (reuse the shared verdict); DEFERRED = in-progress
        # elsewhere / domain cooldown (retry later). Neither is a negative — signal the
        # caller NOT to cache REJECT (that would poison cross-entity positive reuse or
        # permanently deny a legitimate deferred retry).
        return _SKIP_SENTINEL
    if result.source in (
        url_fetch.DEAD,
        url_fetch.BLOCKED_NO_ARCHIVE,
        url_fetch.RATE_LIMITED,
    ):
        # gone / walled-with-no-archive / rate-limited -> no content this run.
        return None
    html = result.content
    summary: Optional[str] = None
    try:
        if html:
            summary = _grok_summarize_html(
                html, url, context, grok_client, result, provenance
            )
    except Exception as e:  # noqa: BLE001 - best-effort; never block enrichment
        logger.debug("URL summarize failed for %s: %s", url, e)

    # Finalize the registry entry AFTER summarizing (finalize-after-write #2): searched,
    # 90-day TTL, with the HTTP status for future filter decisions. relevant = we got a
    # usable summary.
    try:
        url_registry.finalize_searched(
            result.final_url or url,
            relevant=bool(summary),
            context=context[:200],
            source="openserp",
            first_wayback_ts=(result.wayback_ts or ""),
            http_status=result.http_status,
        )
    except Exception:  # noqa: BLE001
        pass
    return summary


def _url_verdict_cached(url: str) -> Optional[str]:
    """Return a cached URL verdict: 'REJECT' (negative — skip), a summary string (positive), or
    None (never processed). Negative + positive URLs are both cached (90-day) to prevent
    reprocessing on future runs."""
    if not url:
        return None
    try:
        from src.utils.search_cache import _get_backend, _make_key

        return _get_backend().get(_make_key("openserp_url_verdict", url))
    except Exception:  # noqa: BLE001
        return None


def _cache_url_verdict(url: str, verdict: str) -> None:
    """Cache a URL verdict ('REJECT' or a summary) for the retention window."""
    if not url:
        return
    try:
        from src.utils.search_cache import _get_backend, _make_key

        _get_backend().put(
            _make_key("openserp_url_verdict", url),
            verdict,
            ttl_days=_URL_FETCH_RETENTION_DAYS,
        )
    except Exception:  # noqa: BLE001
        pass


def _wayback_ts_to_iso(ts: str) -> str:
    """Convert a Wayback 14-digit capture timestamp (YYYYMMDDhhmmss) to an ISO-8601
    UTC datetime for the bibliographical record. Returns "" if unparseable."""
    if not ts or len(ts) < 8:
        return ""
    try:
        return (
            _dt.datetime.strptime(ts[:14].ljust(14, "0"), "%Y%m%d%H%M%S")
            .replace(tzinfo=_dt.timezone.utc)
            .isoformat()
        )
    except ValueError:
        return ""


def process_positive_url(
    url: str,
    title: str,
    snippet: str,
    context: str,
    grok_client: Any,
) -> Optional[Dict[str, Any]]:
    """Verify + process one discovered URL into an enriched result, with positive AND negative
    URL caching (both cached 90 days to prevent reprocessing):

      * previously REJECTED or dead URL -> return None immediately (no re-verify/re-fetch);
      * previously processed positive    -> rebuild the result from the cached summary;
      * new URL -> Grok-verify (fail-closed); reject -> cache 'REJECT' + None; accept -> fetch
        + summarize the page, cache the summary, return {url,title,summary,fetched_at,source}.
    """
    import time as _time

    if not url:
        return None
    cached = _url_verdict_cached(url)
    if cached == "REJECT":
        return None
    if cached:  # positive summary cached
        return {
            "url": url,
            "title": title,
            "summary": cached,
            "fetched_at": int(_time.time()),
            "source": "openserp",
        }

    if not _verify_result(title, context, grok_client, snippet=snippet, url=url):
        _cache_url_verdict(url, "REJECT")
        return None

    prov: Dict[str, Any] = {}
    summary = _summarize_url_page(url, context, grok_client, provenance=prov)
    if summary == _SKIP_SENTINEL:
        # Already searched (reuse) or deferred (retry later) — NOT a negative. Do not
        # cache REJECT; a prior/positive summary (if any) is served from the shared
        # url-keyed verdict cache above, or a later run resolves the deferral.
        return None
    if not summary:
        # Verified-relevant but the page could not be fetched/summarized: still a negative for
        # reprocessing purposes -> cache REJECT so we don't retry the fetch for 90 days.
        _cache_url_verdict(url, "REJECT")
        return None
    _metric("urls_fetched")  # a real page fetch+summarize (cache hits return earlier)
    _cache_url_verdict(url, summary)
    result: Dict[str, Any] = {
        "url": url,  # ALWAYS the original source URL (citation anchor)
        "title": title,
        "summary": summary,
        "fetched_at": int(_time.time()),
        "source": "openserp",
    }
    # If the live page was WAF-blocked/dead and content came from the Internet Archive,
    # record BOTH the original URL (above) and the archive retrieval for the citation:
    # the archived URL, the archive's capture date, and when WE retrieved it.
    if prov.get("archived_url"):
        result["retrieved_from"] = "wayback"
        result["archived_url"] = prov["archived_url"]
        result["wayback_capture_timestamp"] = prov.get("wayback_capture_timestamp", "")
        result["archive_capture_date"] = _wayback_ts_to_iso(
            prov.get("wayback_capture_timestamp", "")
        )
        result["retrieved_at"] = _dt.datetime.now(_dt.timezone.utc).isoformat()
    return result


# --- Image Search ---


def _name_last_and_initial(person_name: str) -> "tuple[str, str]":
    """Extract (last_name, first_initial) — both lowercased — from a person name.

    last_name = last token longer than 2 chars that is not an abbreviation ("J."); first_initial
    = first letter of the first capitalized token. Either may be "" when absent."""
    parts = person_name.split()
    name_parts = [p for p in parts if len(p) > 2 and not p.endswith(".")]
    last_name = name_parts[-1].lower() if name_parts else ""
    first_initial = ""
    for p in parts:
        if p and p[0].isupper():
            first_initial = p[0].lower()
            break
    return last_name, first_initial


def _name_initial_matches(person_name: str, result_title: str) -> bool:
    """Pre-filter: check if result title could be about this person (first initial + last name)."""
    last_name, first_initial = _name_last_and_initial(person_name)
    if not last_name:
        return True  # Can't filter, allow through
    title_lower = result_title.lower()
    # Last name must appear in title
    if last_name not in title_lower:
        return False
    # If we have a first initial, check that some word in title starts with it
    if first_initial:
        title_words = title_lower.split()
        if not any(w.startswith(first_initial) for w in title_words):
            return False
    return True


def search_person_images(
    person_name: str,
    openserp_url: str,
    grok_client: Any = None,
    max_results: int = 3,
    facts: str = "",
) -> List[Dict[str, str]]:
    """Search for portrait images of a person (query sharpened with the person's own facts)."""
    queries = _render_queries(
        "people", "portrait_images", name=person_name, facts=facts
    )
    images = []
    for query in queries:
        results = _search_openserp(query, openserp_url)
        for r in results:
            url = r.get("url", "")
            title = r.get("title", "")
            if not url:
                continue
            # Pre-filter: skip results that clearly aren't about this person
            if not _name_initial_matches(person_name, title):
                continue
            if _verify_result(
                title,
                f"Photo of {person_name}",
                grok_client,
                snippet=r.get("description", ""),
                url=url,
            ):
                images.append({"url": url, "title": title, "source": "openserp"})
                if len(images) >= max_results:
                    return images
    return images


def search_equipment_images(
    equipment_name: str,
    openserp_url: str,
    grok_client: Any = None,
    max_results: int = 3,
    identifier: str = "",
    year: str = "",
    facts: str = "",
) -> List[Dict[str, str]]:
    """Search for photos of military equipment (uses search_queries/equipment.yaml,
    sharpened with the equipment's own facts: category + country_of_origin)."""
    queries = _render_queries(
        "equipment",
        "images",
        name=equipment_name,
        identifier=identifier or equipment_name,
        year=year,
        facts=facts,
    )
    images: List[Dict[str, str]] = []
    seen: set = set()
    for query in queries:
        for r in _search_openserp(query, openserp_url):
            url = r.get("url", "")
            title = r.get("title", "")
            if not url or url in seen:
                continue
            if _verify_result(
                title,
                f"Photo of {equipment_name}",
                grok_client,
                snippet=r.get("description", ""),
                url=url,
            ):
                images.append({"url": url, "title": title, "source": "openserp"})
                seen.add(url)
                if len(images) >= max_results:
                    return images
    return images


# --- Academic/Media Search ---


def search_academic_sources(
    person_name: str,
    openserp_url: str,
    grok_client: Any = None,
    max_results: int = 5,
    facts: str = "",
) -> List[Dict[str, str]]:
    """Search for academic papers, oral histories, and media about a person."""
    queries = _render_queries(
        "people", "academic_sources", name=person_name, facts=facts
    )
    sources = []
    seen_urls = set()
    for query in queries:
        results = _search_openserp(query, openserp_url)
        for r in results:
            url = r.get("url", "")
            title = r.get("title", "")
            if url and url not in seen_urls:
                # Pre-filter: skip results that clearly aren't about this person
                if not _name_initial_matches(person_name, title):
                    continue
                if _verify_result(
                    title, f"Academic/media about {person_name}", grok_client
                ):
                    sources.append(
                        {
                            "url": url,
                            "title": title,
                            "type": _classify_source(url, title),
                            "source": "openserp",
                        }
                    )
                    seen_urls.add(url)
                    if len(sources) >= max_results:
                        return sources
    return sources


def _is_oral_history_source(url_lower: str, title_lower: str) -> bool:
    return "oral history" in title_lower or "interview" in title_lower


def _is_video_source(url_lower: str, title_lower: str) -> bool:
    return (
        "youtube.com" in url_lower
        or "video" in title_lower
        or "documentary" in title_lower
    )


def _is_academic_source(url_lower: str, title_lower: str) -> bool:
    return (
        ".edu" in url_lower or "university" in title_lower or "journal" in title_lower
    )


def _is_archive_source(url_lower: str, title_lower: str) -> bool:
    return "archive" in url_lower or "museum" in url_lower


def _is_military_award_source(url_lower: str, title_lower: str) -> bool:
    return any(
        s in url_lower for s in ("valor.militarytimes", "homeofheroes", "cmohs.org")
    )


# Ordered (predicate -> label) rules; first match wins, mirroring the original if-chain.
_SOURCE_CLASSIFIERS = (
    (_is_oral_history_source, "oral_history"),
    (_is_video_source, "video"),
    (_is_academic_source, "academic"),
    (_is_archive_source, "archive"),
    (_is_military_award_source, "military_award"),
)


def _classify_source(url: str, title: str) -> str:
    """Classify a source by URL/title patterns."""
    url_lower = url.lower()
    title_lower = title.lower()
    for predicate, label in _SOURCE_CLASSIFIERS:
        if predicate(url_lower, title_lower):
            return label
    return "other"


_AWARD_SITES = {
    "valor.militarytimes.com",
    "homeofheroes.com",
    "themedalofhonor.com",
    "cmohs.org",
    "militaryhallofhonor.com",
}


def _cached_awards(person_name: str) -> "Optional[List[Dict[str, str]]]":
    """Return cached awards list for a person, [] for a cached NOT_FOUND, or None if uncached."""
    from src.utils.search_cache import get_cached

    cached = get_cached("openserp_awards", person_name)
    if cached == "NOT_FOUND":
        return []
    if cached:
        import json as _json

        return _json.loads(cached)
    return None


def _store_awards(person_name: str, awards: "List[Dict[str, str]]") -> None:
    """Persist (or negative-cache) the awards result for a person."""
    from src.utils.search_cache import cache_result

    if awards:
        import json as _json

        cache_result("openserp_awards", person_name, _json.dumps(awards))
    else:
        cache_result("openserp_awards", person_name, None)


def _collect_award_results(
    person_name: str, queries: "List[str]", openserp_url: str, grok_client: Any
) -> "List[Dict[str, str]]":
    """Run award queries, name-gate + Grok-verify hits, and collect up to 5 awards."""
    awards: List[Dict[str, str]] = []
    seen: set = set()
    for query in queries:
        for r in _search_openserp(query, openserp_url):
            url = r.get("url", "")
            title = r.get("title", "")
            if not url or url in seen:
                continue
            # Pre-filter: skip results that clearly aren't about this person
            if not _name_initial_matches(person_name, title):
                continue
            if _verify_result(
                title, f"Military service of {person_name} in WWII", grok_client
            ):
                awards.append({"url": url, "title": title, "source": "openserp"})
                seen.add(url)
                if len(awards) >= 5:
                    break
        if len(awards) >= 5:
            break
    return awards


def search_military_awards(
    person_name: str,
    openserp_url: str,
    grok_client: Any = None,
    facts: str = "",
) -> List[Dict[str, str]]:
    """Search the web for military award citations and biographical data."""
    cached = _cached_awards(person_name)
    if cached is not None:
        return cached

    queries = _render_queries("people", "web_results", name=person_name, facts=facts)
    awards = _collect_award_results(person_name, queries, openserp_url, grok_client)
    _store_awards(person_name, awards)
    return awards


def search_event_content(
    event_name: str,
    aliases: Optional[List[str]] = None,
    openserp_url: str = "http://localhost:7001",
    grok_client: Any = None,
    max_results: int = 5,
) -> List[Dict[str, str]]:
    """Search for primary sources related to an event, including non-English."""
    from src.utils.search_query_loader import render_search_queries as _rsq2

    queries = _rsq2("events", "primary_sources", event_name=event_name)

    # Add non-English queries for major events
    if aliases:
        for alias in aliases[:2]:
            queries.append(f'"{alias}" témoignage')  # French: testimony
            queries.append(f'"{alias}" Zeitzeuge')  # German: eyewitness

    sources = []
    seen_urls = set()
    for query in queries:
        results = _search_openserp(query, openserp_url)
        for r in results:
            url = r.get("url", "")
            title = r.get("title", "")
            if url and url not in seen_urls:
                processed = process_positive_url(
                    url,
                    title,
                    r.get("description", ""),
                    f"Content about {event_name}",
                    grok_client,
                )
                if processed:
                    processed["type"] = _classify_source(url, title)
                    sources.append(processed)
                    seen_urls.add(url)
                    if len(sources) >= max_results:
                        return sources
    return sources


# --- Batch Enrichment ---


def _verify_and_apply(
    candidate: Dict,
    data: Dict,
    name: str,
    grok_client: Any,
    max_images: int,
    max_web: int,
) -> bool:
    """Verify OpenSERP results with Grok and apply to entity data."""
    img_changed = _apply_verified_images(candidate, data, name, grok_client, max_images)
    web_changed = _apply_verified_web(candidate, data, name, grok_client, max_web)
    return img_changed or web_changed


def _apply_verified_images(
    candidate: Dict, data: Dict, name: str, grok_client: Any, max_images: int
) -> bool:
    """Verify each image result (name-initial gate + Grok) and append up to max_images."""
    existing_image_urls = {
        i.get("url") for i in (data.get("images") or []) if isinstance(i, dict)
    }
    changed = False
    for r in candidate.get("image_results", []):
        url = r.get("url", "")
        title = r.get("title", "")
        if (
            not url
            or url in existing_image_urls
            or not _name_initial_matches(name, title)
        ):
            continue
        if _verify_result(title, f"Photo of {name} WWII", grok_client):
            data.setdefault("images", []).append(
                {"url": url, "title": title, "source": "openserp"}
            )
            existing_image_urls.add(url)
            _metric("items_added")
            changed = True
            if len(data.get("images", [])) >= max_images:
                break
    return changed


def _apply_verified_web(
    candidate: Dict, data: Dict, name: str, grok_client: Any, max_web: int
) -> bool:
    """Fetch + summarize each web result (name-initial gate) and append up to max_web awards."""
    existing_award_urls = {
        a.get("url") for a in (data.get("military_awards") or []) if isinstance(a, dict)
    }
    changed = False
    for r in candidate.get("web_results", []):
        url = r.get("url", "")
        title = r.get("title", "")
        if (
            not url
            or url in existing_award_urls
            or not _name_initial_matches(name, title)
        ):
            continue
        processed = process_positive_url(
            url,
            title,
            r.get("description", ""),
            f"Military service of {name} in WWII",
            grok_client,
        )
        if processed:
            data.setdefault("military_awards", []).append(processed)
            existing_award_urls.add(url)
            _metric("items_added")
            changed = True
            if len(data.get("military_awards", [])) >= max_web:
                break
    return changed


# Non-award sites OpenSERP should also skip (low-value / excluded by the owner).
_EXTRA_SKIP_HOSTS = frozenset({"ibiblio.org", "www.ibiblio.org"})


def _skip_result(url: str) -> bool:
    """True if an OpenSERP result URL should be dropped: award-source domains (sourced
    authoritatively by the award adapters) or explicitly excluded hosts (ibiblio)."""
    if not url:
        return False
    from urllib.parse import urlparse

    from src.enrichment.award_registry import is_award_domain

    host = (urlparse(url).netloc or "").lower()
    if host in _EXTRA_SKIP_HOSTS or host.removeprefix("www.") in _EXTRA_SKIP_HOSTS:
        return True
    return is_award_domain(url)


def _primary_unit_designation(units: Any) -> str:
    """First unit designation from a units_served list (designation or unit), "" if none."""
    for u in units or []:
        if isinstance(u, dict):
            desig = u.get("designation") or u.get("unit")
            if desig:
                return str(desig)
    return ""


def _person_query_terms(data: Dict) -> str:
    """Build extra query terms from People-JSON facts to sharpen the OpenSERP search:
    primary unit designation + rank + nationality. Keeps the query specific without flooding
    it (one unit, one rank, one nationality)."""
    bp = data.get("biographical_profile") or {}
    terms: List[str] = []
    desig = _primary_unit_designation(bp.get("units_served"))
    if desig:
        terms.append(desig)
    rank = bp.get("rank") or data.get("rank")
    if rank:
        terms.append(str(rank))
    nat = bp.get("nationality") or data.get("nationality")
    if nat:
        terms.append(str(nat))
    return " ".join(terms)


def _equipment_query_terms(data: Dict) -> str:
    """Build extra query terms from Equipment-JSON facts to sharpen the OpenSERP search:
    category + country_of_origin (the technical_identifier is already a template variable).
    Keeps it specific without flooding the query."""
    terms: List[str] = []
    cat = data.get("category")
    if cat:
        terms.append(str(cat))
    origin = data.get("country_of_origin")
    if origin:
        terms.append(str(origin))
    return " ".join(terms)


def _collect_person_candidates(f, name: str, data: Dict, openserp_url: str) -> Dict:
    """Build the OpenSERP candidate bundle for one person: a portrait-image search
    (skipped if a Wikipedia portrait was already captured in Phase 2) + a web search
    (awards/bio/academic), both augmented with People-JSON facts and filtered of
    award/ibiblio domains."""
    person_candidates: Dict = {"file": f, "name": name, "data": data}
    facts = _person_query_terms(data)

    # Phase 2 already captures the Wikipedia portrait (people enrichment); do NOT re-fetch
    # Wikipedia here (Phase 3 is OpenSERP-only). Only run the OpenSERP image search when the
    # record has no image yet. Queries come from people.yaml (facts-sharpened), unifying this
    # path with search_person_images rather than a hardcoded query string.
    if not data.get("images"):
        hits: List[Dict[str, str]] = []
        for q in _render_queries("people", "portrait_images", name=name, facts=facts):
            hits.extend(_search_openserp(q, openserp_url))
        person_candidates["image_results"] = [
            h for h in hits if not _skip_result(h.get("url", ""))
        ]

    # Web query from people.yaml web_results (facts-sharpened); skip award-domain + ibiblio
    # hits (award sites are sourced authoritatively elsewhere).
    if not data.get("military_awards"):
        web_hits: List[Dict[str, str]] = []
        for q in _render_queries("people", "web_results", name=name, facts=facts):
            web_hits.extend(_search_openserp(q, openserp_url))
        person_candidates["web_results"] = [
            h for h in web_hits if not _skip_result(h.get("url", ""))
        ]
    return person_candidates


def enrich_one_person(
    data: Dict,
    openserp_url: str,
    grok_client: Any,
    f: Any = None,
    max_images: Optional[int] = None,
    max_web: Optional[int] = None,
) -> bool:
    """Enrich ONE already-loaded person record in place (search + verify + apply).

    Returns True if the record was changed. This is the per-entity unit of work shared by the
    batch directory driver (below) and the SQS worker (one message = one entity); it does NOT
    read/write files or set the openserp_searched marker — the caller owns persistence.
    """
    if max_images is None or max_web is None:
        from src.utils.config import load_config

        cfg = load_config().get("openserp", {})
        max_images = (
            max_images
            if max_images is not None
            else cfg.get("max_images_per_entity", 1)
        )
        max_web = (
            max_web if max_web is not None else cfg.get("max_web_results_per_entity", 5)
        )
    name = data.get("name", "")
    if not name:
        return False
    c = _collect_person_candidates(f, name, data, openserp_url)
    return _verify_and_apply(c, data, name, grok_client, max_images, max_web)


def _openserp_recently_searched(data: Dict) -> bool:
    """True if this record was OpenSERP-searched within the 90-day retention window (so the
    driver should skip it this run). Mirrors the inline gate each driver used."""
    if not data.get("openserp_searched"):
        return False
    import time as _time

    searched_at = data.get("openserp_searched_at", 0)
    return bool(searched_at and (_time.time() - searched_at) < 90 * 86400)


def _mark_openserp_searched(data: Dict) -> None:
    """Stamp the record as searched (sets marker + timestamp, increments entities_searched).
    Behaviour identical to the inline block each driver used."""
    data["openserp_searched"] = True
    _metric("entities_searched")
    import time as _time

    data["openserp_searched_at"] = int(_time.time())


def _iter_loadable_records(directory: Path):
    """Yield (file, parsed_data) for each non-SKIP *.json file in ``directory`` (sorted) that
    parses as JSON; silently skips the index/skip files and any file that fails to parse.
    Mirrors the per-file load/skip preamble the drivers shared (the max_items break, which
    depends on the running enriched count, stays in each driver)."""
    for f in sorted(directory.glob("*.json")):
        if f.name in SKIP_FILES:
            continue
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        yield f, data


def enrich_people_with_openserp(
    people_dir: Path,
    openserp_url: str,
    grok_client: Any = None,
    max_items: Optional[int] = None,
) -> int:
    """Add images and academic sources to people files. Returns count enriched.

    Two-pass approach:
    1. Search OpenSERP for all people, collect candidate results
    2. Verify candidates with Grok (cached — repeat runs are free)
    3. Write verified results to files
    """
    reset_circuit()  # H3: don't inherit breaker state from a prior book/run
    if not _openserp_reachable(openserp_url):
        logger.warning("OpenSERP not reachable at %s — skipping", openserp_url)
        return 0

    # Pass 1: Collect candidates from OpenSERP
    candidates = _collect_people_candidates(people_dir, openserp_url, max_items)

    logger.info(
        "OpenSERP people: %d candidates collected, verifying with Grok...",
        len(candidates),
    )

    # Pass 2: Verify and write
    from src.utils.config import load_config

    cfg = load_config().get("openserp", {})
    max_images = cfg.get("max_images_per_entity", 1)
    max_web = cfg.get("max_web_results_per_entity", 5)

    enriched = 0
    for c in candidates:
        data = c["data"]
        name = c["name"]
        changed = _verify_and_apply(c, data, name, grok_client, max_images, max_web)

        _mark_openserp_searched(data)
        if write_json_with_lock(c["file"], data, entity="people"):
            if changed:
                enriched += 1
                _metric("entities_enriched")
                logger.info("  ✓ OpenSERP enriched: %s", name)

    logger.info("OpenSERP people enrichment: %d enriched", enriched)
    return enriched


def _collect_people_candidates(
    people_dir: Path, openserp_url: str, max_items: Optional[int]
) -> List[Dict]:
    """Pass 1 of people enrichment: iterate people files, skip the index/recently-searched/
    nameless ones, and build an OpenSERP candidate bundle per eligible person."""
    candidates: List[Dict] = []
    for f in sorted(people_dir.glob("*.json")):
        if f.name in SKIP_FILES:
            continue
        if max_items and len(candidates) >= max_items:
            break
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue

        if _openserp_recently_searched(data):
            continue

        name = data.get("name", "")
        if not _usable_entity_name(name):
            continue
        _record_eligible()

        candidates.append(_collect_person_candidates(f, name, data, openserp_url))
    return candidates


def enrich_equipment_with_openserp(
    equipment_dir: Path,
    openserp_url: str,
    grok_client: Any = None,
    max_items: Optional[int] = None,
) -> int:
    """Add images to equipment files. Returns count enriched."""
    reset_circuit()  # H3: don't inherit breaker state from a prior book/run
    if not _openserp_reachable(openserp_url):
        logger.warning("OpenSERP not reachable at %s — skipping", openserp_url)
        return 0

    enriched = 0
    for f in sorted(equipment_dir.glob("*.json")):
        if f.name in SKIP_FILES:
            continue
        if max_items and enriched >= max_items:
            break
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue

        if _openserp_recently_searched(data):
            continue

        name = data.get("common_name", data.get("name", ""))
        if not _usable_entity_name(name):
            continue
        _record_eligible()

        changed = enrich_one_equipment(data, openserp_url, grok_client)
        if changed:
            enriched += 1
            _metric("entities_enriched")
            logger.info("  ✓ OpenSERP enriched: %s", name)

        _mark_openserp_searched(data)
        write_json_with_lock(f, data, entity="equipment")

    logger.info("OpenSERP equipment enrichment: %d enriched", enriched)
    return enriched


def enrich_one_equipment(
    data: Dict,
    openserp_url: str,
    grok_client: Any,
    f: Any = None,  # noqa: ARG001 - signature parity with enrich_one_person
    max_images: Optional[int] = None,  # noqa: ARG001 - unused; search uses its own cap
    max_web: Optional[int] = None,  # noqa: ARG001 - equipment has no web results
) -> bool:
    """Enrich ONE already-loaded equipment record in place (search + verify + apply).

    Returns True if the record was changed. Mirrors enrich_one_person: this is the per-entity
    unit of work shared by the batch directory driver (above) and the SQS worker (one message =
    one entity); it does NOT read/write files or set the openserp_searched marker — the caller
    owns persistence. Metric increments other than items_added (which is intrinsic to the apply)
    stay in the driver's persistence section."""
    name = data.get("common_name", data.get("name", ""))
    if not name:
        return False
    if data.get("images"):
        return False
    images = search_equipment_images(
        name,
        openserp_url,
        grok_client,
        identifier=data.get("technical_identifier") or "",
        facts=_equipment_query_terms(data),
    )
    if not images:
        return False
    data["images"] = images
    _metric("items_added", len(images))
    return True


def enrich_source_sections_with_openserp(
    source_section_dir: Path,
    openserp_url: str,
    grok_client: Any = None,
    max_items: Optional[int] = None,
) -> int:
    """Add OpenSERP primary-source web results to source_section records, keyed on the derived
    OPERATION label (the coarse anchor — e.g. 'Battle of the Bulge') rather than granular event
    names. Only sections with an operation are searched. Native write, gated on
    openserp_searched (90-day), deduped. Returns count enriched."""
    reset_circuit()  # don't inherit breaker state from a prior book/run
    if not _openserp_reachable(openserp_url):
        logger.warning("OpenSERP not reachable at %s — skipping", openserp_url)
        return 0

    enriched = 0
    for f, data in _iter_loadable_records(source_section_dir):
        if max_items and enriched >= max_items:
            break
        if _process_one_source_section(f, data, openserp_url, grok_client):
            enriched += 1

    logger.info("OpenSERP source_section enrichment: %d enriched", enriched)
    return enriched


def _process_one_source_section(
    f: Path, data: Dict, openserp_url: str, grok_client: Any
) -> bool:
    """Gate + enrich + persist one source_section record. Returns True if it was enriched
    (so the driver increments its count). Records with no operation / recently searched are
    skipped (persisted unchanged only when searched)."""
    operation = data.get("operation")
    if not operation or not operation.get("name"):
        return False  # null-over-fake: no operation -> nothing to search

    if _openserp_recently_searched(data):
        return False

    _record_eligible()
    op_name = operation["name"]
    before = len(data.get("primary_sources") or [])
    changed = enrich_one_source_section(data, openserp_url, grok_client)
    enriched = False
    if changed:
        added_count = len(data.get("primary_sources") or []) - before
        enriched = True
        _metric("entities_enriched")
        logger.info("  ✓ OpenSERP primary sources for %s: +%d", op_name, added_count)

    _mark_openserp_searched(data)
    write_json_with_lock(f, data, entity="source_section")
    return enriched


def enrich_one_source_section(
    data: Dict,
    openserp_url: str,
    grok_client: Any,
    f: Any = None,  # noqa: ARG001 - signature parity with enrich_one_person
    max_images: Optional[int] = None,  # noqa: ARG001 - unused
    max_web: Optional[int] = None,  # noqa: ARG001 - unused
) -> bool:
    """Enrich ONE already-loaded source_section record in place (search + verify + apply).

    Returns True if primary_sources were added. Mirrors enrich_one_person: shared per-entity
    unit for the batch driver (above) and the SQS worker; it does NOT read/write files or set
    openserp_searched. entities_enriched/entities_searched + persistence stay in the driver.
    """
    operation = data.get("operation")
    if not operation or not operation.get("name"):
        return False  # null-over-fake: no operation -> nothing to search
    op_name = operation["name"]
    aliases = operation.get("aliases") or []
    results = search_event_content(
        op_name, aliases=aliases, openserp_url=openserp_url, grok_client=grok_client
    )
    existing = data.get("primary_sources") or []
    existing_urls = {s.get("url") for s in existing if isinstance(s, dict)}
    added = [r for r in results if r.get("url") not in existing_urls]
    if not added:
        return False
    data["primary_sources"] = existing + added
    _metric("items_added", len(added))
    return True


def _group_name_matches(group_name: str, result_title: str) -> bool:
    """Pre-filter for unit results: the (often numeric) unit designation must appear in the
    title. Units are highly ambiguous, so this is a coarse guard before Grok verification.
    """
    if not group_name:
        return True
    name_low = group_name.lower()
    title_low = result_title.lower()
    # Require the longest significant token (usually the ordinal/arm, e.g. "panzer",
    # "airborne", or the number) to be present.
    tokens = [t for t in name_low.replace("-", " ").split() if len(t) > 2]
    if not tokens:
        return name_low in title_low
    return any(t in title_low for t in tokens)


def _group_openserp_category(
    group_name: str,
    nationality: str,
    category: str,
    openserp_url: str,
    grok_client: Any,
    max_results: int,
) -> List[Dict[str, Any]]:
    """Run one people_groups.yaml query category (images|web_results|veterans_association)."""
    from src.utils.search_query_loader import render_search_queries

    queries = render_search_queries(
        "people_groups", category, name=group_name, nationality=nationality
    )
    out: List[Dict[str, str]] = []
    seen: set = set()
    context = f"{group_name} ({nationality}) in WWII"
    # Images have no article to summarize -> verify-only. Textual categories
    # (web_results, veterans_association) are fetched + summarized with pos/neg URL caching.
    textual = category != "images"
    for query in queries:
        for r in _search_openserp(query, openserp_url):
            url = r.get("url", "")
            title = r.get("title", "")
            if not url or url in seen or not _group_name_matches(group_name, title):
                continue
            if textual:
                processed = process_positive_url(
                    url, title, r.get("description", ""), context, grok_client
                )
                if processed:
                    out.append(processed)
                    seen.add(url)
            elif _verify_result(
                title, context, grok_client, snippet=r.get("description", ""), url=url
            ):
                out.append({"url": url, "title": title, "source": "openserp"})
                seen.add(url)
            if len(out) >= max_results:
                return out
    return out


def enrich_groups_with_openserp(
    groups_dir: Path,
    openserp_url: str,
    grok_client: Any = None,
    max_items: Optional[int] = None,
) -> int:
    """Add images, unit-history web results, and veterans-association sites to people_groups
    records. Queries are nationality-disambiguated and WWII-scoped (people_groups.yaml), so a
    same-numbered unit from another conflict/army is not matched. Native write, gated on
    openserp_searched (90-day), deduped. Returns count enriched."""
    reset_circuit()
    if not _openserp_reachable(openserp_url):
        logger.warning("OpenSERP not reachable at %s — skipping", openserp_url)
        return 0

    enriched = 0
    for f, data in _iter_loadable_records(groups_dir):
        if max_items and enriched >= max_items:
            break

        name = data.get("group_name", "")
        if not _usable_entity_name(name, min_len=3):
            continue

        if _openserp_recently_searched(data):
            continue
        _record_eligible()

        changed = enrich_one_group(
            data, openserp_url, grok_client, output_root=groups_dir.parent
        )

        _mark_openserp_searched(data)
        if write_json_with_lock(f, data, entity="people_groups"):
            if changed:
                enriched += 1
                _metric("entities_enriched")
                logger.info("  ✓ OpenSERP enriched group: %s", name)

    logger.info("OpenSERP people_groups enrichment: %d enriched", enriched)
    return enriched


def enrich_one_group(
    data: Dict,
    openserp_url: str,
    grok_client: Any,
    f: Any = None,  # noqa: ARG001 - signature parity with enrich_one_person
    max_images: Optional[int] = None,  # noqa: ARG001 - unused
    max_web: Optional[int] = None,  # noqa: ARG001 - unused
    output_root: Optional[Path] = None,
) -> bool:
    """Enrich ONE already-loaded people_groups record in place (search + verify + apply).

    Returns True if images / web_results / veterans_associations / a unit_patch were
    added. Mirrors enrich_one_person: shared per-entity unit for the batch driver (above)
    and the SQS worker; it does NOT read/write files or set openserp_searched.
    entities_enriched/entities_searched + persistence stay in the driver."""
    name = data.get("group_name", "")
    if not name or len(name) < 3:
        return False
    nationality = data.get("nationality") or ""
    changed = _apply_group_categories(
        data, name, nationality, openserp_url, grok_client
    )
    # Unit patch (ARTIFACT_STORE_DESIGN.md M0): download + VISION-verify + durably
    # store the SINGLE highest-resolution shoulder-sleeve insignia for this unit.
    # Only run once (null-over-fake: absent until a patch is confirmed). Fail-safe.
    if _apply_group_unit_patch(
        data, name, nationality, openserp_url, grok_client, output_root
    ):
        changed = True
    return changed


def _apply_group_categories(
    data: Dict,
    name: str,
    nationality: str,
    openserp_url: str,
    grok_client: Any,
) -> bool:
    """Run each people_groups query category (images/web_results/veterans_association), dedup
    against existing entries, and append new findings. Returns True if anything was added.
    """
    changed = False
    for category, field in (
        ("images", "images"),
        ("web_results", "web_results"),
        ("veterans_association", "veterans_associations"),
    ):
        results = _group_openserp_category(
            name, nationality, category, openserp_url, grok_client, max_results=3
        )
        if not results:
            continue
        existing = data.get(field) or []
        existing_urls = {r.get("url") for r in existing if isinstance(r, dict)}
        added = [r for r in results if r.get("url") not in existing_urls]
        if added:
            data[field] = existing + added
            _metric("items_added", len(added))
            changed = True
    return changed


def _apply_group_unit_patch(
    data: Dict,
    name: str,
    nationality: str,
    openserp_url: str,
    grok_client: Any,
    output_root: Optional[Path],
) -> bool:
    """Acquire + store a unit patch if the record has none yet. Returns True if one was added.
    Fail-safe: a patch-acquisition error never blocks group enrichment."""
    if data.get("unit_patch"):
        return False
    try:
        patch = _acquire_group_unit_patch(
            name,
            nationality,
            data.get("GroupID", ""),
            openserp_url,
            grok_client,
            output_root,
        )
        if patch:
            data["unit_patch"] = patch
            _metric("items_added", 1)
            return True
    except Exception as e:  # noqa: BLE001 - never block group enrichment
        logger.debug("unit patch acquisition failed for %s: %s", name, e)
    return False


def _acquire_group_unit_patch(
    name: str,
    nationality: str,
    group_id: str,
    openserp_url: str,
    grok_client: Any,
    output_root: Optional[Path],
) -> Optional[Dict[str, Any]]:
    """Gather patch/insignia image candidates (OpenSERP) and hand them to the
    vision-verifying single-largest-patch acquirer. Returns the unit_patch record
    or None."""
    if not group_id or not grok_client:
        return None
    from src.enrichment import unit_patch
    from src.utils.search_query_loader import render_search_queries

    # Patch-ONLY queries (not generic photographs): insignia + unit patch.
    candidates: List[Dict[str, str]] = []
    seen: set = set()
    for query in render_search_queries(
        "people_groups", "images", name=name, nationality=nationality
    ):
        if "insignia" not in query.lower() and "patch" not in query.lower():
            continue  # patch-only per decision (drop the generic photograph query)
        for r in _search_openserp(query, openserp_url):
            url = r.get("url", "")
            if url and url not in seen:
                seen.add(url)
                candidates.append({"url": url, "title": r.get("title", "")})
    if not candidates:
        return None
    return unit_patch.acquire_unit_patch(
        candidates,
        group_id=group_id,
        unit_name=name,
        nationality=nationality,
        grok_client=grok_client,
        output_root=output_root,
    )


def place_name_variants(place: Dict) -> List[str]:
    """Return the deduped set of names to search for a place: current_name + name + aliases +
    historical_names[].name (the native-language WWII names Phase 2 already extracted, e.g.
    German 'Pfalz' for Palatinate). Order-preserving, case-insensitive dedup."""
    variants: List[str] = []
    seen: set = set()

    def _add(n: Any) -> None:
        if isinstance(n, str) and n.strip() and n.strip().lower() not in seen:
            seen.add(n.strip().lower())
            variants.append(n.strip())

    _add(place.get("current_name"))
    _add(place.get("name"))
    for a in place.get("aliases") or []:
        _add(a)
    for h in place.get("historical_names") or []:
        _add(h.get("name") if isinstance(h, dict) else h)
    return variants


def enrich_places_with_openserp(
    places_dir: Path,
    openserp_url: str,
    grok_client: Any = None,
    max_items: Optional[int] = None,
) -> int:
    """Add images + source web results to place records, searching EACH name variant
    (current + aliases + native-language historical names), so e.g. a place's German WWII name
    is searched alongside its English name. Textual sources are fetched + summarized via
    process_positive_url (with pos/neg URL caching); images are verify-only. Native write,
    gated on openserp_searched (90-day), deduped. Returns count enriched."""
    reset_circuit()
    if not _openserp_reachable(openserp_url):
        logger.warning("OpenSERP not reachable at %s — skipping", openserp_url)
        return 0

    enriched = 0
    for f, data in _iter_loadable_records(places_dir):
        if max_items and enriched >= max_items:
            break

        variants = place_name_variants(data)
        if not variants:
            continue

        if _openserp_recently_searched(data):
            continue

        primary = variants[0]
        changed = enrich_one_place(data, openserp_url, grok_client)

        _mark_openserp_searched(data)
        if write_json_with_lock(f, data, entity="places"):
            if changed:
                enriched += 1
                _metric("entities_enriched")
                logger.info("  ✓ OpenSERP enriched place: %s", primary)

    logger.info("OpenSERP places enrichment: %d enriched", enriched)
    return enriched


def enrich_one_place(
    data: Dict,
    openserp_url: str,
    grok_client: Any,
    f: Any = None,  # noqa: ARG001 - signature parity with enrich_one_person
    max_images: Optional[int] = None,  # noqa: ARG001 - unused
    max_web: Optional[int] = None,  # noqa: ARG001 - unused
) -> bool:
    """Enrich ONE already-loaded place record in place (search + verify + apply).

    Searches EACH name variant (current + aliases + native-language historical names): images
    are verify-only; textual sources are fetched + summarized via process_positive_url. Returns
    True if images or web_results were added. Mirrors enrich_one_person: shared per-entity unit
    for the batch driver (above) and the SQS worker; it does NOT read/write files or set
    openserp_searched. entities_enriched/entities_searched + persistence stay in the driver.
    """
    from src.utils.search_query_loader import render_search_queries

    variants = place_name_variants(data)
    if not variants:
        return False
    primary = variants[0]
    changed = _enrich_place_images(data, variants, primary, openserp_url, grok_client)
    changed = (
        _enrich_place_sources(data, variants, primary, openserp_url, grok_client)
        or changed
    )
    return changed


def _enrich_place_images(
    data: Dict, variants: list, primary: str, openserp_url: str, grok_client: Any
) -> bool:
    """Images (verify-only): search each name variant, Grok-verify each hit, append new ones."""
    from src.utils.search_query_loader import render_search_queries

    existing_img_urls = {
        i.get("url") for i in (data.get("images") or []) if isinstance(i, dict)
    }
    changed = False
    for variant in variants:
        for q in render_search_queries("places", "images", name=variant):
            for r in _search_openserp(q, openserp_url):
                url = r.get("url", "")
                if not url or url in existing_img_urls:
                    continue
                if _verify_result(
                    r.get("title", ""),
                    f"Photo of {primary} (WWII place)",
                    grok_client,
                    snippet=r.get("description", ""),
                    url=url,
                ):
                    data.setdefault("images", []).append(
                        {"url": url, "title": r.get("title", ""), "source": "openserp"}
                    )
                    existing_img_urls.add(url)
                    changed = True
    return changed


def _enrich_place_sources(
    data: Dict, variants: list, primary: str, openserp_url: str, grok_client: Any
) -> bool:
    """Textual sources: search each variant, fetch + summarize + pos/neg-cache each hit."""
    from src.utils.search_query_loader import render_search_queries

    existing_src_urls = {
        s.get("url") for s in (data.get("web_results") or []) if isinstance(s, dict)
    }
    changed = False
    for variant in variants:
        for q in render_search_queries("places", "sources", name=variant):
            for r in _search_openserp(q, openserp_url):
                url = r.get("url", "")
                if not url or url in existing_src_urls:
                    continue
                processed = process_positive_url(
                    url,
                    r.get("title", ""),
                    r.get("description", ""),
                    f"{primary} in World War II",
                    grok_client,
                )
                if processed:
                    data.setdefault("web_results", []).append(processed)
                    existing_src_urls.add(url)
                    _metric("items_added")
                    changed = True
    return changed

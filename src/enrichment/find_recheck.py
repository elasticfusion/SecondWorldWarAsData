"""Re-check approved finds by checksum (owner decision 2026-11-17, built 2026-11-24).

An approved (affirmed or submitted) find names the page it was judged on (``find_pages``: kept
under its checksum). Every ``find_recheck.interval_days`` (default 180) the page is fetched again
through ``url_fetch`` (registry, politeness, SSRF checks, Wayback fallback) and its TEXT checksum
(``find_pages.text_sha256``: active content, markup and whitespace dropped) is compared with the
kept page's:

* **unchanged** - no Grok request; the check is recorded (``last_checked_at``).
* **changed** - the new page is kept (without moving the URL's pointer: the cached summary still
  describes the approved page) and queued for human review as a NEW find of the same entity
  (``recheck_of`` = the approved find; queued once per text revision). The approved find, its kept
  page and the Phase 0 source made from it stay as they are: the cited version, and slugs never
  change. The relevance verdict is the approved find's (same URL, same entity, and a human approved
  it; ``relevancy_from`` says so); no Grok request is made - the reviewer sees the previous summary
  and decides. A changed find is not checked again: its new version is a find of its own, checked
  once approved.
* **dead** - the live page is gone, only the Internet Archive has it, or the registry knows it is
  dead: recorded with the date it was first seen dead; the kept page stays, so citations still
  resolve. Checked again after the interval.
* **retry** - deferred, rate-limited or walled with no archive: checked again in ``RETRY_DAYS``
  (the last state, its date and history are kept; the attempt is noted). A change that could not
  be kept or queued is tried again in ``FAILED_BACKOFF_DAYS``, so it cannot hold the per-run cap.

The comparison is of the page's TEXT: a page that changes only a link target or an image (same
text) counts as unchanged; the approved copy keeps the links it had.

Each check is a record (``find_recheck_v1``, keyed on the find's id, 10 years) with the history of
earlier states, so every change is traceable to the page versions it compared. Runs as a Phase 3
step (``phase3_enrich_data``: the pipeline task has the AWS-mode registry and cache), at most
``find_recheck.max_per_run`` finds per run, oldest check first.
"""

from __future__ import annotations

import datetime as _dt
import json
import logging
import time
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

RECHECK_SOURCE = "find_recheck_v1"
RECORD_DAYS = 3650
DEFAULT_INTERVAL_DAYS = 180
DEFAULT_MAX_PER_RUN = 50
RETRY_DAYS = 14
FAILED_BACKOFF_DAYS = 1
_HISTORY_MAX = 10
_DAY_S = 86400

UNCHANGED = "unchanged"
CHANGED = "changed"
DEAD = "dead"
RETRY = "retry"
BASELINE = "baseline"  # first check of a find with no kept page: nothing to compare yet
FAILED = "failed"  # the change could not be kept / queued: tried again after a backoff
_OUTCOMES = (UNCHANGED, CHANGED, DEAD, RETRY, BASELINE, FAILED)


def _settings(config: Optional[Dict[str, Any]]) -> "tuple[int, int]":
    from src.enrichment.openserp_enrichment import _config_int

    cfg = (config or {}).get("find_recheck") or {}
    return (
        _config_int(
            cfg, "interval_days", DEFAULT_INTERVAL_DAYS, hi=3650, section="find_recheck"
        ),
        _config_int(
            cfg, "max_per_run", DEFAULT_MAX_PER_RUN, hi=1000, section="find_recheck"
        ),
    )


def _iso(t: float) -> str:
    return _dt.datetime.fromtimestamp(t, _dt.timezone.utc).isoformat()


def state_for(fetch_event_id: str) -> Optional[Dict[str, Any]]:
    """The last re-check record of a find, or None (never checked)."""
    from src.utils.search_cache import get_cached

    raw = get_cached(RECHECK_SOURCE, fetch_event_id)
    try:
        record = json.loads(raw) if raw else None
    except ValueError:
        return None
    return record if isinstance(record, dict) and record.get("status") else None


def _save(fetch_event_id: str, record: Dict[str, Any]) -> bool:
    from src.utils.search_cache import cache_result

    try:
        cache_result(
            RECHECK_SOURCE,
            fetch_event_id,
            json.dumps(record),
            positive_days=RECORD_DAYS,
        )
        return True
    except Exception as e:  # noqa: BLE001 - said; the find is checked again next run
        logger.error(
            "OBSERV find_recheck.state_not_saved err=%s status=error - the re-check result was "
            "not stored, so the find is checked again next run. Check the task role's access to "
            "the api-cache table",
            type(e).__name__,
        )
        return False


def _eligible(event: Dict[str, Any]) -> bool:
    from src.utils import fetch_provenance as fp

    return (
        event.get("review_status") in (fp.AFFIRMED, fp.SUBMITTED)
        and event.get("provider") in fp.FIND_PROVIDERS
        and bool(event.get("endpoint"))
        and bool(event.get("fetch_event_id"))
    )


def _judged_at(event: Dict[str, Any]) -> float:
    """When the find's page was fetched (else when it was queued), as epoch seconds; 0 unknown."""
    params = event.get("request_params") or {}
    fetched = params.get("page_fetched_at")
    if isinstance(fetched, (int, float)) and not isinstance(fetched, bool):
        return float(fetched)
    try:
        at = _dt.datetime.fromisoformat(str(event.get("queried_at")))
    except (TypeError, ValueError):
        return 0.0
    if at.tzinfo is None:  # stored times are UTC
        at = at.replace(tzinfo=_dt.timezone.utc)
    return at.timestamp()


def _next_check(
    event: Dict[str, Any], state: Optional[Dict[str, Any]], interval_s: float
) -> Optional[float]:
    """When the find is next due (epoch seconds), or None for a changed find."""
    if state:
        if state.get("status") == CHANGED:
            return None
        nxt = state.get("next_check_at")
        if isinstance(nxt, (int, float)):
            return float(nxt)
    return _judged_at(event) + interval_s


def due_finds(
    events: List[Dict[str, Any]], now: float, interval_days: int
) -> List["tuple[Dict[str, Any], Optional[Dict[str, Any]]]"]:
    """Approved finds due for a re-check, with their last record, oldest due first."""
    interval_s = interval_days * _DAY_S
    due = []
    for event in events:
        # every recorded next check is at or after judged + interval: a younger find needs no
        # record lookup
        if not _eligible(event) or _judged_at(event) + interval_s > now:
            continue
        state = state_for(event["fetch_event_id"])
        nxt = _next_check(event, state, interval_s)
        if nxt is not None and nxt <= now:
            due.append((nxt, event, state))
    due.sort(key=lambda t: t[0])
    return [(e, s) for _, e, s in due]


def _kept_text_sha(event: Dict[str, Any], state: Optional[Dict[str, Any]]) -> str:
    """The text checksum the page is compared with: the last check's, else the kept page's
    ("" when the find has no usable kept page)."""
    from src.utils import find_pages
    from src.utils.html_safety import PageTooComplex

    if state and state.get("text_sha256"):
        return str(state["text_sha256"])
    try:
        body = find_pages.load(event.get("request_params") or {})
        return find_pages.text_sha256(body.decode("utf-8", "replace")) if body else ""
    except (find_pages.PageUnusable, PageTooComplex):
        return ""


def _record(
    event: Dict[str, Any],
    state: Optional[Dict[str, Any]],
    status: str,
    now: float,
    next_s: Optional[float],
    **fields: Any,
) -> Dict[str, Any]:
    """A check that decided something: the new state, the previous one moved to the history."""
    prev = state or {}
    history = list(prev.get("history") or [])
    if state:
        keys = ("status", "checked_at", "text_sha256", "page_sha256")
        history.append({k: prev.get(k) for k in keys})
    params = event.get("request_params") or {}
    if status == DEAD:
        fields["dead_since"] = _dead_since(prev, now)
    return {
        "fetch_event_id": event["fetch_event_id"],
        "url": event.get("endpoint"),
        "status": status,
        "checked_at": _iso(now),
        "last_checked_at": now,
        "next_check_at": next_s,
        "text_sha256": prev.get("text_sha256") or "",
        "page_sha256": prev.get("page_sha256") or params.get("page_sha256"),
        **fields,
        "history": history[-_HISTORY_MAX:],
    }


def _dead_since(prev: Dict[str, Any], now: float) -> str:
    """The date the page was FIRST seen dead, kept across later dead checks."""
    if prev.get("status") == DEAD and prev.get("dead_since"):
        return str(prev["dead_since"])
    return _iso(now)


def _retry(
    event: Dict[str, Any],
    state: Optional[Dict[str, Any]],
    now: float,
    next_s: float,
    why: str,
) -> Dict[str, Any]:
    """An attempt that decided nothing: the last state (status, dates, history) stays; only the
    next check and the attempt are noted."""
    from src.utils.find_queue import clean_for_log

    base = dict(state) if state else _record(event, None, RETRY, now, next_s)
    base["history"] = list(base.get("history") or [])  # not shared with the old state
    base.update(
        next_check_at=next_s, last_attempt_at=now, last_attempt=clean_for_log(why, 120)
    )
    return base


def _origin(params: Dict[str, Any]) -> Any:
    from src.utils.find_queue import Origin

    return Origin(
        entity_type=params.get("origin_entity_type") or "",
        entity_id=params.get("origin_entity_id") or "",
        book=params.get("source_doc") or "",
        source_url=params.get("source_url") or "",
        query=params.get("query") or "",
    )


def _queue_changed(
    event: Dict[str, Any], html: str, text_sha: str, now: float
) -> Optional[Dict[str, Any]]:
    """Keep the changed page and queue it as a new find. Returns the fields for the re-check
    record, or None when it could not be kept or queued."""
    from src.utils import find_pages, find_queue

    page = find_pages.keep(event["endpoint"], html, point=False)
    if not page.get("page_kept"):
        return None  # find_pages said why
    params = event.get("request_params") or {}
    changed_at = _iso(now)
    new_params = {
        "title": params.get("title"),
        "previous_summary": params.get("summary"),
        "recheck_of": event["fetch_event_id"],
        "previous_page_sha256": params.get("page_sha256"),
        "content_changed_at": changed_at,
        "relevancy_from": event[
            "fetch_event_id"
        ],  # no new Grok request: the approved verdict
        **{k: v for k, v in params.items() if k == "category"},
        **{k: page.get(k) for k in find_pages.PAGE_FIELDS},
        **{k: page[k] for k in find_pages.OPTIONAL_PAGE_FIELDS if k in page},
        "page_kept": True,
    }
    status = find_queue.enqueue_find(
        provider=event["provider"],
        url=event["endpoint"],
        verdict=find_queue.RELEVANT,
        origin=_origin(params),
        # the review UI shows this line: say the verdict is carried, not judged on the new page
        summary=f"Changed since approved find {event['fetch_event_id']} (relevance carried "
        f"from it, not re-judged): {params.get('title') or event['endpoint']}",
        params=new_params,
        revision=text_sha,  # the text that changed: per-request markup is not a new version
    )
    if status not in (find_queue.ENQUEUED, find_queue.ALREADY_QUEUED):
        return None  # find_queue logged it
    return {
        "text_sha256": text_sha,
        "page_sha256": page["page_sha256"],
        "content_changed_at": changed_at,
    }


def _settle_claim(event: Dict[str, Any], result: Any) -> None:
    """A fetch that claimed the URL in the registry: settle it (a page came back) or give it back
    (no verdict: other steps may try it). A dead URL is already recorded by ``url_fetch``.
    """
    from src.utils import url_fetch, url_registry

    if result.source in (url_fetch.LIVE, url_fetch.WAYBACK):
        url_registry.finalize_searched(
            result.final_url or event["endpoint"],
            relevant=True,
            context="find re-check",
            source="find-recheck",
            first_wayback_ts=result.wayback_ts or "",
            http_status=result.http_status,
        )
    elif result.source in (url_fetch.BLOCKED_NO_ARCHIVE, url_fetch.RATE_LIMITED):
        url_registry.release(event["endpoint"])


def _is_dead(result: Any) -> bool:
    """Gone (no archive), only archived, or known dead to the registry (permanent: no fetch)."""
    from src.utils import url_fetch

    if result.source in (url_fetch.DEAD, url_fetch.WAYBACK):
        return True
    rec = result.registry_record
    return result.source == url_fetch.SKIP and getattr(rec, "status", "") == "dead"


def recheck_one(
    event: Dict[str, Any],
    state: Optional[Dict[str, Any]],
    now: float,
    interval_days: int,
) -> str:
    """Re-check one approved find and record the result. Returns its outcome (``_OUTCOMES``)."""
    from src.utils import url_fetch
    from src.utils.find_queue import clean_for_log

    later = now + interval_days * _DAY_S
    fid = event["fetch_event_id"]
    result = url_fetch.fetch(event["endpoint"])
    _settle_claim(event, result)
    if _is_dead(result):
        # the live page is gone (or walled): the kept page stays the cited copy
        dead = _record(
            event,
            state,
            DEAD,
            now,
            later,
            detail=clean_for_log(result.detail, 120),
            archived_url=clean_for_log(result.archived_url or "", 300) or None,
        )
        _save(fid, dead)
        return DEAD
    if result.source != url_fetch.LIVE or not result.content:
        # SKIP: another step searched it lately, or its domain is excluded - wait the interval;
        # anything else (deferred, walled, rate-limited) may clear soon
        soon = result.source != url_fetch.SKIP
        nxt = now + RETRY_DAYS * _DAY_S if soon else later
        _save(fid, _retry(event, state, now, nxt, result.detail))
        return RETRY
    return _compare(event, state, result.content, now, later)


def _compare(
    event: Dict[str, Any],
    state: Optional[Dict[str, Any]],
    html: str,
    now: float,
    later: float,
) -> str:
    """The live page against the approved one: unchanged, changed (queued), or a baseline."""
    from src.utils import find_pages
    from src.utils.find_queue import clean_for_log
    from src.utils.html_safety import PageTooComplex

    fid = event["fetch_event_id"]
    try:
        new_sha = find_pages.text_sha256(html)
    except PageTooComplex as e:
        logger.warning(
            "OBSERV find_recheck.page_too_complex fetch_event_id=%s why=%s status=warn - the "
            "page could not be compared (too large or too slow to strip), so a change would go "
            "unseen; it is tried again after the interval. Many of these: check html_safety's "
            "limits against these pages",
            clean_for_log(fid, 40),
            clean_for_log(e, 120),
        )
        _save(fid, _retry(event, state, now, later, "page too complex to compare"))
        return RETRY
    old_sha = _kept_text_sha(event, state)
    if not old_sha:
        _save(fid, _record(event, state, BASELINE, now, later, text_sha256=new_sha))
        return BASELINE
    if new_sha == old_sha:
        _save(fid, _record(event, state, UNCHANGED, now, later, text_sha256=new_sha))
        return UNCHANGED
    queued = _queue_changed(event, html, new_sha, now)
    if queued is None:  # find_pages / find_queue said why
        backoff = now + FAILED_BACKOFF_DAYS * _DAY_S
        _save(fid, _retry(event, state, now, backoff, "change not kept or queued"))
        return FAILED
    _save(fid, _record(event, state, CHANGED, now, None, **queued))
    logger.info(
        "OBSERV find_recheck.changed fetch_event_id=%s provider=%s - the approved page changed; "
        "the new version is queued for review (recheck_of=%s)",
        clean_for_log(fid, 40),
        clean_for_log(event.get("provider"), 40),
        clean_for_log(fid, 40),
    )
    return CHANGED


def run(config: Optional[Dict[str, Any]] = None, now: Optional[float] = None) -> int:
    """Re-check the approved finds that are due (at most ``max_per_run``). Returns the number
    checked. Never raises for one find: a failure is counted and the run goes on."""
    from src.enrichment import fetch_review
    from src.utils.find_queue import clean_for_log

    interval_days, max_per_run = _settings(config)
    now = time.time() if now is None else now
    events, errors = fetch_review.load_events()
    if errors:
        logger.warning(
            "OBSERV find_recheck.load_errors errors=%d status=warn - some find events could not "
            "be read, so they are not re-checked this run. Check the api-cache table scan "
            "(fetch_review.load_events) and the unreadable records",
            errors,
        )
    due = due_finds(events, now, interval_days)
    counts: Dict[str, int] = {k: 0 for k in _OUTCOMES}
    for event, state in due[:max_per_run]:
        try:
            counts[recheck_one(event, state, now, interval_days)] += 1
        except Exception as e:  # noqa: BLE001 - one find must not stop the run
            counts[FAILED] += 1
            backoff = now + FAILED_BACKOFF_DAYS * _DAY_S  # not first in line next run
            _save(
                event["fetch_event_id"],
                _retry(event, state, now, backoff, f"error: {type(e).__name__}"),
            )
            logger.error(
                "OBSERV find_recheck.error fetch_event_id=%s err=%s status=error - the find "
                "was not re-checked; it is tried again after a day. See the traceback",
                clean_for_log(event.get("fetch_event_id"), 40),
                type(e).__name__,
                exc_info=True,
            )
    checked = sum(counts.values()) - counts[FAILED]
    logger.log(
        logging.WARNING if counts[FAILED] else logging.INFO,
        "OBSERV find_recheck.summary due=%d checked=%d %s left=%d%s",
        len(due),
        checked,
        " ".join(f"{k}={v}" for k, v in counts.items()),
        max(0, len(due) - max_per_run),
        (
            " status=warn - some finds could not be re-checked (see the errors above); they "
            "are tried again next run"
            if counts[FAILED]
            else ""
        ),
    )
    return checked

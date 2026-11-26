"""Universal fetch-provenance primitive — every external fetch becomes a traceable event.

Principle (owner): "I queried source X at time T with params P and got result R (status S,
outcome O)." If an API misbehaves you can walk backwards from the stored record. A positive
*textual* find is additionally enqueued for human review and, on affirm, flows through the SAME
path into ingestion/extraction like an uploaded book (handled by callers; this module records).

Design: docs/archive/2026-11-16/FETCH_PROVENANCE_DESIGN.md. This is operational TELEMETRY, not a
schema-governed corpus entity (no _schema_version / fingerprint). Storage reuses the existing
``dev-wwii-api-cache`` DynamoDB table (key ``fetchevent#{id}``) in AWS mode, and an append-only
JSONL at ``output/_fetch_events/<provider>.jsonl`` locally. Recording is ALWAYS fail-safe: a
recording error never blocks or fails the underlying fetch.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

import requests
import ulid

logger = logging.getLogger(__name__)

# outcome enum
HIT = "hit"
MISS = "miss"
ERROR = "error"
BLOCKED = "blocked"

# kind enum
STRUCTURED = "structured"  # geocode / weather — provenance only
TEXTUAL = "textual"  # a find (review-eligible) or a lookup (provenance only)

# review_status enum (textual only)
PENDING = "pending"
AFFIRMED = "affirmed"
REJECTED = "rejected"
SUBMITTED = "submitted"  # affirmed AND handed to Phase-1 as part of a batch (terminal)

# ENQUEUE POLICY (owner 2026-11-17): a SEARCH CALL is never a review item. Only a FIND is: a URL a
# step found and Grok judged relevant, queued through ``src/utils/find_queue.enqueue_find`` (one
# helper for every finder), carrying its origin. ``recorded_fetch`` records search requests as
# provenance only (nara / archive.org / gutendex queries used to queue one item per successful query,
# with no BibliographyID and no verdict).
#
# FIND_PROVIDERS: the providers that ``enqueue_find`` may queue under (one per finder, so a reviewer
# sees where a find came from). "link-asset" is a link cited in a parsed document (M4).
FIND_PROVIDERS = frozenset(
    {
        "link-asset",  # a link cited in a parsed document
        "openserp-find",  # a page OpenSERP found for an entity
        "archive-org-find",  # an Archive.org item for a bibliography record
        "gutenberg-find",  # a Project Gutenberg text for a bibliography record
        "nara-find",  # a National Archives record for a bibliography record
        "llm-find",  # a URL Grok suggested, content-verified
        "map-find",  # a map URL
    }
)
# "wayback" is NOT here: the availability lookup is a fetch fallback for a URL some step already
# found (that find is what a human reviews); the snapshot it returns is recorded as provenance only.
_ENQUEUE_PROVIDERS = FIND_PROVIDERS


def _initial_review_status(provider: str, kind: str, outcome: str) -> Optional[str]:
    """A fetch is enqueued for human review only when it is a TEXTUAL hit from an enqueue-eligible
    primary-source provider; everything else records provenance with no review (review_status=None).
    """
    if kind == TEXTUAL and outcome == HIT and provider in _ENQUEUE_PROVIDERS:
        return PENDING
    return None


_KEY_PREFIX = "fetchevent#"
_LOCAL_DIR = Path("output/_fetch_events")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _url_key(endpoint: str) -> Optional[str]:
    """sha256 link back to a url_registry entry (clean URL) when this is a URL fetch."""
    try:
        from src.utils.url_hygiene import clean_url

        clean = clean_url(endpoint)
        if not clean:
            return None
        return hashlib.sha256(clean.encode("utf-8")).hexdigest()
    except Exception:  # noqa: BLE001 - best-effort linkage only
        return None


def _redact(params: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Shallow-redact obvious secrets from request params before recording."""
    if not isinstance(params, dict):
        return {}
    out = {}
    for k, v in params.items():
        if any(
            s in str(k).lower() for s in ("token", "key", "secret", "password", "auth")
        ):
            out[k] = "<redacted>"
        else:
            out[k] = v
    return out


def _table():
    """DynamoDB table handle (dev-wwii-api-cache), or None (local/test). Mirrors url_registry."""
    try:
        from src.utils.config import load_config
        from src.utils.aws_clients import get_dynamo_table

        aws = load_config().get("aws", {})
        if not aws.get("enabled"):
            return None
        name = aws.get("cache_table", "dev-wwii-api-cache")
        region = aws.get("region", "us-east-1")
        return get_dynamo_table(name, region=region)
    except Exception as e:  # noqa: BLE001
        logger.debug("fetch-provenance table unavailable: %s", e)
        return None


def _persist(record: Dict[str, Any], once: bool = False) -> bool:
    """Write the fetch-provenance record. Fail-safe: never raise. DynamoDB in AWS mode,
    append-only JSONL locally. ``once``: not written if a record with this id exists (a review
    item queued by an earlier run keeps its review status). Returns whether it was written.
    """
    try:
        table = _table()
        if table is not None:
            kwargs: Dict[str, Any] = (
                {"ConditionExpression": "attribute_not_exists(cache_key)"}
                if once
                else {}
            )
            table.put_item(
                Item={
                    "cache_key": f"{_KEY_PREFIX}{record['fetch_event_id']}",
                    "response": json.dumps(record),
                },
                **kwargs,
            )
            return True
        # Local/test: append to output/_fetch_events/<provider>.jsonl
        provider = str(record.get("provider") or "unknown").replace("/", "_")
        _LOCAL_DIR.mkdir(parents=True, exist_ok=True)
        path = _LOCAL_DIR / f"{provider}.jsonl"
        if once and record["fetch_event_id"] in _local_ids(path):
            return False
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")
        _local_ids(path).add(record["fetch_event_id"])
        return True
    except Exception as e:  # noqa: BLE001 - recording must never block a fetch
        if type(e).__name__ == "ConditionalCheckFailedException":
            return False  # recorded by an earlier run
        if (
            once
        ):  # a review item that was not stored: say so (it is queued again next run)
            logger.warning(
                "OBSERV fetch_provenance.write_failed provider=%s err=%s status=warn - the "
                "review item was not stored; the next run queues it again. Check the task "
                "role's DynamoDB access to the api-cache table",
                str(record.get("provider"))[:40],
                type(e).__name__,
            )
        else:
            logger.debug("fetch-provenance persist skipped: %s", e)
        return False


_LOCAL_SEEN: Dict[str, set] = {}


def event_recorded(provider: str, once_key: tuple) -> bool:
    """Whether the event with this (provider, once_key) identity is already stored, with any review
    status. Fail-safe: False when it cannot be told (the caller then judges and the once-write guard
    still prevents a duplicate event)."""
    try:
        from src.utils.deterministic_ids import fetch_event_id as _fixed_id

        event_id = _fixed_id(provider, *once_key)
        table = _table()
        if table is not None:
            got = table.get_item(Key={"cache_key": f"{_KEY_PREFIX}{event_id}"})
            return bool(got.get("Item"))
        path = _LOCAL_DIR / f"{provider.replace('/', '_')}.jsonl"
        return event_id in _local_ids(path)
    except Exception as e:  # noqa: BLE001
        logger.debug("event_recorded unavailable: %s", e)
        return False


def _local_ids(path: Path) -> set:
    """The event ids already in a local JSONL file (read once per process, then kept)."""
    key = str(path)
    if key not in _LOCAL_SEEN:
        seen: set = set()
        if path.exists():
            for line in path.read_text(encoding="utf-8").splitlines():
                try:
                    seen.add(json.loads(line).get("fetch_event_id"))
                except (ValueError, AttributeError):
                    continue
        _LOCAL_SEEN[key] = seen
    return _LOCAL_SEEN[key]


def record_fetch_event(
    *,
    provider: str,
    endpoint: str,
    kind: str,
    http_method: str = "GET",
    request_params: Optional[Dict[str, Any]] = None,
    http_status: Optional[int] = None,
    outcome: str = HIT,
    result_summary: Optional[str] = None,
    latency_ms: Optional[int] = None,
    error: Optional[str] = None,
    queried_at: Optional[str] = None,
    once_key: Optional[tuple] = None,
) -> str:
    """Record a single fetch-provenance event (for callers that already hold a response, e.g.
    wayback's courtesy wrapper). Returns the fetch_event_id. Fail-safe. ``once_key``: the
    event's identity - its id is derived from it and it is recorded once ever; a repeat
    returns '' (task 1.2f: a re-run must not queue a second review item)."""
    from src.utils.deterministic_ids import fetch_event_id as _fixed_id

    fetch_event_id = _fixed_id(provider, *once_key) if once_key else str(ulid.new())
    record: Dict[str, Any] = {
        "fetch_event_id": fetch_event_id,
        "provider": provider,
        "endpoint": endpoint,
        "request_params": _redact(request_params),
        "http_method": http_method,
        "queried_at": queried_at or _now_iso(),
        "http_status": http_status,
        "outcome": outcome,
        "result_summary": result_summary,
        "latency_ms": latency_ms,
        "error": error,
        "url_key": _url_key(endpoint),
        "kind": kind,
        "review_status": _initial_review_status(provider, kind, outcome),
    }
    written = _persist(record, once=bool(once_key))
    return fetch_event_id if written or not once_key else ""


def recorded_fetch(
    provider: str,
    endpoint: str,
    *,
    kind: str,
    method: str = "GET",
    params: Optional[Dict[str, Any]] = None,
    session: Optional[requests.Session] = None,
    result_summary_fn=None,
    **kwargs: Any,
) -> requests.Response:
    """Perform an external fetch through the single choke point, recording a fetch-provenance
    event (queried_at, status, outcome, latency, result summary). Reuses the pooled
    ``http_pool.get_session()`` unless a session is supplied.

    ``result_summary_fn(resp) -> str`` optionally derives a compact result summary (NOT the full
    payload). Fail-safe: a recording error is swallowed; the HTTP response (or the raised transport
    error) is the caller's to handle — on a transport exception we record outcome=error and
    re-raise so callers keep their existing error handling.

    Return contract: ALWAYS returns a ``requests.Response`` or RAISES (never None). An unsafe
    endpoint host raises ``ValueError``; a transport error is recorded then re-raised. Callers can
    therefore use the result without a None-check (the annotation was formerly ``Optional`` which
    produced spurious mypy union-attr noise across enrichers).
    """
    if session is None:
        from src.utils.http_pool import get_session

        session = get_session()

    # Defense-in-depth: recorded_fetch is for trusted FIXED first-party hosts, but validate the
    # endpoint host anyway (reject loopback/private/link-local/metadata/bad-scheme) so a future
    # caller passing a source/LLM-derived host can't turn this into an SSRF. clean_url is used only
    # as a GATE (its normalized form is discarded — the real request keeps its query params).
    from src.utils.url_hygiene import clean_url

    if clean_url(endpoint) is None:
        record_fetch_event(
            provider=provider,
            endpoint=endpoint,
            kind=kind,
            http_method=method,
            request_params=params,
            http_status=None,
            outcome=ERROR,
            latency_ms=0,
            error="blocked_unsafe_host",
        )
        raise ValueError(f"recorded_fetch refused unsafe endpoint host: {endpoint}")

    start = time.monotonic()
    resp: Optional[requests.Response] = None
    try:
        resp = session.request(method, endpoint, params=params, **kwargs)
    except (
        Exception
    ) as exc:  # noqa: BLE001 - record then re-raise (preserve caller handling)
        record_fetch_event(
            provider=provider,
            endpoint=endpoint,
            kind=kind,
            http_method=method,
            request_params=params,
            http_status=None,
            outcome=ERROR,
            latency_ms=int((time.monotonic() - start) * 1000),
            error=f"{type(exc).__name__}: {exc}",
        )
        raise

    status = getattr(resp, "status_code", None)
    outcome = HIT if (status is not None and 200 <= status < 300) else MISS
    summary = None
    if result_summary_fn is not None:
        try:
            summary = result_summary_fn(resp)
        except Exception:  # noqa: BLE001 - summary is best-effort
            summary = None
    record_fetch_event(
        provider=provider,
        endpoint=endpoint,
        kind=kind,
        http_method=method,
        request_params=params,
        http_status=status,
        outcome=outcome,
        result_summary=summary,
        latency_ms=int((time.monotonic() - start) * 1000),
    )
    return resp

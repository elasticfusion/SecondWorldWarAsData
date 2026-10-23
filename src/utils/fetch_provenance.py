"""Universal fetch-provenance primitive — every external fetch becomes a traceable event.

Principle (owner): "I queried source X at time T with params P and got result R (status S,
outcome O)." If an API misbehaves you can walk backwards from the stored record. A positive
*textual* find is additionally enqueued for human review and, on affirm, flows through the SAME
path into ingestion/extraction like an uploaded book (handled by callers; this module records).

Design: docs/current/FETCH_PROVENANCE_DESIGN.md. This is operational TELEMETRY, not a
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
TEXTUAL = "textual"  # wayback / award / bio — review-eligible

# review_status enum (textual only)
PENDING = "pending"
AFFIRMED = "affirmed"
REJECTED = "rejected"

# ENQUEUE POLICY (owner-confirmed): which TEXTUAL providers represent a NEW, citable PRIMARY-SOURCE
# document that should be enqueued for human review and, on affirm, promoted into ingestion like an
# uploaded book — vs. an entity-ENRICHMENT lookup that merely attaches a fact/snippet (provenance
# only). The test: "would a historian want this in the bibliography?" Archive/library/government
# sources qualify; encyclopedia blurbs, image-license metadata, and search-result pointers do NOT.
# Narrow + explicit so it is easy to adjust; a provider NOT listed is recorded with no review.
_ENQUEUE_PROVIDERS = frozenset(
    {
        "wayback",  # recovered archived primary document
        "archive-org",  # archive.org item (book/document)
        "archive-solr",  # archive.org advancedsearch hit -> acquirable item
        "gutendex",  # Project Gutenberg full text
        "nara",  # National Archives record (archival primary source)
    }
)


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
        import boto3

        from src.utils.config import load_config

        aws = load_config().get("aws", {})
        if not aws.get("enabled"):
            return None
        name = aws.get("cache_table", "dev-wwii-api-cache")
        region = aws.get("region", "us-east-1")
        return boto3.resource("dynamodb", region_name=region).Table(name)
    except Exception as e:  # noqa: BLE001
        logger.debug("fetch-provenance table unavailable: %s", e)
        return None


def _persist(record: Dict[str, Any]) -> None:
    """Write the fetch-provenance record. Fail-safe: never raise. DynamoDB in AWS mode,
    append-only JSONL locally."""
    try:
        table = _table()
        if table is not None:
            table.put_item(
                Item={
                    "cache_key": f"{_KEY_PREFIX}{record['fetch_event_id']}",
                    "response": json.dumps(record),
                }
            )
            return
        # Local/test: append to output/_fetch_events/<provider>.jsonl
        provider = str(record.get("provider") or "unknown").replace("/", "_")
        _LOCAL_DIR.mkdir(parents=True, exist_ok=True)
        with open(_LOCAL_DIR / f"{provider}.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")
    except Exception as e:  # noqa: BLE001 - recording must never block a fetch
        logger.debug("fetch-provenance persist skipped: %s", e)


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
) -> str:
    """Record a single fetch-provenance event (for callers that already hold a response, e.g.
    wayback's courtesy wrapper). Returns the fetch_event_id. Fail-safe."""
    fetch_event_id = str(ulid.new())
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
    _persist(record)
    return fetch_event_id


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
) -> Optional[requests.Response]:
    """Perform an external fetch through the single choke point, recording a fetch-provenance
    event (queried_at, status, outcome, latency, result summary). Reuses the pooled
    ``http_pool.get_session()`` unless a session is supplied.

    ``result_summary_fn(resp) -> str`` optionally derives a compact result summary (NOT the full
    payload). Fail-safe: a recording error is swallowed; the HTTP response (or the raised transport
    error) is the caller's to handle — on a transport exception we record outcome=error and
    re-raise so callers keep their existing error handling.
    """
    if session is None:
        from src.utils.http_pool import get_session

        session = get_session()

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

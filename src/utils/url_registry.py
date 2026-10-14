"""DynamoDB-backed URL registry (URL_SOURCED_ENRICHMENT_DESIGN.md).

A cross-source (Grok + OpenSERP) memory of URLs we have EXAMINED for an entity, with
the relevance verdict and when. Subsequent searches consult it so a URL is not
re-fetched / re-evaluated within the retention window (90 days, owner-confirmed);
after that it is eligible for re-examination.

Trust model: Grok FACTS are untrusted; a cited URL that Grok EVALUATES as relevant
(to the specific entity + WWII) earns some trust and is recorded here with provenance.

Every URL is run through ``url_hygiene.clean_url`` FIRST — a URL that is not clean
(malformed, non-http(s), SSRF-unsafe host, over-long) is REJECTED and never stored or
looked up. The registry key is ``url#{sha256(clean_url)}`` so the SAME resource (after
normalization) maps to one entry.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from dataclasses import dataclass
from typing import Optional

from src.utils.url_hygiene import clean_url

logger = logging.getLogger(__name__)

# Owner-confirmed retention: reuse a known URL verdict for 90 days; re-examine after.
RETENTION_DAYS = 90
_KEY_PREFIX = "url#"


@dataclass
class UrlRecord:
    url: str  # the CLEAN (normalized) url
    relevant: bool  # Grok's relevance verdict (entity + WWII)
    context: str  # what it was evaluated against (e.g. "First U.S. Army | WWII")
    source: str  # "grok" | "openserp"
    examined_at: int  # epoch seconds


def _key(clean: str) -> str:
    return f"{_KEY_PREFIX}{hashlib.sha256(clean.encode('utf-8')).hexdigest()}"


def _table():
    """DynamoDB table handle (dev-wwii-api-cache), or None if unavailable (local/test)."""
    try:
        import boto3

        from src.utils.config import load_config

        aws = load_config().get("aws", {})
        if not aws.get("enabled"):
            return None
        name = aws.get("cache_table", "dev-wwii-api-cache")
        region = aws.get("region", "us-east-1")
        return boto3.resource("dynamodb", region_name=region).Table(name)
    except Exception as e:  # noqa: BLE001 - no table -> caller treats as "not seen"
        logger.debug("URL registry table unavailable: %s", e)
        return None


def lookup(url: str) -> Optional[UrlRecord]:
    """Return a non-expired UrlRecord for ``url`` if we examined it within the
    retention window, else None. Returns None for a non-clean URL (never stored)."""
    clean = clean_url(url)
    if not clean:
        return None
    table = _table()
    if table is None:
        return None
    try:
        item = table.get_item(Key={"cache_key": _key(clean)}).get("Item")
    except Exception as e:  # noqa: BLE001
        logger.debug("URL registry lookup failed: %s", e)
        return None
    if not item:
        return None
    ttl = int(item.get("ttl", 0) or 0)
    if ttl and int(time.time()) > ttl:
        return None  # expired -> treat as not seen (eligible for re-examination)
    try:
        payload = json.loads(item.get("response", "{}"))
    except Exception:  # noqa: BLE001
        return None
    return UrlRecord(
        url=payload.get("url", clean),
        relevant=bool(payload.get("relevant", False)),
        context=payload.get("context", ""),
        source=payload.get("source", ""),
        examined_at=int(payload.get("examined_at", 0) or 0),
    )


def register(url: str, relevant: bool, context: str, source: str) -> Optional[str]:
    """Record a URL + its relevance verdict with a RETENTION_DAYS TTL. The URL is
    validated + normalized first; a non-clean URL is REJECTED (returns None, nothing
    stored). Returns the clean URL on success, None on reject/failure."""
    clean = clean_url(url)
    if not clean:
        logger.info("URL registry: rejected non-clean url %r", (url or "")[:120])
        return None
    table = _table()
    if table is None:
        return clean  # local/test: validation still enforced, just not persisted
    now = int(time.time())
    payload = {
        "url": clean,
        "relevant": bool(relevant),
        # Bound stored strings — context is Grok-supplied; keep the item well under the
        # DynamoDB 400KB cap and avoid storing unbounded prose.
        "context": (context or "")[:500],
        "source": (source or "")[:50],
        "examined_at": now,
    }
    try:
        table.put_item(
            Item={
                "cache_key": _key(clean),
                "response": json.dumps(payload),
                "ttl": now + RETENTION_DAYS * 86400,
            }
        )
    except Exception as e:  # noqa: BLE001 - best effort; never crash enrichment
        logger.warning("URL registry write failed for %s: %s", clean, e)
        return None
    return clean


def should_examine(url: str) -> bool:
    """True if a (clean) URL has NOT been examined within the retention window and so
    should be fetched + Grok-evaluated now. A non-clean URL returns False (never
    examined — it was rejected at the hygiene gate)."""
    if not clean_url(url):
        return False
    return lookup(url) is None

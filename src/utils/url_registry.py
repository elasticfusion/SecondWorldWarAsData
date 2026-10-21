"""DynamoDB-backed URL registry + per-domain politeness gate — the coordination core
of the centralized URL-sourced enrichment (URL_SOURCED_ENRICHMENT_DESIGN.md).

ONE central path (invoked by both Phase 2 and Phase 3) consults this to make a
STATE-BASED go/no-go decision per URL, using two stored timestamps:

  1. URL REGISTRY entry  `url#{sha256(clean_url)}`  — status in:
       in_progress : a task CLAIMED it and is fetching now (atomic claim; a stale
                     claim older than CLAIM_STALE_SECONDS is reclaimable — a crashed
                     claimant must not strand the URL).
       searched    : done; verdict stored; 90-day TTL (re-examinable after expiry).
       dead        : permanently gone (>= DEAD_DNS_FAILURES DNS/unreachable failures at
                     ~90-day increments AND no Archive.org snapshot). NO TTL — never
                     re-searched (the 90-day rule does NOT apply to `dead`).
  2. PER-DOMAIN gate     `fetchgate#{domain}.last_query_at` — politeness: a domain
       queried within POLITENESS_SECONDS is in cooldown -> NO-GO (defer). Parallel
       Phase 2/3 tasks naturally serialize per domain (whoever stamped last wins).

Trust model: Grok FACTS are untrusted; a cited URL Grok EVALUATES (against fetched
content) earns some trust. Relevance is per-(URL, entity) (#4 hybrid): fetch state is
URL-global, but the relevance verdict is keyed on (url, entity) so one entity's verdict
is never reused for another.

Every URL is run through url_hygiene.clean_url FIRST; a non-clean URL is rejected.
Best-effort + fail-safe throughout: never crash enrichment; on uncertainty, allow a
re-examination rather than silently skip.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from dataclasses import dataclass
from typing import Optional, Tuple

from src.utils.url_hygiene import clean_url

logger = logging.getLogger(__name__)

RETENTION_DAYS = 90  # `searched` TTL (owner-confirmed)
POLITENESS_SECONDS = int(1)  # min interval between same-domain queries
CLAIM_STALE_SECONDS = int(
    10 * 60
)  # an in_progress claim older than this is reclaimable
DEAD_DNS_FAILURES = 4  # failures (at ~90d increments) -> dead (#9)

_KEY_PREFIX = "url#"
_GATE_PREFIX = "fetchgate#"

# go/no-go actions returned by decide()
GO = "go"  # claim + fetch + evaluate now
SKIP_KNOWN = "skip_known"  # already searched/dead/irrelevant -> reuse stored verdict
DEFER_DOMAIN = "defer_domain"  # domain in cooldown -> caller retries later
DEFER_INPROGRESS = (
    "defer_inprogress"  # another task is fetching it now -> caller retries later
)


@dataclass
class UrlRecord:
    url: str
    status: str  # in_progress | searched | dead
    relevant: bool
    context: str
    source: str
    examined_at: int
    claimed_at: int = 0
    dns_failures: int = 0
    first_wayback_ts: str = (
        ""  # earliest Wayback capture ts we recorded (archive-stable check #9)
    )
    http_status: Optional[int] = None  # last live/terminal HTTP code (future filtering)


def _url_key(clean: str) -> str:
    return f"{_KEY_PREFIX}{hashlib.sha256(clean.encode('utf-8')).hexdigest()}"


def _domain_key(clean: str) -> str:
    from urllib.parse import urlparse

    host = (urlparse(clean).hostname or "").lower()
    return f"{_GATE_PREFIX}{host}"


def _table():
    """DynamoDB table handle (dev-wwii-api-cache), or None (local/test)."""
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
        logger.debug("URL registry table unavailable: %s", e)
        return None


def _load_raw(table, clean: str) -> Tuple[Optional[UrlRecord], str]:
    """Read the registry record ONCE; return (parsed record | None, the exact raw
    'response' string observed). The raw string is used as the optimistic-lock
    condition in claim() so staleness is judged on the SAME bytes we lock on (B1)."""
    try:
        item = table.get_item(Key={"cache_key": _url_key(clean)}).get("Item")
    except Exception as e:  # noqa: BLE001
        logger.debug("URL registry read failed: %s", e)
        return None, ""
    if not item:
        return None, ""
    raw = item.get("response", "")
    try:
        p = json.loads(raw or "{}")
    except Exception:  # noqa: BLE001
        return None, raw
    return (
        UrlRecord(
            url=p.get("url", clean),
            status=p.get("status", "searched"),
            relevant=bool(p.get("relevant", False)),
            context=p.get("context", ""),
            source=p.get("source", ""),
            examined_at=int(p.get("examined_at", 0) or 0),
            claimed_at=int(p.get("claimed_at", 0) or 0),
            dns_failures=int(p.get("dns_failures", 0) or 0),
            first_wayback_ts=p.get("first_wayback_ts", ""),
            http_status=p.get("http_status"),
        ),
        raw,
    )


def _load(table, clean: str) -> Optional[UrlRecord]:
    """Read + parse the registry record for a clean URL (None if absent)."""
    return _load_raw(table, clean)[0]


def _is_live_record(rec: UrlRecord, now: int) -> bool:
    """Does this record currently suppress a fresh search?"""
    if rec.status == "dead":
        return True  # permanent, no TTL
    if rec.status == "searched":
        return (now - rec.examined_at) < RETENTION_DAYS * 86400
    if rec.status == "in_progress":
        return (now - rec.claimed_at) < CLAIM_STALE_SECONDS  # stale claim is NOT live
    return False


def _domain_in_cooldown(table, clean: str, now: int) -> bool:
    try:
        item = table.get_item(Key={"cache_key": _domain_key(clean)}).get("Item")
    except Exception:  # noqa: BLE001
        return False
    if not item:
        return False
    last = int(item.get("last_query_at", 0) or 0)
    return (now - last) < POLITENESS_SECONDS


def decide(url: str) -> Tuple[str, Optional[UrlRecord]]:
    """The central go/no-go. Returns (action, record):
      GO               -> caller should claim() then fetch+evaluate.
      SKIP_KNOWN       -> already searched/dead; reuse record's verdict (record given).
      DEFER_INPROGRESS -> another task is fetching it now; caller retries later.
      DEFER_DOMAIN     -> domain in politeness cooldown; caller retries later.
    Fail-safe: a non-clean URL is SKIP_KNOWN (never fetched); any error -> GO (allow).
    """
    clean = clean_url(url)
    if not clean:
        return SKIP_KNOWN, None
    table = _table()
    if table is None:
        return GO, None  # local/test: no coordination, just proceed
    now = int(time.time())
    rec = _load(table, clean)
    if rec and _is_live_record(rec, now):
        if rec.status == "in_progress":
            return DEFER_INPROGRESS, rec
        return SKIP_KNOWN, rec  # searched (fresh) or dead
    if _domain_in_cooldown(table, clean, now):
        return DEFER_DOMAIN, rec
    return GO, rec


def claim(url: str) -> bool:
    """Atomically claim a URL as in_progress before fetching (closes the concurrency
    race #1). Stamps the per-domain last_query_at (reserves the polite slot #8).
    Returns True if we won the claim (proceed to fetch), False if a LIVE claim/record
    already exists (another task owns it, or it's searched/dead). A STALE in_progress
    (crashed claimant) is reclaimable. Local/test (no table) -> True."""
    clean = clean_url(url)
    if not clean:
        return False
    table = _table()
    if table is None:
        return True
    now = int(time.time())
    claim_payload = json.dumps(
        {"url": clean, "status": "in_progress", "claimed_at": now, "examined_at": 0}
    )
    try:
        # Win iff no record exists at all.
        table.put_item(
            Item={"cache_key": _url_key(clean), "response": claim_payload},
            ConditionExpression="attribute_not_exists(cache_key)",
        )
        _stamp_domain(table, clean, now)
        return True
    except Exception as e:  # noqa: BLE001
        if not _is_conditional_failure(e):
            # transient (throttle/5xx/timeout) — NOT a lost claim; do not skip silently
            logger.debug("claim put failed (transient) for %s: %s", clean, e)
            return False
        # ConditionalCheckFailed -> record exists -> fall through to stale-reclaim
    # Record exists — reclaim ONLY if it's a stale in_progress (crashed claimant).
    # B1: key the optimistic lock on the EXACT bytes we observed when judging staleness
    # (one read, threaded through) — re-reading would reopen the double-win race.
    rec, observed = _load_raw(table, clean)
    if (
        rec
        and rec.status == "in_progress"
        and (now - rec.claimed_at) >= CLAIM_STALE_SECONDS
    ):
        try:
            table.put_item(
                Item={"cache_key": _url_key(clean), "response": claim_payload},
                ConditionExpression="#r = :old",
                ExpressionAttributeNames={"#r": "response"},
                ExpressionAttributeValues={":old": observed},
            )
            _stamp_domain(table, clean, now)
            logger.info("Reclaimed stale in_progress URL claim: %s", clean)
            return True
        except (
            Exception
        ):  # noqa: BLE001 - another task reclaimed first (condition failed)
            return False
    return False


def _is_conditional_failure(e: Exception) -> bool:
    """True iff the exception is a DynamoDB ConditionalCheckFailed (record already
    exists / optimistic-lock mismatch) — distinguished from a transient error so a
    throttle/5xx is never misread as 'claim lost'. Typed-first (botocore ClientError
    error Code), name-fallback for test doubles."""
    code = ""
    try:
        code = getattr(e, "response", {}).get("Error", {}).get("Code", "")
    except Exception:  # noqa: BLE001
        code = ""
    return code == "ConditionalCheckFailedException" or (
        "ConditionalCheckFailed" in type(e).__name__
    )


def _stamp_domain(table, clean: str, now: int) -> None:
    try:
        table.put_item(
            Item={
                "cache_key": _domain_key(clean),
                "last_query_at": now,
                "ttl": now + 86400,  # the gate itself can expire in a day
            }
        )
    except Exception as e:  # noqa: BLE001
        logger.debug("fetchgate stamp failed: %s", e)


def finalize_searched(
    url: str,
    relevant: bool,
    context: str,
    source: str,
    first_wayback_ts: str = "",
    http_status: Optional[int] = None,
) -> Optional[str]:
    """Finalize a claimed URL as `searched` (90-day TTL) AFTER the result write
    confirms (#2 — searched always implies data landed). Captures http_status for
    future filter decisions (e.g. report only 200s, flag 403s). Returns the clean URL.
    """
    clean = clean_url(url)
    if not clean:
        return None
    table = _table()
    if table is None:
        return clean
    now = int(time.time())
    payload = {
        "url": clean,
        "status": "searched",
        "relevant": bool(relevant),
        "context": (context or "")[:500],
        "source": (source or "")[:50],
        "examined_at": now,
        "first_wayback_ts": (first_wayback_ts or "")[:32],
        "http_status": int(http_status) if http_status is not None else None,
    }
    try:
        table.put_item(
            Item={
                "cache_key": _url_key(clean),
                "response": json.dumps(payload),
                "ttl": now + RETENTION_DAYS * 86400,
            }
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("finalize_searched failed for %s: %s", clean, e)
        return None
    return clean


def should_fetch_content(url: str) -> bool:
    """Lightweight cross-entity / cross-phase dedup gate for first-party CONTENT fetches
    (Grokipedia Playwright render, Wikipedia API) that don't go through url_fetch.fetch().

    Returns False only when the URL was ALREADY searched (or is dead) within the 90-day
    window (decide() -> SKIP_KNOWN) — so e.g. two UNMERGED "George Patton" person records
    don't each re-render the same Grokipedia page; the second is skipped. Returns True on
    GO or any DEFER/error (fail-safe: prefer a fetch over silently dropping enrichment —
    DEFER politeness cooldown does not apply to these stable first-party endpoints).

    Callers that fetch should call finalize_searched(url, ...) afterwards so the next
    duplicate is skipped."""
    try:
        action, _rec = decide(url)
        return action != SKIP_KNOWN
    except Exception:  # noqa: BLE001 - never block enrichment
        return True


def record_dns_failure(url: str) -> str:
    """Record one DNS/unreachable failure for a URL (#3/#9). Does NOT mark dead on its
    own; increments a counter. Returns the current status: 'dead' once failures reach
    DEAD_DNS_FAILURES AND no Wayback snapshot was ever recorded, else 'retry' (transient
    — ignored, no permanent verdict). A single failure never marks dead."""
    clean = clean_url(url)
    if not clean:
        return "retry"
    table = _table()
    if table is None:
        return "retry"
    now = int(time.time())
    rec = _load(table, clean)
    # I2: this read-modify-write of the counter is NOT atomic across parallel tasks.
    # Accepted: an undercount only makes `dead` HARDER to reach (never a false dead) —
    # the safe direction. A single/transient failure never marks dead regardless.
    failures = (rec.dns_failures if rec else 0) + 1
    had_archive = bool(rec and rec.first_wayback_ts)
    if failures >= DEAD_DNS_FAILURES and not had_archive:
        payload = {
            "url": clean,
            "status": "dead",
            "relevant": False,
            "context": (rec.context if rec else ""),
            "source": (rec.source if rec else ""),
            "examined_at": now,
            "dns_failures": failures,
        }
        try:
            # DEAD has NO ttl attribute -> DynamoDB native TTL never deletes it (permanent).
            table.put_item(
                Item={"cache_key": _url_key(clean), "response": json.dumps(payload)}
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("mark-dead failed for %s: %s", clean, e)
        return "dead"
    # Not yet dead: persist the incremented counter (no terminal verdict).
    _persist_dns_counter(table, clean, rec, failures, now)
    return "retry"


def _persist_dns_counter(table, clean, rec, failures, now) -> None:
    """Persist the incremented DNS-failure counter WITHOUT a terminal verdict. Skips the write
    while the record is in_progress (I3: rewriting would drop claimed_at and surrender a
    claimant's lock mid-fetch — the failure is a transient the fetching task handles).
    """
    if rec and rec.status == "in_progress":
        return
    payload = _dns_counter_payload(clean, rec, failures, now)
    try:
        item: dict = {"cache_key": _url_key(clean), "response": json.dumps(payload)}
        if rec and rec.status == "searched" and rec.examined_at:
            item["ttl"] = rec.examined_at + RETENTION_DAYS * 86400
        table.put_item(Item=item)
    except Exception as e:  # noqa: BLE001
        logger.debug("dns-failure counter write failed for %s: %s", clean, e)


def _dns_counter_payload(clean, rec, failures, now) -> dict:
    """Build the persisted payload for a DNS-failure counter write (no terminal verdict).
    Falls back to searched/empty defaults when there is no existing record. Preserves exactly.
    """
    return {
        "url": clean,
        "status": (rec.status if rec else "searched"),
        "relevant": bool(rec.relevant) if rec else False,
        "context": (rec.context if rec else ""),
        "source": (rec.source if rec else ""),
        "examined_at": (rec.examined_at if rec else now),
        "dns_failures": failures,
        "first_wayback_ts": (rec.first_wayback_ts if rec else ""),
    }


# --- backward-compatible wrappers (pre-state-model callers / tests) ---


def lookup(url: str) -> Optional[UrlRecord]:
    """Return a non-expired record (searched within TTL, or dead), else None."""
    clean = clean_url(url)
    if not clean:
        return None
    table = _table()
    if table is None:
        return None
    rec = _load(table, clean)
    if rec and _is_live_record(rec, int(time.time())) and rec.status != "in_progress":
        return rec
    return None


def register(url: str, relevant: bool, context: str, source: str) -> Optional[str]:
    """Compat shim: finalize a URL as searched directly (no explicit claim). Rejects
    non-clean URLs. Prefer claim()+finalize_searched() in the central path."""
    return finalize_searched(url, relevant, context, source)


def should_examine(url: str) -> bool:
    """True if a clean URL is not currently suppressed (not searched-within-TTL, not
    dead). A non-clean URL returns False (rejected at the hygiene gate)."""
    if not clean_url(url):
        return False
    return lookup(url) is None

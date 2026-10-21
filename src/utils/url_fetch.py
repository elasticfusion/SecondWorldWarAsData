"""Central URL fetch — the single path every pipeline URL GET flows through
(URL_SOURCED_ENRICHMENT_DESIGN.md). Invoked by both Phase 2 and Phase 3 (in-process
now; wrappable as the central Lambda per the locked architecture).

Responsibilities (the mechanical half; Grok content-verdict + write are the caller's):
  1. decide() go/no-go against the DynamoDB registry + per-domain politeness gate.
  2. On GO: claim the URL (in_progress), then fetch the CONTENT:
       - MANUAL per-hop redirect follow, re-running url_hygiene.clean_url (SSRF/scheme/
         host check) on EVERY Location before following it (#5 — redirects reopen SSRF;
         a clean cited URL can 302 -> 169.254.169.254). Hop cap; loop/excess -> fail.
       - Honest User-Agent; honor Retry-After / 429 / 503 backoff.
       - On 404/410, transient error, or an HTTP-200 bot-block/CAPTCHA page
         (wayback.is_block_page): FAIL OUT to the Archive.org Wayback snapshot
         (#3 "ignore transients but again fail out to archive.org").
       - If live is a confirmed-gone 404/410 AND Wayback has no snapshot -> count a
         failure toward `dead` (record_dns_failure; dead only after >=4 @90d — #9).
  3. Returns a FetchResult; the CALLER renders the Grok content-verdict, writes the
     result, THEN calls url_registry.finalize_searched (finalize-after-write #2).

Per-domain politeness (#8) is enforced by decide()/claim() via fetchgate#{domain} — this
module does not sleep. Best-effort + fail-safe: never raise into enrichment.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import urljoin, urlparse

from src.utils import url_registry
from src.utils.url_hygiene import clean_url, resolved_host_is_safe

logger = logging.getLogger(__name__)

_USER_AGENT = (
    "SecondWorldWarAsData/1.0 (WWII historical data pipeline; "
    "contact: dchristian@cirrusnine.com)"
)
_MAX_REDIRECT_HOPS = 5
_DEFAULT_TIMEOUT = 20
_MAX_BYTES = 5 * 1024 * 1024  # cap response body (DoS / decompression-bomb guard)

# FetchResult.source values
LIVE = "live"
WAYBACK = "wayback"
DEAD = "dead"
DEFERRED = "deferred"  # registry said defer (in_progress elsewhere / domain cooldown)
SKIP = "skip"  # already searched/dead -> reuse the stored verdict
BLOCKED_NO_ARCHIVE = "blocked_no_archive"  # walled + no snapshot (retryable, not dead)
RATE_LIMITED = "rate_limited"  # 429/503 + no archive (retryable, never -> dead)


@dataclass
class FetchResult:
    url: str  # the cited (clean) url
    source: str  # LIVE | WAYBACK | DEAD | DEFERRED | SKIP | BLOCKED_NO_ARCHIVE
    content: Optional[str] = None  # fetched HTML (live or archived), if any
    final_url: Optional[str] = (
        None  # post-redirect resolved clean url (LIVE) — the dedup identity
    )
    archived_url: Optional[str] = None  # Wayback snapshot url (WAYBACK)
    wayback_ts: Optional[str] = None  # Wayback capture timestamp (WAYBACK)
    http_status: Optional[int] = None  # the live/terminal HTTP code observed (None if
    #                                    never reached: transient / DNS-blocked / skip)
    detail: str = ""  # human-readable reason
    registry_record: object = field(default=None)  # the UrlRecord for SKIP/DEFERRED


def _session():
    import requests

    s = requests.Session()
    s.headers.update({"User-Agent": _USER_AGENT})
    return s


def _one_hop(session, url: str, timeout: int):
    """Fetch ONE hop WITHOUT auto-following redirects, AFTER a DNS-rebinding check:
    resolve the host and require every resolved IP to be public (clean_url only checks
    the host STRING). Streams the body with a byte cap. Returns the Response (body in
    ._wwii_text up to the cap) or None on a transport/SSRF-resolve error."""
    host = (urlparse(url).hostname or "").lower()
    vetted = resolved_host_is_safe(host)
    if vetted is None:
        logger.warning(
            "blocked fetch: host resolves to a non-public/invalid address: %s", host
        )
        return None
    try:
        # Pin the connection to a vetted IP (DNS-rebind defense); per-hop (no auto-redirect).
        from src.utils.url_hygiene import _pinned_get

        resp = _pinned_get(
            url,
            host,
            vetted,
            timeout=timeout,
            headers=dict(session.headers),
        )
    except Exception as e:  # noqa: BLE001 - transport error = transient/unreachable
        logger.debug("live hop error for %s: %s", url, e)
        return None
    if not (resp.is_redirect or resp.status_code in (301, 302, 303, 307, 308)):
        resp._wwii_text = _read_hop_text(resp, url)
    try:
        resp.close()
    except Exception:  # noqa: BLE001
        pass
    return resp


def _read_hop_text(resp, url: str) -> str:
    """Stream a terminal hop's body with a hard byte cap; decode to text. Fail-safe to ''."""
    try:
        body = b""
        for chunk in resp.iter_content(8192):
            body += chunk
            if len(body) > _MAX_BYTES:
                logger.warning(
                    "response exceeded %d bytes, truncating: %s", _MAX_BYTES, url
                )
                break
        return body.decode(resp.encoding or "utf-8", "replace")
    except Exception as e:  # noqa: BLE001
        logger.debug("body read error for %s: %s", url, e)
        return ""


def _hop_text(resp) -> str:
    return getattr(resp, "_wwii_text", "") or ""


def _follow_live(session, clean: str, timeout: int):
    """MANUAL per-hop redirect follow with SSRF re-check (host string + RESOLVED IP) on
    every hop. Returns (response | None, final_clean_url | None, outcome) where outcome:
      'ok'           terminal response reached safely
      'gone'         404/410 (authoritatively gone — counts toward dead)
      'rate_limited' 429/503 (honor Retry-After — NEVER counts toward dead)
      'transient'    transport error / loop / excess hops / unsafe hop (NEVER -> dead)
    """
    current = clean
    for _hop in range(_MAX_REDIRECT_HOPS + 1):
        resp = _one_hop(session, current, timeout)
        if resp is None:
            return None, None, "transient", None
        if resp.status_code in (404, 410):
            return None, None, "gone", resp.status_code
        if resp.status_code in (429, 503):
            return resp, current, "rate_limited", resp.status_code
        if resp.is_redirect or resp.status_code in (301, 302, 303, 307, 308):
            loc = resp.headers.get("Location")
            if not loc:
                return resp, current, "ok", resp.status_code
            nxt_clean = clean_url(urljoin(current, loc))  # SSRF/scheme/host re-check
            if not nxt_clean:
                logger.warning(
                    "unsafe/invalid redirect target blocked: %s -> %s", current, loc
                )
                return None, None, "transient", resp.status_code
            current = nxt_clean
            continue
        return resp, current, "ok", resp.status_code
    logger.warning("redirect hop cap exceeded starting at %s", clean)
    return None, None, "transient", None


def fetch(url: str, timeout: int = _DEFAULT_TIMEOUT) -> FetchResult:
    """Central fetch. Honors the registry go/no-go, claims, fetches (SSRF-safe
    redirects, live->Wayback), and classifies. Does NOT render the Grok verdict or
    write — the caller does, then calls url_registry.finalize_searched. Never raises."""
    clean = clean_url(url)
    if not clean:
        return FetchResult(url=url or "", source=SKIP, detail="non-clean url")

    action, rec = url_registry.decide(clean)
    if action == url_registry.SKIP_KNOWN:
        return FetchResult(
            url=clean, source=SKIP, detail="already searched/dead", registry_record=rec
        )
    if action in (url_registry.DEFER_INPROGRESS, url_registry.DEFER_DOMAIN):
        return FetchResult(
            url=clean, source=DEFERRED, detail=action, registry_record=rec
        )

    # GO: claim before fetching (atomic; stamps the per-domain fetchgate).
    if not url_registry.claim(clean):
        # Lost the claim to a concurrent task -> defer.
        return FetchResult(url=clean, source=DEFERRED, detail="claim lost")

    try:
        return _do_fetch(clean, timeout)
    except Exception as e:  # noqa: BLE001 - never raise into enrichment
        logger.error("central fetch unexpected error for %s: %s", clean, e)
        return FetchResult(url=clean, source=DEFERRED, detail=f"error:{e}")


def _do_fetch(clean: str, timeout: int) -> FetchResult:
    # RADON-INHERENT (C18): an irreducible outcome-classification state machine — live-ok / block /
    # gone / rate-limited / transient, each crossed with archive-hit/miss, producing a distinct
    # FetchResult + registry side-effect. The branches must stay co-located to reason about the
    # live->wayback->dead fallthrough ordering; extracting them would scatter one cohesive decision.
    from src.enrichment import wayback

    session = _session()
    resp, final_url, outcome, code = _follow_live(session, clean, timeout)
    # Live success + not a block page -> use it.
    text = _hop_text(resp) if resp is not None else ""
    if outcome == "ok" and resp is not None and resp.status_code == 200 and text:
        if not wayback.is_block_page(text, resp.status_code):
            return FetchResult(
                url=clean,
                source=LIVE,
                content=text,
                final_url=final_url or clean,
                http_status=code,
                detail="live 200",
            )
        detail = (
            "live block page (CAPTCHA/WAF)"  # 200 but walled -> fall out to Wayback
        )
    elif outcome == "gone":
        detail = "live 404/410"
    elif outcome == "rate_limited":
        detail = "live 429/503"
    else:
        detail = "live transient/unreachable"

    # Fail out to Archive.org (always try — #3 "ignore transients but fail out to archive").
    archived = wayback.fetch_archived(clean, timeout=max(timeout, 20))
    if archived is not None:
        html, archived_url, ts = archived
        return FetchResult(
            url=clean,
            source=WAYBACK,
            content=html,
            archived_url=archived_url,
            wayback_ts=ts,
            http_status=code,
            detail=f"wayback ({detail})",
        )

    # No live, no archive. Only CONFIRMED-GONE counts toward `dead` (#3/#9) — a
    # transient / rate-limit / unsafe-redirect-blocked NEVER ages a URL toward dead.
    if outcome == "gone":
        status = url_registry.record_dns_failure(
            clean
        )  # dead only >=4 @90d + no archive
        return FetchResult(
            url=clean,
            source=(DEAD if status == "dead" else BLOCKED_NO_ARCHIVE),
            http_status=code,
            detail=f"gone + no archive ({status})",
        )
    if outcome == "rate_limited":
        retry_after = ""
        try:
            retry_after = resp.headers.get("Retry-After", "") if resp else ""
        except Exception:  # noqa: BLE001
            retry_after = ""
        return FetchResult(
            url=clean,
            source=RATE_LIMITED,
            http_status=code,
            detail=f"rate-limited (Retry-After={retry_after or 'n/a'}) + no archive",
        )
    if detail.startswith("live block"):
        # walled + no snapshot -> retryable (the wall may lift / a snapshot may appear).
        return FetchResult(
            url=clean,
            source=BLOCKED_NO_ARCHIVE,
            http_status=code,
            detail="walled + no archive",
        )
    # transient + no archive -> IGNORE: no verdict recorded, retried next run (#3).
    return FetchResult(
        url=clean,
        source=BLOCKED_NO_ARCHIVE,
        http_status=code,
        detail="transient + no archive (ignored)",
    )

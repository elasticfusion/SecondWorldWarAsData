"""Relevancy gate for outbound document links before human-review enqueue (M4, step 3b).

Owner policy (2026-10-27): every outbound link in a parsed doc is enqueued for HUMAN review rather
than auto-ingested. To keep that queue signal-rich we gate on relevancy first:

  * a TRUSTED source (``trusted_sources`` in config.yaml, read through ``src/utils/link_policy`` -
    HyperWar volumes and NARA records; task 1.9 replaced the wider ``link_relevancy_whitelist``,
    which trusted all of ibiblio.org) -> skip the model call, enqueue;
  * domain on the license-rejection ``blacklist`` -> DROP (never enqueue, never fetch);
  * otherwise -> ask Grok a one-word relevancy verdict; relevant -> enqueue, irrelevant -> drop.

Fail-safe default is ENQUEUE: a model/config error must not silently discard a citable link — let a
human decide. No network I/O here beyond the (optional) Grok call; the link itself is never fetched.
"""

from __future__ import annotations

import logging
from typing import Any, List, Optional
from urllib.parse import urlparse
from src.grok_client import BatchModeCollecting

logger = logging.getLogger(__name__)


def _host_matches(url: str, domains: List[str]) -> bool:
    """True if ``url``'s host equals or is a subdomain of any entry (entries may carry a path
    fragment, which is ignored for the host comparison). Mirrors source_retrieval.is_blacklisted.
    """
    try:
        host = (urlparse(url).hostname or "").lower()
    except Exception:  # noqa: BLE001
        host = ""
    if not host:
        return False
    for entry in domains:
        dom = entry.split("/", 1)[0].strip().lower()
        if dom and (host == dom or host.endswith("." + dom)):
            return True
    return False


def is_whitelisted(url: str, whitelist: Optional[List[str]] = None) -> bool:
    """True if the link is to a trusted source (skip Grok): ``whitelist`` (host or subdomain
    match) when given, else the configured trusted sources (host AND path prefix)."""
    if whitelist is not None:
        return _host_matches(url, whitelist)
    from src.utils.link_policy import trust_basis

    return trust_basis(url) is not None


def _grok_link_relevant(grok_client: Any, text: str, url: str, context: str) -> bool:
    """Ask Grok whether a cited link is relevant to a WWII primary-source corpus. One-word verdict;
    fail-safe -> True (enqueue when unsure, so a human still decides)."""
    try:
        prompt = (
            "You are curating a World War II primary-source history corpus. A document links to "
            "the resource below. Would a WWII historian consider this link a relevant primary/"
            "reference source worth reviewing (not an ad, nav/boilerplate, or unrelated site)?\n"
            f"Link text: {text!r}\nURL: {url}\nContext: {context[:400]!r}\n"
            "Answer with exactly one word: YES, NO, or UNKNOWN."
        )
        from src.utils.grok_choice import ask_choice, can_reask, evictor

        choice = ask_choice(
            lambda: grok_client.chat_completion(prompt),
            ("YES", "NO", "UNKNOWN"),
            evictor(grok_client, prompt),
            what="link_relevancy",
            reask=can_reask(grok_client),
        )
        return choice != "NO"  # YES, UNKNOWN or unusable -> enqueue (safe default)
    except BatchModeCollecting:
        raise  # queued for the batch: not an answer, not a failure
    except Exception as e:  # noqa: BLE001
        logger.warning(
            "OBSERV link_asset.grok_error reason=%s (fail-safe=enqueue)", str(e)[:120]
        )
        return True


def should_enqueue_link(
    url: str,
    text: str,
    *,
    context: str = "",
    grok_client: Any = None,
    whitelist: Optional[List[str]] = None,
    blacklist: Optional[List[str]] = None,
) -> tuple:
    """Relevancy verdict for one link. Returns (enqueue: bool, verdict: str) where verdict is one of
    ``whitelisted`` | ``relevant`` | ``irrelevant`` | ``blacklisted``. Fail-safe (never raises).
    """
    try:
        if blacklist and _host_matches(url, blacklist):
            return (False, "blacklisted")
        if is_whitelisted(url, whitelist):
            return (True, "whitelisted")
        if grok_client is None:
            return (True, "relevant")  # no model available -> let a human decide
        return (
            (True, "relevant")
            if _grok_link_relevant(grok_client, text, url, context)
            else (
                False,
                "irrelevant",
            )
        )
    except BatchModeCollecting:
        raise  # queued for the batch: not an answer, not a failure
    except Exception:  # noqa: BLE001
        return (True, "relevant")  # fail-safe: when in doubt, enqueue

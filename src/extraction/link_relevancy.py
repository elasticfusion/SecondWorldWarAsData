"""Relevancy gate for outbound document links before human-review enqueue (M4, step 3b).

Owner rule (2026-11-17): Grok judges EVERY link before a human sees it - a trusted source is no
exception (``trusted_sources`` in config.yaml, ``src/utils/link_policy``, only marks the item for the
reviewer). One one-word verdict:

  * domain on the license-rejection ``blacklist`` -> DROP (never queued, never fetched);
  * Grok YES -> queue; Grok NO -> drop;
  * UNKNOWN, an unusable reply, a Grok error or no client -> ``undecided``: NOT queued, and nothing
    is recorded, so a later run asks again. (The old default was to queue "when in doubt": a link
    nobody had judged reached a human.)

No network I/O here beyond the Grok call; the link itself is never fetched.
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
    """True if the link is to a trusted source: ``whitelist`` (host or subdomain match) when given,
    else the configured trusted sources (host AND path prefix). Marks the item for the reviewer; it
    does NOT skip the Grok verdict."""
    if whitelist is not None:
        return _host_matches(url, whitelist)
    from src.utils.link_policy import trust_basis

    return trust_basis(url) is not None


def grok_link_verdict(
    grok_client: Any, text: str, url: str, context: str
) -> Optional[str]:
    """Grok's one-word verdict on a cited link: ``YES``, ``NO``, ``UNKNOWN``, or None (an unusable
    reply twice, or an error). A queued batch request is not a verdict (``BatchModeCollecting``).
    """
    try:
        prompt = (
            "You are curating a World War II primary-source history corpus. A document links to "
            "the resource below. Would a WWII historian consider this link a relevant primary/"
            "reference source worth reviewing (not an ad, nav/boilerplate, or unrelated site)?\n"
            f"Link text: {text!r}\nURL: {url!r}\nContext: {context[:400]!r}\n"
            "Answer with exactly one word: YES, NO, or UNKNOWN."
        )
        from src.utils.grok_choice import ask_choice, can_reask, evictor

        return ask_choice(
            lambda: grok_client.chat_completion(prompt),
            ("YES", "NO", "UNKNOWN"),
            evictor(grok_client, prompt),
            what="link_relevancy",
            reask=can_reask(grok_client),
        )
    except BatchModeCollecting:
        raise  # queued for the batch: not an answer, not a failure
    except Exception as e:  # noqa: BLE001
        logger.warning(
            "OBSERV link_asset.grok_error reason=%s (undecided: not queued, asked again next run)",
            str(e)[:120],
        )
        return None


def should_enqueue_link(
    url: str,
    text: str,
    *,
    context: str = "",
    grok_client: Any = None,
    whitelist: Optional[List[str]] = None,
    blacklist: Optional[List[str]] = None,
) -> tuple:
    """Relevancy verdict for one link: ``(enqueue, verdict)``, verdict one of ``relevant`` |
    ``irrelevant`` | ``blacklisted`` | ``undecided``. Only ``relevant`` is queued. ``whitelist`` is
    kept for callers that name trusted hosts; it no longer skips the Grok question. Never raises
    (but a queued batch request propagates)."""
    try:
        if blacklist and _host_matches(url, blacklist):
            return (False, "blacklisted")
        if grok_client is None:
            return (False, "undecided")  # nobody judged it: nobody is shown it
        verdict = grok_link_verdict(grok_client, text, url, context)
        if verdict == "YES":
            return (True, "relevant")
        if verdict == "NO":
            return (False, "irrelevant")
        return (False, "undecided")
    except BatchModeCollecting:
        raise  # queued for the batch: not an answer, not a failure
    except Exception:  # noqa: BLE001
        return (False, "undecided")

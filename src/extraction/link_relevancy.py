"""Relevancy gate for outbound document links before human-review enqueue (M4, step 3b).

Owner policy (2026-10-27): every outbound link in a parsed doc is enqueued for HUMAN review rather
than auto-ingested. To keep that queue signal-rich we gate on relevancy first:

  * domain on the EXTERNAL, EDITABLE whitelist (``link_relevancy_whitelist`` in
    ``config/domain_blacklist.yaml``, seeded with ``ibiblio.org`` — the corpus host) -> TRUST, skip
    the model call, enqueue;
  * domain on the license-rejection ``blacklist`` -> DROP (never enqueue, never fetch);
  * otherwise -> ask Grok a one-word relevancy verdict; relevant -> enqueue, irrelevant -> drop.

Fail-safe default is ENQUEUE: a model/config error must not silently discard a citable link — let a
human decide. No network I/O here beyond the (optional) Grok call; the link itself is never fetched.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, List, Optional
from urllib.parse import urlparse

import yaml

logger = logging.getLogger(__name__)

_CONFIG = Path("config/domain_blacklist.yaml")


def load_link_relevancy_whitelist(path: Path = _CONFIG) -> List[str]:
    """Load the editable ``link_relevancy_whitelist`` domains (lowercased). Mirrors
    grok_search_maps.load_whitelisted_sites. Fail-safe -> the corpus-host default ``[ibiblio.org]``
    so the relevancy bypass still works if the file is missing/garbled."""
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        wl = [
            str(d).strip().lower()
            for d in (data.get("link_relevancy_whitelist") or [])
            if d
        ]
        return wl or ["ibiblio.org"]
    except Exception as e:  # noqa: BLE001
        logger.warning("link_relevancy_whitelist load failed (%s); using default", e)
        return ["ibiblio.org"]


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
    """True if the link's domain is on the relevancy whitelist (trusted -> skip Grok)."""
    return _host_matches(
        url, whitelist if whitelist is not None else load_link_relevancy_whitelist()
    )


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
        resp = (grok_client.chat_completion(prompt) or "").strip().upper()
        if resp.startswith("NO"):
            return False
        return True  # YES or UNKNOWN -> enqueue (safe default)
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
    except Exception:  # noqa: BLE001
        return (True, "relevant")  # fail-safe: when in doubt, enqueue

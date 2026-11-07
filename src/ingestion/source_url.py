"""Resolve the real source URL of an uploaded document (provenance + relative-asset base).

The parser resolves relative ``img/`` / ``icn/`` / ``maps/`` references against the chapter's
``source_url``. A converted document whose meta carried no real URL silently lost every image and
map (XChannel-8 E2E, 2026-11-06). Resolution order:

1. **S3 object metadata** ``source-url`` (set at upload: ``aws s3 cp ... --metadata
   source-url=<url>``) — authoritative.
2. **HyperWar derivation** — only when the document's ``<title>`` says HyperWar AND the book slug
   follows the HyperWar file convention (``<COUNTRY>-<volume>-<n>``, e.g. ``USA-E-XChannel-8`` ->
   ``https://www.ibiblio.org/hyperwar/USA/USA-E-XChannel/USA-E-XChannel-8.html``). Recorded with
   basis ``derived_hyperwar`` and an actionable warning (confirm it / set the metadata).
3. Otherwise ``""`` (never a fake non-URL string) + an actionable warning.
"""

from __future__ import annotations

import logging
import re
from typing import Tuple

from src.utils.observe import observe_warn

logger = logging.getLogger(__name__)

_HYPERWAR_BASE = "https://www.ibiblio.org/hyperwar"
_HYPERWAR_SLUG = re.compile(
    r"^(?P<country>[A-Z]{2,6})-(?P<vol_rest>[A-Za-z0-9-]+)-(?P<n>\d+)$"
)
_TITLE = re.compile(rb"<title[^>]{0,200}>(.{0,500}?)</title>", re.I | re.S)
_HEAD_BYTES = 4096


def _metadata_url(s3, bucket: str, key: str) -> str:
    try:
        meta = s3.head_object(Bucket=bucket, Key=key).get("Metadata") or {}
    except (
        Exception
    ) as e:  # noqa: BLE001 - absence/transient: fall through to derivation
        logger.debug("source-url metadata lookup failed for %s: %s", key, e)
        return ""
    raw = str(meta.get("source-url") or meta.get("source_url") or "").strip()
    if not raw:
        return ""
    from src.utils.url_hygiene import clean_url

    # The CLEANED form (http(s) only, safe host, fragment/tracking query dropped) — the raw
    # user-supplied value never reaches the meta or the parser base_url.
    return clean_url(raw) or ""


def _is_hyperwar_document(s3, bucket: str, key: str) -> bool:
    try:
        head = s3.get_object(Bucket=bucket, Key=key, Range=f"bytes=0-{_HEAD_BYTES - 1}")
        body = head["Body"].read(_HEAD_BYTES)
    except Exception as e:  # noqa: BLE001
        logger.debug("title probe failed for %s: %s", key, e)
        return False
    m = _TITLE.search(body or b"")
    return bool(m and b"hyperwar" in m.group(1).lower())


def derive_hyperwar_url(book: str) -> str:
    """HyperWar chapter URL from a book slug, or "" when the slug is not HyperWar-shaped."""
    m = _HYPERWAR_SLUG.fullmatch(book or "")
    if not m:
        return ""
    volume = f"{m.group('country')}-{m.group('vol_rest')}"
    url = f"{_HYPERWAR_BASE}/{m.group('country')}/{volume}/{book}.html"
    from src.enrichment.hyperwar_biblio import volume_matches

    return url if volume_matches(url) else ""


_WEB_EXTS = (".html", ".htm", ".xhtml")


def resolve_source_url(s3, bucket: str, key: str, book: str) -> Tuple[str, str]:
    """Return ``(source_url, basis)``; basis is s3_metadata | derived_hyperwar | missing."""
    url = _metadata_url(s3, bucket, key)
    if url:
        return url, "s3_metadata"
    is_web = key.lower().endswith(_WEB_EXTS)
    if not is_web:
        # EPUB/DOCX/TXT carry no web page URL and no web-relative assets: nothing is lost and
        # there is nothing for an operator to do, so this is a record, not an alert.
        logger.info("OBSERV phase0.source_url basis=missing book=%s kind=file", book)
        return "", "missing"
    if _is_hyperwar_document(s3, bucket, key):
        url = derive_hyperwar_url(book)
        if url:
            observe_warn(
                "phase0",
                "source_url_derived",
                note=(
                    "source URL was DERIVED from the HyperWar naming convention; confirm it "
                    "opens the same chapter, and set it explicitly on future uploads with "
                    "--metadata source-url=<url>"
                ),
                book=book,
                source_url=url,
            )
            return url, "derived_hyperwar"
    observe_warn(
        "phase0",
        "source_url_missing",
        note=(
            "no source URL: relative images/maps in this document cannot be resolved and its "
            "provenance has no URL. Re-upload with --metadata source-url=<original page URL>, "
            "then re-run Phase 0 for the book"
        ),
        book=book,
        key=key,
    )
    return "", "missing"

"""The derivation of a book's slug from an S3 key - the only one (owner rule 2026-11-11: a slug is
IMMUTABLE: assigned once at intake, never renamed).

The slug is a book's identity everywhere: the S3 prefixes (``contentrepository/<slug>/``,
``ocr-output/<slug>/``, ``output/content/<slug>/``), DynamoDB keys, the ``--book`` argument,
``BOOK_NAME``, every EventID and the deterministic ``BookID`` (``deterministic_ids.book_id``).

Two keys name a book, by two rules that always agree:

* a SOURCE document (a PDF/EPUB/DOCX/video/image upload, anywhere under ``contentrepository/``)
  is named by its FILE: ``book_slug`` - the filename stem, spaces as ``_``
  (``contentrepository/books/Patton at the Bulge.epub`` -> ``Patton_at_the_Bulge``). Phase 0 then
  writes the book's chapters to ``contentrepository/<slug>/chapterN/...``;
* CHAPTER CONTENT (the ``.md`` Phase 1 parses) is named by its FOLDER: ``content_slug`` -
  ``contentrepository/[<source>/]<slug>/...`` (a leading source segment - ``_system`` or a
  ``user-``/``contrib-``/``org-`` namespace - skipped; a loose file: its stem).

So a source and the chapters made from it get the same slug (tests/test_book_slug.py pins it, and
both rules: changing one re-keys future uploads relative to existing books). The folder a source
was dropped in does not name it.

Both validate: the result must fully match ``[A-Za-z0-9._-]+`` (not '.'/'..'), else ``UNKNOWN`` -
loudly - which callers REJECT, never use (every unnamed upload would otherwise share one book).
Never raises.
"""

from __future__ import annotations

import logging
import re
from typing import List

logger = logging.getLogger(__name__)

UNKNOWN = "unknown"
_SAFE_SLUG_RE = re.compile(r"[A-Za-z0-9._-]+")
_SYSTEM_SOURCE_ID = "_system"
_SOURCE_PREFIXES = ("user-", "contrib-", "org-")


def is_source_segment(segment: str) -> bool:
    """A path segment naming a content SOURCE (not a title)."""
    return segment == _SYSTEM_SOURCE_ID or segment.lower().startswith(_SOURCE_PREFIXES)


def is_safe_slug(slug: str) -> bool:
    return (
        bool(slug) and slug not in (".", "..") and bool(_SAFE_SLUG_RE.fullmatch(slug))
    )


def source_candidate(key: str) -> str:
    """The raw slug of a SOURCE document key: its filename stem, spaces as ``_``."""
    return (key or "").rsplit("/", 1)[-1].rsplit(".", 1)[0].replace(" ", "_")


def content_candidate(key: str) -> str:
    """The raw slug of a CHAPTER CONTENT key: its book folder (see the module docstring)."""
    parts: List[str] = [p for p in (key or "").split("/") if p]
    if parts and parts[0] == "contentrepository":
        rest = parts[1:]
        if rest and is_source_segment(rest[0]):
            rest = rest[1:]
        if len(rest) >= 2:
            return rest[0].replace(" ", "_")
        parts = rest
    return source_candidate("/".join(parts))


def _validated(candidate: str, key: str) -> str:
    if not is_safe_slug(candidate):
        logger.warning(
            "OBSERV book.slug_rejected key=%r (unsafe/untitled -> %r)", key, UNKNOWN
        )
        return UNKNOWN
    return candidate


def book_slug(key: str) -> str:
    """The slug of a SOURCE document key (or a bare book name), or ``UNKNOWN``."""
    try:
        return _validated(source_candidate(key), key)
    except Exception:  # noqa: BLE001 - derivation must never crash the phase
        return _validated("", key)


def content_slug(key: str) -> str:
    """The slug of a CHAPTER CONTENT key, or ``UNKNOWN``."""
    try:
        return _validated(content_candidate(key), key)
    except Exception:  # noqa: BLE001
        return _validated("", key)

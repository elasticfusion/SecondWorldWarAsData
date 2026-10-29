"""Shared, converged book/title slug derivation for the untrusted-upload boundary.

Phase-0's three self-fetching tasks (convert, video, OCR-merge) each derived a
"book" name from an S3 key with their OWN hand-rolled logic (``_book_from_key``),
and the derivations had DIVERGED — a subtle correctness + security hazard, since
that name flows into S3 prefixes, DynamoDB keys, a ``--book`` subprocess arg, and
(via ``reject_to_review``) a ``needs-review/{category}/{book}.json`` object key.

This module is the single converged derivation (P1-S4 + P1-C2):

    stem of the filename  ->  spaces replaced with '_'  ->  must fully match
    ``[A-Za-z0-9._-]+`` (and not be '.'/'..') else fall back to 'unknown'.

The fallback is LOUD: an ``OBSERV book.slug_rejected`` WARNING is emitted so a
rejected (possibly hostile / path-traversal) key is diagnosable rather than a
silent collapse. Fail-safe: this helper never raises into the phase.
"""

from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)

# A book slug becomes a filesystem path segment, an S3/DynamoDB key component, and
# a subprocess ``--book`` argument, so it must be a plain safe token — no path
# separators, no '..', no whitespace, no shell/YAML metacharacters.
_SAFE_SLUG_RE = re.compile(r"[A-Za-z0-9._-]+")


def book_slug(key: str) -> str:
    """Derive a SAFE book slug from an S3 key (filename stem, spaces->_).

    Returns the validated slug, or ``'unknown'`` (with a loud ``OBSERV
    book.slug_rejected`` WARNING) when the derived candidate is empty, '.'/'..',
    or contains any char outside ``[A-Za-z0-9._-]``. Never raises.
    """
    try:
        stem = (key or "").rsplit("/", 1)[-1].rsplit(".", 1)[0].replace(" ", "_")
    except Exception:  # noqa: BLE001 - derivation must never crash the phase
        stem = ""
    if stem in ("", ".", "..") or not _SAFE_SLUG_RE.fullmatch(stem):
        logger.warning(
            "OBSERV book.slug_rejected key=%r (unsafe/untitled -> 'unknown')", key
        )
        return "unknown"
    return stem

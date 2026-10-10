"""Deterministic, content-derived identifiers for events (HIGH-1 #1).

An event's identity is a pure function of its STABLE source location (book + chapter + section),
not a fresh random ULID from the LLM each run. Re-extracting the same source unit therefore yields
the SAME ``EventID`` — so dependents (maps/weather/casualties/images/source_section) never dangle
across re-runs, and the LLM-supplied id is derived/validated rather than trusted.

The output is a normal 26-char Crockford-base32 ULID string (schema-compatible: matches
``^[0-9A-HJKMNP-TV-Z]{26}$``) — only its DERIVATION is deterministic (128 bits from a sha256 of the
identity key instead of timestamp+randomness). A version tag in the hash input allows a deliberate,
non-silent re-key in the future.

Single source of truth: all event-producing paths must mint ids through here (never ``ulid.new()``
for an EventID/Sub-eventID), so identity is consistent across the codebase.
"""

from __future__ import annotations

import hashlib
import re
from typing import Optional

import ulid

# Bump ONLY for an intentional, corpus-wide re-key (changes every derived id by design).
_SCHEMA = "v1"


def _normalize(text: Optional[str]) -> str:
    """Lowercase, strip punctuation, collapse whitespace — so trivial reformatting of a title does
    not change identity. ``None``/empty normalize to the empty string."""
    if not text:
        return ""
    cleaned = re.sub(r"[^0-9a-z]+", " ", str(text).lower())
    return " ".join(cleaned.split())


def _ulid_from_key(key: str) -> str:
    """Mint a deterministic, schema-valid ULID string from an identity key (first 128 bits of its
    sha256 digest)."""
    digest = hashlib.sha256(key.encode("utf-8")).digest()[:16]
    return str(ulid.from_bytes(digest))


def event_id(book: Optional[str], chapter: Optional[str], section: str = "full") -> str:
    """Deterministic ``EventID`` for one source section.

    One Event per source section (chunk-split chapters are merged into a single Event upstream, so
    ``section`` defaults to ``"full"``). Derived from ``book`` + ``chapter`` + ``section`` only.
    """
    key = "|".join(
        ["event", _SCHEMA, _normalize(book), _normalize(chapter), _normalize(section)]
    )
    return _ulid_from_key(key)


def sub_event_id(
    parent_event_id: str,
    *,
    para_start: Optional[int] = None,
    para_end: Optional[int] = None,
    name: Optional[str] = None,
) -> str:
    """Deterministic ``Sub-eventID``, stable WITHIN its parent event.

    Preferred key is the sub-event's paragraph range (objective, stable across runs); falls back to
    a normalized ``name`` only when no range is available (less stable — the model can reword). At
    least one of (para_start/para_end) or ``name`` must be provided."""
    if para_start is not None or para_end is not None:
        locator = f"p{para_start}-{para_end}"
    elif name:
        locator = f"n:{_normalize(name)}"
    else:
        raise ValueError("sub_event_id requires a paragraph range or a name")
    return _ulid_from_key("|".join(["subevent", _SCHEMA, parent_event_id, locator]))

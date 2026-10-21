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
import logging
import re
from typing import Optional

import ulid

logger = logging.getLogger(__name__)

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


def _normalize_ulid(value: Optional[str]) -> str:
    """Normalize a (possibly LLM-mangled) ULID for cosmetic near-match: lowercase + drop any
    non-alphanumeric chars. Recovers only COSMETIC noise (case, stray dashes/spaces/quotes).
    """
    return re.sub(r"[^a-z0-9]", "", (value or "").lower())


def verify_embedded_ulid(known_id: str, echoed_id: Optional[str]) -> tuple[str, str]:
    """Inbound→outbound integrity check for an id we EMBEDDED in a prompt (HIGH#7b).

    The LLM is given a known reference id (e.g. a Sub-eventID) and asked to echo it back. Because we
    ALREADY KNOW the correct value, we never guess/randomize on mismatch — we snap back to
    ``known_id``. Returns ``(trusted_id, outcome)`` where trusted_id is ALWAYS ``known_id`` and
    outcome is one of:

    - ``"exact"``        — echo matched byte-for-byte (Grok honored it).
    - ``"restored_cosmetic"`` — echo differed only by case/punctuation noise; restored.
    - ``"restored_mismatch"``  — echo was absent or substantively different; restored.

    The outcome is for OBSERVABILITY (measures Grok's honor rate); the trusted id is authoritative
    either way. Callers should count non-``exact`` outcomes as a quality signal.
    """
    if echoed_id == known_id:
        return known_id, "exact"
    if echoed_id and _normalize_ulid(echoed_id) == _normalize_ulid(known_id):
        return known_id, "restored_cosmetic"
    return known_id, "restored_mismatch"


def observe_embedded_ulid(known_id: str, echoed_id: Optional[str], entity: str) -> str:
    """verify_embedded_ulid + OBSERVABILITY. Emits ``OBSERV xref.embedded_ulid entity=<entity>
    outcome=<...>`` so Grok's HONOR rate for embedded reference ids is visible; a non-exact outcome
    (the LLM altered an id we told it to echo) is logged at INFO as a quality signal. Returns the
    trusted id (always ``known_id``). Fail-safe: never raises into the extractor."""
    try:
        trusted, outcome = verify_embedded_ulid(known_id, echoed_id)
        if outcome == "exact":
            logger.debug("OBSERV xref.embedded_ulid entity=%s outcome=exact", entity)
        else:
            logger.info(
                "OBSERV xref.embedded_ulid entity=%s outcome=%s echoed=%r known=%s",
                entity,
                outcome,
                (echoed_id or "")[:40],
                known_id,
            )
        return trusted
    except Exception:  # noqa: BLE001 - observability must never break extraction
        return known_id


def reconcile_echoed_keys(known_ids, keyed: dict, entity: str) -> dict:
    """Re-key a dict whose keys are LLM-ECHOED ids onto the KNOWN ids (HIGH#7b, batch shape).

    A batch response keyed by an echoed Sub-eventID would otherwise miss a ``keyed.get(real_id)``
    lookup if the model mangled the key — silently dropping that unit's data (#448 class). For each
    known id, take its value by EXACT key, else by a UNIQUE cosmetic-normalized match (case/
    punctuation only — never a fuzzy guess; ambiguous matches are skipped). Emits OBSERV
    ``xref.embedded_ulid entity=<entity>`` with exact/recovered counts. Fail-safe: returns ``keyed``
    unchanged on any error or when nothing reconciles.
    """
    try:
        known = [k for k in known_ids if k]
        if not known or not isinstance(keyed, dict):
            return keyed
        reconciled, exact, recovered = _reconcile_known_against_keyed(known, keyed)
        if recovered:
            logger.info(
                "OBSERV xref.embedded_ulid entity=%s exact=%d recovered=%d known=%d",
                entity,
                exact,
                recovered,
                len(known),
            )
        return reconciled or keyed
    except Exception:  # noqa: BLE001 - reconciliation must never break extraction
        return keyed


def _reconcile_known_against_keyed(known: list, keyed: dict):
    """Core re-key: for each known id take its value by EXACT key, else by a UNIQUE cosmetic-
    normalized match (ambiguous matches skipped). Returns (reconciled, exact, recovered).
    Identity-critical — preserves the match rules EXACTLY."""
    norm_index: dict = {}
    for k in keyed:
        norm_index.setdefault(_normalize_ulid(k), []).append(k)
    reconciled: dict = {}
    exact = recovered = 0
    for real in known:
        if real in keyed:
            reconciled[real] = keyed[real]
            exact += 1
            continue
        cand = norm_index.get(_normalize_ulid(real), [])
        if len(cand) == 1:
            reconciled[real] = keyed[cand[0]]
            recovered += 1
    return reconciled, exact, recovered

"""people_groups source-recheck — a thin configuration of the reusable SourceRechecker.

Recovers the two CRITICAL group fields from retained source text:
  * nationality (absolute dedup veto; also required for Wikipedia disambiguation);
  * a Combat Command's parent division (makes a bare CCA/CCB/CCR identifiable).
Gap-fill only; source-first; fail-safe. See src/extraction/source_recheck.py.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List

from src.extraction.source_recheck import (
    FieldRecheckSpec,
    SourceRechecker,
    set_sourced,
)

# Source books whose nationality is unambiguous — a prior when the text is thin.
_SOURCE_NATIONALITY_HINT = {
    "cross-channel attack": "USA",
    "the ardennes: battle of the bulge": "USA",
    "united states army in world war ii": "USA",
}

_CC_RE = re.compile(r"\bcc[\s\-]*[abr]\b|\bcombat command\s+[abr]\b", re.I)

_FIELDS = {
    "nationality": "ISO 3166-1 alpha-3 (e.g. USA, DEU, GBR) or null",
    "parent_organization": 'the parent division name (e.g. "3rd Armored Division") or null',
}


def _needs(rec: Dict[str, Any]) -> List[str]:
    out = []
    if not (rec.get("nationality") or rec.get("country_of_origin")):
        out.append("nationality")
    name = (rec.get("name") or rec.get("group_name") or "").lower()
    is_cc = bool(_CC_RE.search(name))
    if is_cc and "division" not in (rec.get("parent_organization") or "").lower():
        out.append("parent_organization")
    return out


def _source_book(rec: Dict[str, Any]) -> str:
    for m in rec.get("event_mentions", []) or []:
        if m.get("book"):
            return m["book"]
    return ""


def _book_hint_posthook(rec: Dict[str, Any], filled: List[str]) -> int:
    """If nationality was needed but not recovered from the text, fall back to a curated
    source-book prior."""
    if "nationality" in filled:
        return 0
    if rec.get("nationality") or rec.get("country_of_origin"):
        return 0
    hint = _SOURCE_NATIONALITY_HINT.get(_source_book(rec).strip().lower())
    if hint:
        set_sourced(rec, "nationality", hint, "source_book_hint", 0.6)
        return 1
    return 0


_SPEC = FieldRecheckSpec(
    fields=_FIELDS,
    needed=_needs,
    label="people_group",
    post_hook=_book_hint_posthook,
)
_RECHECKER = SourceRechecker(_SPEC)


def recheck_group_from_source(data: Dict[str, Any], grok_client: Any) -> int:
    """Recover missing critical group fields (nationality, CC parent division) from the
    retained source text. Gap-fill only. Returns the count filled."""
    return _RECHECKER.recheck(data, grok_client)

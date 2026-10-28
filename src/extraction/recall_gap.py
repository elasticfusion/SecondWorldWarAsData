"""People RECALL-GAP review (PR-B, 2026-10-28 audit).

The yield observer (``observe_yield``) catches the *mass* starvation case (0 people from an event
whose sub-events had text). This module catches the *targeted* miss: a deterministic, rank-anchored
name clearly present in the source (``General Patton``, ``Lt. Col. Jones``, even an ambiguous
``Private Smith``) that the LLM extraction did NOT resolve to a person entity.

Policy (owner): ambiguous names MUST be surfaced for human follow-up, NEVER silently dropped — but a
recall-gap is a data-quality SIGNAL, not a fatal error, so detection + review-write are strictly
FAIL-SAFE: any error here is swallowed to a DEBUG line and the run proceeds.

It REUSES the existing needs-review off-ramp pattern (cf. ``src/ingestion/review_reject.py`` and
``supplemental._append_to_review_queue``): recall-gap items are appended to a local
``people/review_queue.json`` with ``category='recall-gap'`` for operator triage.
"""

import json
import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.utils.text_utils import normalize_name

logger = logging.getLogger(__name__)

# Deterministic, high-precision military-rank vocabulary (abbreviations + full forms). A
# rank immediately followed by a Capitalized token is an unambiguous person mention in WWII prose.
_RANKS = [
    "General",
    "Gen",
    "Lieutenant General",
    "Lt Gen",
    "Major General",
    "Maj Gen",
    "Brigadier General",
    "Brig Gen",
    "Brigadier",
    "Colonel",
    "Col",
    "Lieutenant Colonel",
    "Lt Col",
    "Major",
    "Maj",
    "Captain",
    "Capt",
    "Lieutenant",
    "Lt",
    "Sergeant",
    "Sgt",
    "Corporal",
    "Cpl",
    "Private",
    "Pvt",
    "Admiral",
    "Adm",
    "Vice Admiral",
    "Rear Admiral",
    "Commodore",
    "Commander",
    "Cdr",
    "Field Marshal",
    "Marshal",
    "Air Marshal",
    "Air Chief Marshal",
    "Wing Commander",
    "Squadron Leader",
    "Group Captain",
]


# Rank alternation, longest-first so "Lieutenant Colonel" wins over "Lieutenant". Each inter-word
# space tolerates an abbreviation period ("Lt. Col."); a trailing period on the rank is optional too.
def _rank_pattern(rank: str) -> str:
    words = rank.split()
    return r"\.?\s+".join(re.escape(w) for w in words)


_RANK_ALT = "|".join(_rank_pattern(r) for r in sorted(_RANKS, key=len, reverse=True))
# rank (optional trailing '.') + 1-3 Capitalized name tokens (handles "von", "de", hyphens).
_MENTION_RE = re.compile(
    r"\b(?:" + _RANK_ALT + r")\.?\s+"
    r"((?:(?:von|de|van|der|del|di)\s+)?"
    r"[A-Z][A-Za-z'\-]+(?:\s+(?:von|de|van|der|del|di\s+)?[A-Z][A-Za-z'\-]+){0,2})"
)


def find_rank_anchored_mentions(text: str) -> List[str]:
    """Deterministically extract rank-anchored name mentions (full phrase incl. rank). Fail-safe."""
    try:
        if not text:
            return []
        seen: Dict[str, str] = {}
        for m in _MENTION_RE.finditer(text):
            phrase = m.group(0).strip()
            key = normalize_name(phrase)
            if key and key not in seen:
                seen[key] = phrase
        return list(seen.values())
    except Exception as e:  # noqa: BLE001 - detection must never break extraction
        logger.debug("find_rank_anchored_mentions skipped: %s", e)
        return []


def _extracted_name_keys(extracted_people: List[dict]) -> set:
    """Normalized name keys (name + aliases) of the people the LLM actually extracted. Fail-safe."""
    keys: set = set()
    try:
        for person in extracted_people or []:
            keys.update(_person_name_keys(person))
    except Exception as e:  # noqa: BLE001
        logger.debug("_extracted_name_keys skipped: %s", e)
    return keys


def _norm_str_values(values) -> set:
    """Normalized keys for a list of candidate name strings (ignores non-strings/blanks)."""
    out: set = set()
    for v in values or []:
        if isinstance(v, str) and v.strip():
            out.add(normalize_name(v))
    return out


def _person_name_keys(person) -> set:
    """Normalized name + alias keys for a single extracted person dict. Fail-safe -> set()."""
    if not isinstance(person, dict):
        return set()
    names = [person.get(f) for f in ("name", "Name", "full_name", "Full_Name")]
    aliases = person.get("aliases") or person.get("Aliases") or []
    alias_list = aliases if isinstance(aliases, list) else []
    return _norm_str_values(names) | _norm_str_values(alias_list)


def _mention_resolved(mention: str, extracted_keys: set) -> bool:
    """True if a rank-anchored mention maps to an extracted person.

    Matches on the SURNAME (last capitalized token), so "General Patton" resolves to an extracted
    "George Patton". A resolved name must share a surname token with some extracted key.
    """
    mkey = normalize_name(mention)
    if not mkey:
        return False
    tokens = [t for t in mkey.split() if t not in _RANK_TOKENS]
    if not tokens:
        return False
    surname = tokens[-1]
    for ekey in extracted_keys:
        if surname and surname in ekey.split():
            return True
    return False


_RANK_TOKENS = {normalize_name(r) for r in _RANKS}
_RANK_TOKENS = {t for part in _RANK_TOKENS for t in part.split()}


def detect_recall_gaps(text: str, extracted_people: List[dict]) -> List[str]:
    """Return rank-anchored mentions present in ``text`` but NOT resolved to an extracted person.

    Fail-safe: any error returns [] (never raises into extraction).
    """
    try:
        mentions = find_rank_anchored_mentions(text)
        if not mentions:
            return []
        extracted_keys = _extracted_name_keys(extracted_people)
        return [m for m in mentions if not _mention_resolved(m, extracted_keys)]
    except Exception as e:  # noqa: BLE001
        logger.debug("detect_recall_gaps skipped: %s", e)
        return []


def _append_recall_gap_items(people_dir: Path, items: List[Dict[str, Any]]) -> None:
    """Append recall-gap items to people/review_queue.json.

    This mirrors the EXISTING supplemental review-queue writer (``supplemental._append_to_review_
    queue``, already in the raw-write baseline): review_queue.json is a NON-record human-review sink
    (a list of review items, NOT an entity record), so it does not go through the entity write guard.
    """
    if not items:
        return
    queue_file = people_dir / "review_queue.json"
    existing: List[Any] = []
    if queue_file.exists():
        try:
            with open(queue_file, "r", encoding="utf-8") as f:
                existing = json.load(f)
            if not isinstance(existing, list):
                existing = []
        except (OSError, ValueError):
            existing = []
    existing.extend(items)
    with open(queue_file, "w", encoding="utf-8") as f:
        json.dump(existing, f, indent=2, ensure_ascii=False)


def record_people_recall_gaps(
    people_dir: Path,
    event_id: str,
    sub_event_id: str,
    text: str,
    extracted_people: List[dict],
) -> int:
    """Detect unresolved rank-anchored mentions and write recall-gap review items.

    REUSES the needs-review pattern: writes ``category='recall-gap'`` items to
    ``people/review_queue.json`` so ambiguous names (e.g. "Private Smith") are surfaced for human
    follow-up and NEVER silently dropped. Returns the number of gaps recorded. FAIL-SAFE: never
    raises — a detection/write error is logged at DEBUG and the extraction run proceeds.
    """
    try:
        gaps = detect_recall_gaps(text, extracted_people)
        if not gaps:
            return 0
        items = [
            {
                "category": "recall-gap",
                "disposition": "needs-review-recall-gap",
                "mention": g,
                "event_id": event_id or "?",
                "sub_event_id": sub_event_id or "?",
                "reason": (
                    "deterministic rank-anchored name mention present in source but NOT "
                    "resolved to an extracted person entity"
                ),
            }
            for g in gaps
        ]
        _append_recall_gap_items(Path(people_dir), items)
        logger.warning(
            "OBSERV people.recall_gap event=%s sub_event=%s gaps=%d mentions=%s — "
            "rank-anchored name(s) in source not extracted; queued for human review",
            event_id or "?",
            sub_event_id or "?",
            len(gaps),
            gaps,
        )
        return len(gaps)
    except Exception as e:  # noqa: BLE001 - recall-gap review must never fail the run
        logger.debug("record_people_recall_gaps skipped for %s: %s", event_id, e)
        return 0


def _text_from_sub_event(sub_event: Optional[dict]) -> str:
    """Join a sub-event's Sub-event_fulltext into a single string. Fail-safe -> ''."""
    try:
        if not isinstance(sub_event, dict):
            return ""
        fulltext = sub_event.get("Sub-event_fulltext") or {}
        if isinstance(fulltext, dict):
            return "\n".join(str(v) for v in fulltext.values())
        if isinstance(fulltext, str):
            return fulltext
        return ""
    except Exception:  # noqa: BLE001
        return ""

"""Parse depicted person names from a figure caption (M3, multimodal epic).

HyperWar figure captions name the people in a photo using a few stable conventions, e.g.:
  "ALLIED INVASION CHIEFS. Left to right: General Bradley, Admiral Ramsay, Air Chief Marshal
   Tedder, General Eisenhower, General Montgomery, Air Chief Marshal Leigh-Mallory, General Smith."
  "GERMAN FIELD COMMANDERS. Upper left, General von Schlieben; upper right, General Marcks;
   lower left, General Geyr von Schweppenburg; lower right, General Dollmann."
  "General Eisenhower addresses paratroopers."

This module extracts the candidate person-name strings CONSERVATIVELY (only from an explicit
positional cue list, or clear military-title + name patterns) so prose captions don't yield false
positives. It is PURE (no I/O) — resolution to a PersonID happens in image_person_link.
"""

from __future__ import annotations

import re
from typing import List

# Positional cues that introduce a name LIST in a figure caption.
_POSITIONAL_CUE = re.compile(
    r"(left to right|right to left|upper left|upper right|lower left|lower right|"
    r"top to bottom|front row|back row|standing|seated|l to r)\s*[:,]?\s*",
    re.IGNORECASE,
)

# Military / honorific titles that reliably precede a person name in these captions.
_TITLES = (
    "general",
    "lt. general",
    "lieutenant general",
    "maj. gen.",
    "major general",
    "brig. gen.",
    "brigadier general",
    "gen.",
    "lt. col.",
    "lieutenant colonel",
    "colonel",
    "col.",
    "major",
    "maj.",
    "captain",
    "capt.",
    "admiral",
    "adm.",
    "air chief marshal",
    "air marshal",
    "air vice marshal",
    "field marshal",
    "marshal",
    "generalleutnant",
    "generalmajor",
    "generaloberst",
    "konteradmiral",
    "s/sgt.",
    "sgt.",
    "lt.",
    "lieutenant",
    "pvt.",
    "cpl.",
)
# Title + Name: a title token(s) followed by 1-4 Capitalized name tokens (incl. "von"/"de").
_TITLE_NAME = re.compile(
    r"\b("
    + "|".join(re.escape(t) for t in sorted(_TITLES, key=len, reverse=True))
    + r")\s+"
    r"((?:(?:von|van|de|der|den|du|le|la)\s+)?[A-Z][a-zA-Z.'-]+(?:\s+(?:von|van|de|der|den)\s+)?"
    r"(?:\s+[A-Z][a-zA-Z.'-]+){0,3})",
    re.IGNORECASE,
)

_MAX_NAMES = 20  # a figure caption never names more than a handful; cap defensively.


def parse_caption_person_names(caption: str) -> List[str]:
    """Return depicted person names parsed from a figure caption (deduped, order-preserving).

    Strategy: (1) if a positional cue is present, take the name LIST after it, split on separators,
    and strip leading titles; (2) ALSO capture any explicit Title+Name occurrences anywhere. Both
    are conservative so a prose caption yields nothing. Pure; never raises.
    """
    if not caption or not isinstance(caption, str):
        return []
    names: List[str] = []
    seen: set = set()
    _positional_pass(caption, names, seen)
    _title_name_pass(caption, names, seen)
    return names


def _accumulate(raw: str, names: List[str], seen: set) -> None:
    """Clean a raw name chunk and append if new (order-preserving, capped)."""
    n = _clean_name(raw)
    if n and n.lower() not in seen and len(names) < _MAX_NAMES:
        names.append(n)
        seen.add(n.lower())


def _positional_pass(caption: str, names: List[str], seen: set) -> None:
    """(1) A positional cue ('Left to right:' etc.) introduces a name LIST — split + add each."""
    cue = _POSITIONAL_CUE.search(caption)
    if not cue:
        return
    for chunk in re.split(r"[;,]|\band\b", caption[cue.end() :]):
        chunk = chunk.strip().rstrip(".")
        if not chunk:
            continue
        # Trim trailing prose on the LAST list item ("Smith. This photograph was ...").
        chunk = re.split(r"\.\s+[A-Z]", chunk)[0].strip()
        if len(chunk.split()) > 6:  # a long clause = we've left the name list
            break
        _accumulate(chunk, names, seen)


def _title_name_pass(caption: str, names: List[str], seen: set) -> None:
    """(2) Explicit Title+Name anywhere (single-subject captions). Skip positional-pass overlaps."""
    for m in _TITLE_NAME.finditer(caption):
        cand = _clean_name(m.group(2))
        if not cand:
            continue
        cl = cand.lower()
        if any(cl in n or n in cl for n in seen):
            continue
        _accumulate(cand, names, seen)


def _clean_name(raw: str) -> str:
    """Strip a leading military/honorific title + surrounding punctuation from a name chunk; keep
    the proper-name portion. Returns "" if nothing name-like remains."""
    s = raw.strip().strip(".,;:").strip()
    if not s:
        return ""
    low = s.lower()
    for t in sorted(_TITLES, key=len, reverse=True):
        if low.startswith(t + " "):
            s = s[len(t) :].strip()
            break
    # Require at least one Capitalized token (a real name), reject stray lowercase fragments.
    if not re.search(r"[A-Z][a-zA-Z]", s):
        return ""
    # Reject obviously-non-name trailing verbs/prose (a name chunk is short).
    if len(s.split()) > 4:
        return ""
    return s

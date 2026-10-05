"""Canonical unit key for people-group (unit) deduplication.

A unit's identity is (number-set, branch, echelon) derived from its name, NOT raw
string similarity. This makes "Ninth Division" / "9th Division" / "9th Infantry
Division" all match, while keeping genuinely different units apart.

Match rule (owner-confirmed):
  • numbers MUST match (same number-set);
  • branch: ABSENT -> defaults to 'infantry'; branch MISMATCH -> VETO
    (so "9th Armored" vs "9th Division"[=infantry default] do NOT match);
  • echelon: ABSENT -> unknown/permissive (no veto); PRESENT on both and DIFFERENT
    -> VETO (so "9th Division" vs "9th Regiment" do NOT match).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional, Set

# Echelon lexicon (size tier). Order independent; matched as whole words.
_ECHELON_TERMS = {
    "squad": "squad",
    "section": "section",
    "platoon": "platoon",
    "company": "company",
    "battery": "battery",
    "troop": "troop",
    "battalion": "battalion",
    "regiment": "regiment",
    "brigade": "brigade",
    "division": "division",
    "corps": "corps",
    "army": "army",
    "group": "group",
    "squadron": "squadron",
    "wing": "wing",
    "fleet": "fleet",
    "flotilla": "flotilla",
    "command": "command",
}

# Branch lexicon. INFANTRY is the default when no branch word is present.
_BRANCH_TERMS = {
    "infantry": "infantry",
    "armored": "armored",
    "armoured": "armored",
    "armor": "armored",
    "tank": "armored",
    "panzer": "armored",
    "cavalry": "cavalry",
    "airborne": "airborne",
    "parachute": "airborne",
    "glider": "airborne",
    "artillery": "artillery",
    "field artillery": "artillery",
    "engineer": "engineer",
    "engineers": "engineer",
    "signal": "signal",
    "mountain": "mountain",
    "marine": "marine",
    "ranger": "ranger",
    "commando": "commando",
    "parachute infantry": "airborne",
}

# Abbreviation expansions applied to the name before term extraction. Order matters
# (longer/compound first). Maps common unit abbreviations to spelled-out words so the
# echelon/branch terms are found. e.g. "502 PIR" -> parachute infantry regiment.
_ABBREVIATIONS = [
    (r"\bpir\b", "parachute infantry regiment"),
    (r"\bgir\b", "glider infantry regiment"),
    (r"\binf\s*div\b", "infantry division"),
    (r"\barmd?\s*div\b", "armored division"),
    (r"\babn\b", "airborne"),
    (r"\binf\b", "infantry"),
    (r"\barmd\b", "armored"),
    (r"\barty\b", "artillery"),
    (r"\bfa\b", "field artillery"),
    (r"\bengr?\b", "engineer"),
    (r"\bbn\b", "battalion"),
    (r"\bregt?\b", "regiment"),
    (r"\bdiv\b", "division"),
    (r"\bbde\b", "brigade"),
    (r"\bco\b", "company"),
    (r"\bbtry\b", "battery"),
    (r"\bsqdn\b", "squadron"),
]

_ORDINAL_WORDS = {
    "first": "1",
    "second": "2",
    "third": "3",
    "fourth": "4",
    "fifth": "5",
    "sixth": "6",
    "seventh": "7",
    "eighth": "8",
    "ninth": "9",
    "tenth": "10",
    "eleventh": "11",
    "twelfth": "12",
    "thirteenth": "13",
    "fourteenth": "14",
    "fifteenth": "15",
    "sixteenth": "16",
    "seventeenth": "17",
    "eighteenth": "18",
    "nineteenth": "19",
    "twentieth": "20",
}

_ROMAN = {
    "i": "1",
    "ii": "2",
    "iii": "3",
    "iv": "4",
    "v": "5",
    "vi": "6",
    "vii": "7",
    "viii": "8",
    "ix": "9",
    "x": "10",
    "xi": "11",
    "xii": "12",
    "xiii": "13",
    "xiv": "14",
    "xv": "15",
    "xvi": "16",
    "xvii": "17",
    "xviii": "18",
    "xix": "19",
    "xx": "20",
}


@dataclass(frozen=True)
class UnitKey:
    numbers: frozenset
    branch: str  # resolved (infantry default)
    echelon: Optional[str]  # None = unknown/permissive


def _expand(name: str) -> str:
    s = name.lower()
    for pat, repl in _ABBREVIATIONS:
        s = re.sub(pat, repl, s)
    return s


def _numbers(name: str) -> Set[str]:
    low = name.lower()
    nums: Set[str] = set()
    for m in re.finditer(r"(\d+)(?:st|nd|rd|th|d)\b", low):
        nums.add(m.group(1))
    for m in re.finditer(r"\b(\d+)\b", low):
        nums.add(m.group(1))
    for w in re.split(r"[^a-z]+", low):
        if w in _ORDINAL_WORDS:
            nums.add(_ORDINAL_WORDS[w])
        elif w in _ROMAN:
            nums.add(f"r{_ROMAN[w]}")  # keep roman distinct (corps numbering)
    return nums


def _first_term(expanded: str, lexicon: dict) -> Optional[str]:
    # match multi-word terms first (e.g. "field artillery", "parachute infantry")
    for term in sorted(lexicon, key=lambda t: -len(t)):
        if re.search(rf"\b{re.escape(term)}\b", expanded):
            return lexicon[term]
    return None


def derive_unit_key(name: str, *, infantry_default: bool = True) -> UnitKey:
    """Derive (numbers, branch, echelon) from a unit name. Branch defaults to
    'infantry' when absent; echelon stays None (unknown) when absent."""
    expanded = _expand(name or "")
    numbers = frozenset(_numbers(name or ""))
    branch = _first_term(expanded, _BRANCH_TERMS)
    if branch is None and infantry_default:
        branch = "infantry"
    echelon = _first_term(expanded, _ECHELON_TERMS)
    return UnitKey(numbers=numbers, branch=branch or "infantry", echelon=echelon)


def unit_keys_match(k1: UnitKey, k2: UnitKey) -> tuple[bool, str]:
    """Apply the match rule. Returns (match, reason)."""
    # Numbers must match (and at least one must have a number — avoid matching two
    # number-less names on branch/echelon alone).
    if k1.numbers != k2.numbers:
        return False, "number mismatch"
    if not k1.numbers:
        return False, "no unit number"
    # Branch mismatch -> veto (infantry default already applied).
    if k1.branch != k2.branch:
        return False, f"branch mismatch ({k1.branch} vs {k2.branch})"
    # Echelon: veto only when BOTH present and different; absent is permissive.
    if k1.echelon and k2.echelon and k1.echelon != k2.echelon:
        return False, f"echelon mismatch ({k1.echelon} vs {k2.echelon})"
    ech = k1.echelon or k2.echelon or "unspecified"
    return True, f"canonical unit key match (#{sorted(k1.numbers)} {k1.branch} {ech})"

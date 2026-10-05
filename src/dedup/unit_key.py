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

# SERVICE lexicon — the armed SERVICE (branch of service). A mismatch is an ABSOLUTE
# veto, same tier as nationality: 1st Marine Division != 1st Infantry Division (Army).
# Default is ARMY when no service marker (most WWII ground units; the infantry
# combat-arm default below only applies under Army).
_SERVICE_TERMS = {
    "marine": "USMC",
    "marines": "USMC",
    "usmc": "USMC",
    "navy": "USN",
    "naval": "USN",
    "usn": "USN",
    "fleet": "USN",
    "coast guard": "USCG",
    "uscg": "USCG",
    "air force": "USAAF",
    "air forces": "USAAF",
    "army air": "USAAF",
    "army air force": "USAAF",
    "army air forces": "USAAF",
    "air corps": "USAAF",
    "usaaf": "USAAF",
    "raf": "RAF",
    "royal air force": "RAF",
    "royal navy": "RN",
}

# COMBAT ARM lexicon — the arm WITHIN a service (primarily Army). Mismatch -> veto.
# Infantry is the default ONLY when the service is Army (see derive_unit_key).
_ARM_TERMS = {
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
    "parachute infantry": "airborne",
    "artillery": "artillery",
    "field artillery": "artillery",
    "engineer": "engineer",
    "engineers": "engineer",
    "signal": "signal",
    "mountain": "mountain",
    "ranger": "ranger",
    "commando": "commando",
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
    service: str  # armed service (ARMY default); absolute-veto on mismatch
    arm: Optional[str]  # combat arm within the service (infantry default under ARMY)
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
            # Roman numerals (used for corps) unify with arabic: 'VII Corps' == '7th
            # Corps' (a bare arabic corps is a typo for the roman). Owner-confirmed.
            nums.add(_ROMAN[w])
    return nums


def _first_term(expanded: str, lexicon: dict) -> Optional[str]:
    # match multi-word terms first (e.g. "field artillery", "parachute infantry")
    for term in sorted(lexicon, key=lambda t: -len(t)):
        if re.search(rf"\b{re.escape(term)}\b", expanded):
            return lexicon[term]
    return None


import os
from functools import lru_cache


@lru_cache(maxsize=2)
def _load_nicknames(path: str) -> dict:
    """Load the curated nickname -> canonical-name map. {} on any error (graceful)."""
    try:
        import yaml
        from pathlib import Path as _P

        p = _P(path)
        if not p.is_file():
            return {}
        data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        return {
            str(k).strip().lower(): str(v)
            for k, v in (data.get("nicknames", {}) or {}).items()
        }
    except Exception:
        return {}


def resolve_nickname(name: str) -> Optional[str]:
    """If ``name`` (or a cleaned form) is a known unit nickname, return the canonical
    unit name; else None. Tolerates a leading 'the' and surrounding punctuation."""
    if not name:
        return None
    path = os.getenv("UNIT_NICKNAMES_PATH", "data/unit_nicknames.yaml")
    table = _load_nicknames(path)
    key = name.strip().lower().strip("\"'.,")
    if key in table:
        return table[key]
    if key.startswith("the ") and key[4:] in table:
        return table[key[4:]]
    return None


def derive_unit_key(name: str, *, infantry_default: bool = True) -> UnitKey:
    """Derive (numbers, service, arm, echelon) from a unit name.

    A known NICKNAME ("Screaming Eagles") is resolved to its canonical name first.
    service defaults to ARMY when no service marker is present. echelon stays None when
    absent. arm (combat arm) defaults to 'infantry' ONLY at the DIVISION or REGIMENT
    echelon under Army — WWII bare numbered divisions/regiments are infantry by default,
    but a bare 'battalion'/'company' is NOT reliably infantry, so arm stays unknown below
    regiment. Combat Commands (CCA/CCB/CCR) are the armored-division brigade-equivalent
    combined-arms formations: recognized as echelon 'combat_command', arm 'armored',
    with the command letter captured so CCA/CCB/CCR stay distinct.
    """
    canonical = resolve_nickname(name)
    source = canonical if canonical else (name or "")
    expanded = _expand(source)

    # Combat Command (CCA/CCB/CCR or "Combat Command A/B/R"): armored combined-arms.
    cc = re.search(r"\bcc\s*([abr])\b", expanded) or re.search(
        r"\bcombat command\s+([abr])\b", expanded
    )

    numbers = set(_numbers(source))
    service = _first_term(expanded, _SERVICE_TERMS) or "ARMY"
    arm = _first_term(expanded, _ARM_TERMS)
    echelon = _first_term(expanded, _ECHELON_TERMS)

    if cc:
        # The CC letter is the discriminating designator (keep distinct from numbers);
        # a CC belongs to an armored division and is itself an armored combined-arms unit.
        numbers.add(f"cc{cc.group(1)}")
        echelon = "combat_command"
        arm = arm or "armored"

    # Infantry combat-arm default: division/regiment only, Army only, bare arm only.
    if (
        arm is None
        and service == "ARMY"
        and infantry_default
        and echelon
        in (
            "division",
            "regiment",
        )
    ):
        arm = "infantry"

    return UnitKey(
        numbers=frozenset(numbers), service=service, arm=arm, echelon=echelon
    )


def unit_keys_match(k1: UnitKey, k2: UnitKey) -> tuple[bool, str]:
    """Apply the match rule. Returns (match, reason)."""
    # Numbers must match (and at least one must have a number).
    if k1.numbers != k2.numbers:
        return False, "number mismatch"
    if not k1.numbers:
        return False, "no unit number"
    # Combat Command must be affiliated with a DIVISION to be identifiable: a bare
    # CCA/CCB/CCR (letter only, no parent-division number) is underspecified — its
    # composition is task-organized/fluid, so letter+division is the only reliable
    # identity. Two bare CCs can't be confidently matched (route to the human gate).
    if any(str(n).startswith("cc") for n in k1.numbers):
        if not any(not str(n).startswith("cc") for n in k1.numbers):
            return False, "combat command without parent division (underspecified)"
    # SERVICE mismatch -> ABSOLUTE veto (1st Marine Division != 1st Infantry Division).
    if k1.service != k2.service:
        return False, f"service mismatch ({k1.service} vs {k2.service})"
    # COMBAT ARM: veto only when BOTH present and different; absent is permissive.
    if k1.arm and k2.arm and k1.arm != k2.arm:
        return False, f"arm mismatch ({k1.arm} vs {k2.arm})"
    # ECHELON: veto only when BOTH present and different; absent is permissive.
    if k1.echelon and k2.echelon and k1.echelon != k2.echelon:
        return False, f"echelon mismatch ({k1.echelon} vs {k2.echelon})"
    arm = k1.arm or k2.arm or "unspecified"
    ech = k1.echelon or k2.echelon or "unspecified"
    return (
        True,
        f"canonical unit key match (#{sorted(k1.numbers)} {k1.service} {arm} {ech})",
    )

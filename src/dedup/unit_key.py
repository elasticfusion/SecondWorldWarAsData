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
    "volksgrenadier": "volksgrenadier",
    "volks grenadier": "volksgrenadier",
    "grenadier": "volksgrenadier",
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
    (r"\bcav\s*div\b", "cavalry division"),
    (r"\bpz\s*div\b", "panzer division"),
    (r"\bvg\s*div\b", "volksgrenadier division"),
    (r"\bvg\b", "volksgrenadier"),
    (r"\babn\b", "airborne"),
    (r"\binf\b", "infantry"),
    (r"\barmd\b", "armored"),
    (r"\bad\b", "armored division"),
    (r"\bcav\b", "cavalry"),
    (r"\bpz\b", "panzer"),
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
        else:
            # Fallback for higher corps numerals (LXVI, LVIII, XLVII) not in the small
            # table. Only fires on a token that is a VALID canonical roman numeral, so
            # real words ("div", "mix") are never misread as numbers.
            roman = _parse_roman(w)
            if roman is not None:
                nums.add(str(roman))
    return nums


def _parse_roman(token: str) -> Optional[int]:
    """Parse a lowercase token as a canonical Roman numeral (1-399), else None.

    Validates by round-trip (int->roman==token) so only genuine numerals match —
    'div'/'mix'/'did' are rejected. Used for WWII corps designations above XX
    (e.g. 'lxvi' -> 66, 'lviii' -> 58, 'xlvii' -> 47)."""
    if not token or any(c not in "ivxlcdm" for c in token):
        return None
    vals = {"i": 1, "v": 5, "x": 10, "l": 50, "c": 100, "d": 500, "m": 1000}
    total, prev = 0, 0
    for c in reversed(token):
        v = vals[c]
        total += -v if v < prev else v
        prev = max(prev, v)
    if total <= 0 or total > 399:
        return None
    return total if _to_roman(total) == token else None


def _to_roman(n: int) -> str:
    """Canonical lowercase roman for 1..399 (enough for corps numerals)."""
    table = [
        (100, "c"),
        (90, "xc"),
        (50, "l"),
        (40, "xl"),
        (10, "x"),
        (9, "ix"),
        (5, "v"),
        (4, "iv"),
        (1, "i"),
    ]
    out = []
    for val, sym in table:
        while n >= val:
            out.append(sym)
            n -= val
    return "".join(out)


# Echelon nouns that occupy the 'size' slot; a word just before one of these that is
# NOT a known arm is treated as an (unrecognized) branch modifier.
_ECHELON_NOUNS = (
    r"(?:division|regiment|brigade|battalion|corps|army|squadron|wing|group|command)"
)
# Words in the branch slot that are structural, not a branch (skip them).
_SLOT_SKIP = {
    "us",
    "u",
    "s",
    "british",
    "french",
    "german",
    "italian",
    "polish",
    "canadian",
    "soviet",
    "russian",
    "the",
    "ss",
    "panzer",
}

# Non-US nationality/formation markers: when present, the US infantry-default convention
# does NOT apply (a German/British/Soviet/SS/Panzer/Volksgrenadier bare division is not
# infantry-by-default). The infantry default is purely a US Army designation.
_NON_US_SIGNAL = re.compile(
    r"\b(panzer|volksgrenadier|volks\s*grenadier|vg|waffen|wehrmacht|ss|german|germany|"
    r"british|britain|english|soviet|russian|russia|japanese|japan|italian|italy|"
    r"french|france|polish|poland|canadian|canada|grenadier)\b"
)


def _non_us_signal(expanded: str) -> bool:
    """True if the name carries a non-US nationality/formation marker (so the US-only
    infantry default must NOT fire)."""
    return bool(_NON_US_SIGNAL.search(expanded))


def _branch_modifier(expanded: str) -> Optional[str]:
    """Return an unrecognized branch-like adjective in the 'branch slot' right before the
    echelon noun ('fighter division', 'alpini division', 'volksgrenadier division'), or
    None. Signals a NON-infantry arm — captured as a distinct arm value so it vetoes
    against 'infantry'. Known arms and structural words return None (handled elsewhere).
    """
    m = re.search(rf"\b([a-z]+)\s+{_ECHELON_NOUNS}\b", expanded)
    if not m:
        return None
    word = m.group(1)
    if word in _SLOT_SKIP:
        return None
    # service words (marine/naval/air/…) and known arms are not 'unknown' arms
    if word in _SERVICE_TERMS or word in _ARM_TERMS:
        return None
    # a service-mapped token (e.g. "marine"->USMC) is a service, not an arm
    if any(word == k for k in _SERVICE_TERMS):
        return None
    if re.fullmatch(r"\d+(?:st|nd|rd|th|d)?", word):
        return None
    # ordinal words in the slot ("ninth division") are the NUMBER, not a branch
    if word in _ORDINAL_WORDS or word in _ROMAN:
        return None
    return word


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

    cc = re.search(r"\bcc[\s\-]*([abr])\b", expanded) or re.search(
        r"\bcombat command\s+([abr])\b", expanded
    )
    numbers = set(_numbers(source))
    service = _first_term(expanded, _SERVICE_TERMS) or "ARMY"
    arm = _first_term(expanded, _ARM_TERMS)
    echelon = _first_term(expanded, _ECHELON_TERMS)

    if cc:
        # CC letter is the discriminating designator; a CC is an armored combined-arms
        # formation of an armored division.
        numbers.add(f"cc{cc.group(1)}")
        echelon = "combat_command"

    # Exclusion modifier ("3d Armored Division (less CCB)", "... minus CCA", "(-)"):
    # a task-tailored formation MINUS a component is NOT the whole formation, and NOT the
    # excluded component. Stamp a distinguishing token so its key differs from both (rides
    # the number-set veto in unit_keys_match). The excluded component (if named) is folded
    # in so "less CCA" and "less CCB" stay distinct too.
    m_excl = re.search(r"\b(?:less|minus)\s+([a-z0-9]+)\b", expanded)
    if m_excl:
        numbers.add(f"less-{m_excl.group(1)}")
    elif re.search(r"\(\s*-\s*\)", expanded):
        numbers.add("less-x")

    arm = _resolve_arm(arm, expanded, service, echelon, bool(cc), infantry_default)
    return UnitKey(
        numbers=frozenset(numbers), service=service, arm=arm, echelon=echelon
    )


def _resolve_arm(arm, expanded, service, echelon, is_cc, infantry_default):
    """Resolve the combat arm: a CC is armored; an explicit-but-unknown branch modifier
    is captured (so it vetoes vs infantry); otherwise apply the US-only infantry default
    at division/regiment when no non-US signal is present."""
    if arm:
        return arm
    if is_cc:
        return "armored"
    unknown_mod = _branch_modifier(expanded)
    if unknown_mod:
        return unknown_mod
    # Infantry default is PURELY a US Army designation (bare "9th Division" = infantry),
    # only at division/regiment, and only without a non-US nationality/formation signal.
    if (
        service == "ARMY"
        and infantry_default
        and echelon in ("division", "regiment")
        and not _non_us_signal(expanded)
    ):
        return "infantry"
    return None


def unit_keys_match(k1: UnitKey, k2: UnitKey) -> tuple[bool, str]:
    """Apply the match rule. Returns (match, reason)."""
    if k1.numbers != k2.numbers:
        return False, "number mismatch"
    if not k1.numbers:
        return False, "no unit number"
    # A bare Combat Command (letter, no parent division) is underspecified — can't match.
    if _is_bare_combat_command(k1.numbers):
        return False, "combat command without parent division (underspecified)"
    if k1.service != k2.service:
        return False, f"service mismatch ({k1.service} vs {k2.service})"
    if k1.arm and k2.arm and k1.arm != k2.arm:
        return False, f"arm mismatch ({k1.arm} vs {k2.arm})"
    if k1.echelon and k2.echelon and k1.echelon != k2.echelon:
        return False, f"echelon mismatch ({k1.echelon} vs {k2.echelon})"
    arm = k1.arm or k2.arm or "unspecified"
    ech = k1.echelon or k2.echelon or "unspecified"
    return (
        True,
        f"canonical unit key match (#{sorted(k1.numbers)} {k1.service} {arm} {ech})",
    )


def _is_bare_combat_command(numbers) -> bool:
    """True if the number-set has a CC letter but NO parent-division number — a bare
    Combat Command is underspecified (composition is task-organized; letter+division is
    the only reliable identity)."""
    nums = [str(n) for n in numbers]
    has_cc = any(n.startswith("cc") for n in nums)
    has_division_number = any(not n.startswith("cc") for n in nums)
    return has_cc and not has_division_number

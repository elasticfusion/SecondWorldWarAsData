#!/usr/bin/env python3
"""Identify possible duplicate people groups based on name similarity."""

import json
import logging
import re
import sys
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

SKIP_FILES = {
    "index.json",
    "duplicate_report.json",
    "related_groups_report.json",
    "not_related.json",
}


def _normalize(name: str) -> str:
    """Normalize group name for comparison."""
    name = name.lower().strip()
    # Remove common suffixes/prefixes that vary
    for noise in ["the ", "u.s. ", "us "]:
        if name.startswith(noise):
            name = name[len(noise) :]
    return name


def _is_substring_match(a: str, b: str) -> bool:
    """Check if one name is a substring of the other."""
    na, nb = _normalize(a), _normalize(b)
    return na in nb or nb in na


def _similarity(a: str, b: str) -> float:
    """Fuzzy similarity between two group names."""
    na, nb = _normalize(a), _normalize(b)
    return SequenceMatcher(None, na, nb).ratio()


def find_duplicate_groups(groups_dir: Path) -> List[Dict]:
    """Find potential duplicate groups by name similarity."""
    groups = _load_groups(groups_dir)
    excluded_pairs, excluded_names = _load_group_exclusions(groups_dir)

    # Group dedup config + a text index for proximity corroboration (best-effort).
    from src.dedup.config import load_group_dedup_config

    try:
        from src.utils.config import load_config

        gcfg = load_group_dedup_config(load_config())
    except Exception:
        gcfg = load_group_dedup_config(None)
    text_index: dict = {}
    if gcfg.proximity.get("enabled", True):
        try:
            from scripts.find_duplicate_people import _build_text_index

            output_root = groups_dir.parent
            text_index = _build_text_index(output_root)
        except Exception:
            text_index = {}

    # Incremental: only process clusters containing new files
    from src.dedup.incremental import get_last_dedup_run, get_new_files

    since = get_last_dedup_run("groups")
    new_files = get_new_files(groups_dir, since)
    if new_files:
        logger.info(
            "Incremental dedup: %d new group files since last run", len(new_files)
        )

    duplicates: List[Dict[str, Any]] = []
    seen: set = set()

    for i, g1 in enumerate(groups):
        if i in seen:
            continue
        cluster, reasons = _find_group_cluster(
            i, g1, groups, seen, gcfg=gcfg, text_index=text_index
        )
        if len(cluster) >= 2:
            # Incremental: skip cluster if no member is new
            if new_files and not any(g["filename"] in new_files for g in cluster):
                continue
            seen.add(i)
            duplicates.append(_build_group(cluster, reasons))

    duplicates.sort(key=lambda x: float(x["confidence"]), reverse=True)
    filtered = _filter_excluded(duplicates, excluded_pairs, excluded_names)

    from src.dedup.exclusions import load_reviewed_pairs

    reviewed = load_reviewed_pairs("groups")
    if reviewed:
        remaining = []
        for g in filtered:
            filenames = [p["filename"] for p in g["people"]]
            all_reviewed = all(
                tuple(sorted([filenames[i], filenames[j]])) in reviewed
                for i in range(len(filenames))
                for j in range(i + 1, len(filenames))
            )
            if not all_reviewed:
                remaining.append(g)
        filtered = remaining
    return filtered


def _load_group_exclusions(groups_dir: Path) -> tuple:
    """Load excluded pairs from DynamoDB or local JSON."""
    from src.dedup.exclusions import get_exclusion_store

    store = get_exclusion_store("groups", groups_dir)
    return store.load(), store.load_name_exclusions()


def _build_group(cluster: list, reasons: set) -> Dict[str, Any]:
    """Build a duplicate group dict from a cluster."""
    confidence = max(
        _similarity(a["name"], b["name"])
        for a in cluster
        for b in cluster
        if a is not b
    )
    return {
        "confidence": round(confidence, 2),
        "reasons": sorted(reasons),
        "people": [
            {
                "name": g["name"],
                "filename": g["filename"],
                "GroupID": g["data"].get("GroupID", ""),
                "group_type": g["data"].get("group_type", ""),
            }
            for g in cluster
        ],
    }


def _filter_excluded(
    duplicates: List[Dict[str, Any]], excluded_pairs: set, excluded_names: set
) -> List[Dict[str, Any]]:
    """Remove groups where all pairs are excluded (by filename or name)."""
    if not excluded_pairs and not excluded_names:
        return duplicates
    from src.dedup.exclusions import _normalize_exclusion_name

    filtered = []
    for dup in duplicates:
        filenames = [p["filename"] for p in dup["people"]]
        names = [p["name"] for p in dup["people"]]
        all_excluded = True
        for i, (a, na) in enumerate(zip(filenames, names)):
            for b, nb in zip(filenames[i + 1 :], names[i + 1 :]):
                file_pair = tuple(sorted([a, b]))
                name_pair = tuple(
                    sorted(
                        [_normalize_exclusion_name(na), _normalize_exclusion_name(nb)]
                    )
                )
                if file_pair not in excluded_pairs and name_pair not in excluded_names:
                    all_excluded = False
                    break
            if not all_excluded:
                break
        if not all_excluded:
            filtered.append(dup)
    return filtered


def _load_groups(groups_dir: Path) -> list:
    """Load group data from local files + index.json for non-local entries."""
    groups = []
    seen_filenames: set = set()
    for f in sorted(groups_dir.glob("*.json")):
        if f.name in SKIP_FILES:
            continue
        seen_filenames.add(f.name)
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            name = data.get("group_name", data.get("name", ""))
            if name:
                groups.append({"name": name, "filename": f.name, "data": data})
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("Skipping %s: %s", f.name, e)

    index_file = groups_dir / "index.json"
    if index_file.exists():
        try:
            raw = json.loads(index_file.read_text(encoding="utf-8"))
            for name, filename in raw.items():
                if filename not in seen_filenames and filename not in SKIP_FILES:
                    groups.append({"name": name, "filename": filename, "data": {}})
        except (json.JSONDecodeError, OSError):
            pass

    return groups


ORDINAL_MAP = {
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
}

ROMAN_MAP = {
    "i": 1,
    "ii": 2,
    "iii": 3,
    "iv": 4,
    "v": 5,
    "vi": 6,
    "vii": 7,
    "viii": 8,
    "ix": 9,
    "x": 10,
    "xi": 11,
    "xii": 12,
    "xiii": 13,
    "xiv": 14,
    "xv": 15,
    "xvi": 16,
    "xvii": 17,
    "xviii": 18,
    "xix": 19,
    "xx": 20,
    "xxi": 21,
    "xxx": 30,
    "xl": 40,
    "xlvii": 47,
    "xlviii": 48,
    "l": 50,
    "lviii": 58,
    "lxiv": 64,
    "lxvii": 67,
    "lxx": 70,
    "lxxiv": 74,
    "lxxx": 80,
    "lxxxi": 81,
    "lxxxiv": 84,
    "lxxxvi": 86,
    "lxxxviii": 88,
}


def _extract_numbers(name: str) -> set:
    """Extract all numbers as canonical strings. Arabic stays arabic, roman stays roman."""
    lower = name.lower()
    nums = set()
    # Normalize ordinal suffixes: 2d→2, 3d→3, 1st→1, 2nd→2, 3rd→3, 4th→4
    for m in re.finditer(r"(\d+)(?:st|nd|rd|th|d)\b", lower):
        nums.add(m.group(1))
    # Plain arabic numbers
    for m in re.finditer(r"\b(\d+)\b", lower):
        nums.add(m.group(1))
    # Ordinal words → arabic
    for word in lower.split():
        if word in ORDINAL_MAP:
            nums.add(ORDINAL_MAP[word])
    # Roman numerals kept as-is (not converted to arabic)
    for word in lower.split():
        if word in ROMAN_MAP:
            nums.add(f"r{ROMAN_MAP[word]}")
    return nums


def _numbers_match(name1: str, name2: str) -> bool:
    """Return True if both names have the same numbers, or neither has numbers."""
    n1 = _extract_numbers(name1)
    n2 = _extract_numbers(name2)
    if not n1 and not n2:
        return True
    return n1 == n2


def _find_group_cluster(i, g1, groups, seen, **kwargs):
    """Find all groups matching g1. Returns (cluster, reasons)."""
    from src.dedup.unit_key import derive_unit_key, unit_keys_match

    gcfg = kwargs.get("gcfg")
    text_index = kwargs.get("text_index") or {}
    cluster = [g1]
    reasons = set()
    key1 = derive_unit_key(g1["name"])
    for j, g2 in enumerate(groups[i + 1 :], i + 1):
        if j in seen:
            continue
        if not _numbers_match(g1["name"], g2["name"]):
            continue

        # PRIMARY: canonical unit key (number + branch[infantry default] + echelon).
        # A key match clusters; a key VETO (branch/echelon mismatch) blocks the pair
        # even if the raw strings look similar ("9th Armored" vs "9th Division") AND
        # even if they are adjacent in the text (a veto always wins over proximity).
        key2 = derive_unit_key(g2["name"])
        if key1.numbers and key2.numbers:
            # Nationality VETO: "2nd Division (Canadian)" != "2nd Division (US)".
            if _group_nationality_conflict(g1, g2):
                continue
            matched, why = unit_keys_match(key1, key2)
            if matched:
                cluster.append(g2)
                reasons.add(why)
                # Proximity corroboration: note when the match is also text-adjacent
                # (e.g. "110th Regiment" closely followed by "110th").
                if _groups_proximate(g1, g2, gcfg, text_index):
                    reasons.add("text proximity corroboration")
                seen.add(j)
            # else: vetoed or number-mismatch — do NOT fall through to string sim.
            continue

        # FALLBACK (only when a canonical key can't be formed, e.g. no number):
        # weak string-similarity / substring match, as before.
        sim = _similarity(g1["name"], g2["name"])
        if sim >= 0.85:
            cluster.append(g2)
            reasons.add(f"name similarity {sim:.0%}")
            seen.add(j)
        elif (
            _is_substring_match(g1["name"], g2["name"])
            and len(_normalize(g1["name"])) >= 3
        ):
            cluster.append(g2)
            reasons.add("substring match")
            seen.add(j)
    return cluster, reasons


def _group_nationality(g: Dict) -> Optional[str]:
    """Canonical nationality for a group: stored field first, else a nationality word
    embedded in the name ('2nd Division (Canadian)')."""
    data = g.get("data", g) if isinstance(g, dict) else {}
    raw = data.get("nationality") or data.get("country_of_origin") or ""
    name = g.get("name", "")
    try:
        from src.enrichment.award_sources import canonical_nationality

        c = canonical_nationality(raw)
        if c:
            return c
        # scan the name for a nationality adjective/code
        for token in re.split(r"[^a-zA-Z]+", name):
            c = canonical_nationality(token)
            if c:
                return c
    except Exception:
        return (raw or "").strip().upper() or None
    return None


def _group_nationality_conflict(g1: Dict, g2: Dict) -> bool:
    """True only when BOTH groups have a known nationality and they DIFFER — a strong
    veto ('2nd Division (Canadian)' != '2nd Division (US)'). Unknown on either side is
    permissive (no veto)."""
    n1 = _group_nationality(g1)
    n2 = _group_nationality(g2)
    return bool(n1 and n2 and n1 != n2)


def _groups_proximate(g1: Dict, g2: Dict, gcfg, text_index: Dict) -> bool:
    """True if the two group names appear within the configured 'near' radius in the
    source text of a shared sub-event. Corroboration only (never a veto override)."""
    if not gcfg or not text_index or not gcfg.proximity.get("enabled", True):
        return False
    try:
        from scripts.find_duplicate_people import _min_name_distance

        d = _min_name_distance(g1.get("data", g1), g2.get("data", g2), text_index)
        if d is None:
            return False
        return d <= gcfg.proximity.get("near_radius_words", 400)
    except Exception:
        return False


def generate_duplicate_report(groups_dir: Path, output_file: Path) -> None:
    """Generate duplicate groups report."""
    duplicates = find_duplicate_groups(groups_dir)

    from src.dedup.validation import validate_report_groups

    duplicates = validate_report_groups(duplicates, groups_dir)

    report = {
        "total_groups": len(
            [f for f in groups_dir.glob("*.json") if f.name not in SKIP_FILES]
        ),
        "duplicate_groups": len(duplicates),
        "duplicates": duplicates,
    }

    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)

    logger.info("Found %d potential duplicate groups", len(duplicates))
    logger.info("Report saved to: %s", output_file)

    print(f"\nTotal groups: {report['total_groups']}")
    print(f"Duplicate groups found: {len(duplicates)}")
    for i, dup in enumerate(duplicates[:10], 1):
        print(
            f"\n{i}. Confidence: {dup['confidence']:.2f} ({', '.join(dup['reasons'])})"
        )
        for g in dup["people"]:
            print(f"   - {g['name']} ({g['filename']})")
    if len(duplicates) > 10:
        print(f"\n... and {len(duplicates) - 10} more groups")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    script_dir = Path(__file__).parent
    project_root = script_dir.parent if script_dir.name == "scripts" else script_dir

    groups_dir = project_root / "output/people_groups"
    output_file = groups_dir / "duplicate_report.json"

    if not groups_dir.exists():
        logger.error("People groups directory not found: %s", groups_dir)
        sys.exit(1)

    generate_duplicate_report(groups_dir, output_file)

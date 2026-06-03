#!/usr/bin/env python3
"""Identify possible duplicate people groups based on name similarity."""

import json
import logging
import re
import sys
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Dict, List

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

    duplicates: List[Dict[str, Any]] = []
    seen: set = set()

    for i, g1 in enumerate(groups):
        if i in seen:
            continue
        cluster, reasons = _find_group_cluster(i, g1, groups, seen)
        if len(cluster) >= 2:
            seen.add(i)
            duplicates.append(_build_group(cluster, reasons))

    duplicates.sort(key=lambda x: float(x["confidence"]), reverse=True)
    return _filter_excluded(duplicates, excluded_pairs, excluded_names)


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


def _find_group_cluster(i, g1, groups, seen):
    """Find all groups matching g1. Returns (cluster, reasons)."""
    cluster = [g1]
    reasons = set()
    for j, g2 in enumerate(groups[i + 1 :], i + 1):
        if j in seen:
            continue
        if not _numbers_match(g1["name"], g2["name"]):
            continue
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

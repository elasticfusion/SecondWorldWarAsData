#!/usr/bin/env python3
"""Find duplicate equipment files based on name similarity and shared attributes."""

import json
import logging
import sys
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Dict, List, Set

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

SKIP_FILES = {
    "index.json",
    ".processed_events.json",
    "duplicate_report.json",
    "not_duplicates.json",
}


def _normalize(name: str) -> str:
    """Normalize equipment name for comparison."""
    import re

    name = name.lower().strip()
    # Strip leading dots: ".50-caliber" -> "50-caliber"
    name = re.sub(r"^\.(\d)", r"\1", name)
    # Normalize "50-caliber" / "50 caliber" -> "50 cal"
    name = re.sub(r"(\d+)\s*-?\s*caliber", r"\1 cal", name)
    name = re.sub(r"(\d+)\s*-?\s*cal\b", r"\1 cal", name)
    # Normalize mm formats: "155-mm" / "155 mm" -> "155mm"
    name = re.sub(r"(\d+)\s*-?\s*mm\b", r"\1mm", name)
    # Normalize cm: "7.5-cm" -> "7.5cm"
    name = re.sub(r"(\d+\.?\d*)\s*-?\s*cm\b", r"\1cm", name)
    name = re.sub(r"[_\-]+", " ", name)
    return name


def _similarity(a: str, b: str) -> float:
    """Similarity ratio between two strings."""
    return SequenceMatcher(None, _normalize(a), _normalize(b)).ratio()


def _load_aliases() -> Dict[str, str]:
    """Load equipment alias table."""
    import yaml

    alias_file = Path(__file__).parent.parent / "config" / "equipment_aliases.yaml"
    if alias_file.exists():
        try:
            data = yaml.safe_load(alias_file.read_text(encoding="utf-8"))
            return {k.lower(): v.lower() for k, v in data.get("aliases", {}).items()}
        except Exception:
            pass
    return {}


_EQUIPMENT_ALIASES: Dict[str, str] = _load_aliases()


def _all_names(equip: Dict[str, Any]) -> List[str]:
    """Get primary names for an equipment item, including alias resolutions."""
    names = [equip.get("common_name", ""), equip.get("technical_identifier", "")]
    names.extend(equip.get("alternate_names", []))
    names = [n for n in names if n]
    # Add canonical names from alias table
    for n in list(names):
        canonical = _EQUIPMENT_ALIASES.get(n.lower())
        if canonical and canonical not in [x.lower() for x in names]:
            names.append(canonical)
    return names


def _best_name_match(equip1: Dict, equip2: Dict) -> float:
    """Best similarity between any pair of names from two equipment items."""
    names1 = _all_names(equip1)
    names2 = _all_names(equip2)
    if not names1 or not names2:
        return 0.0
    return max(_similarity(n1, n2) for n1 in names1 for n2 in names2)


def _same_category(equip1: Dict, equip2: Dict) -> bool:
    """Check if equipment shares a category."""
    c1 = equip1.get("category", "").lower()
    c2 = equip2.get("category", "").lower()
    return bool(c1 and c2 and c1 == c2)


def _name_contained(equip1: Dict, equip2: Dict) -> bool:
    """Check if one equipment's name is contained in the other's names."""
    names1 = [_normalize(n) for n in _all_names(equip1)]
    names2 = [_normalize(n) for n in _all_names(equip2)]
    for n1 in names1:
        for n2 in names2:
            # Require meaningful length and that the shorter name is >60% of the longer
            shorter, longer = sorted([n1, n2], key=len)
            if (
                len(shorter) >= 8
                and shorter in longer
                and len(shorter) / len(longer) > 0.6
            ):
                return True
    return False


def _load_exclusions(equipment_dir: Path) -> tuple:
    """Load excluded pairs from DynamoDB or local JSON."""
    from src.dedup.exclusions import get_exclusion_store

    store = get_exclusion_store("equipment", equipment_dir)
    return store.load(), store.load_name_exclusions()


def _score_pair(item1: Dict, item2: Dict) -> tuple:
    """Score a pair of equipment items. Returns (confidence, reasons, best_match)."""
    import re

    name1 = item1.get("common_name", item1.get("name", ""))
    name2 = item2.get("common_name", item2.get("name", ""))

    # Reject if leading numbers differ (e.g., "105 mm" vs "155 mm")
    num1 = re.match(r"(\d+)", name1)
    num2 = re.match(r"(\d+)", name2)
    if num1 and num2 and num1.group(1) != num2.group(1):
        return 0.0, [], 0.0

    # Reject if countries differ (unless one is marked as captured equipment)
    country1 = item1.get("country_of_origin", "")
    country2 = item2.get("country_of_origin", "")
    if country1 and country2 and country1 != country2:
        captured1 = "captured" in str(item1.get("context", "")).lower()
        captured2 = "captured" in str(item2.get("context", "")).lower()
        if not captured1 and not captured2:
            return 0.0, [], 0.0

    confidence = 0.0
    reasons = []

    best_match = _best_name_match(item1, item2)
    if best_match >= 0.85:
        confidence += 0.5
        reasons.append(f"name_similarity={best_match:.2f}")
    elif best_match >= 0.7:
        confidence += 0.3
        reasons.append(f"name_similarity={best_match:.2f}")

    if _name_contained(item1, item2):
        confidence += 0.2
        reasons.append("name_contained")

    if _same_category(item1, item2):
        confidence += 0.1
        reasons.append("same_category")

    return confidence, reasons, best_match


def _make_group(item1: Dict, item2: Dict, confidence: float, reasons: list) -> Dict:
    """Create a duplicate group entry from a pair."""
    return {
        "people": [
            {
                "filename": item1["_filename"],
                "name": item1.get("common_name", "?"),
                "EquipmentID": item1.get("EquipmentID", ""),
            },
            {
                "filename": item2["_filename"],
                "name": item2.get("common_name", "?"),
                "EquipmentID": item2.get("EquipmentID", ""),
            },
        ],
        "confidence": confidence,
        "reasons": list(dict.fromkeys(reasons)),
    }


def _load_equipment_items(equipment_dir: Path) -> list:
    """Load equipment data from files and index."""
    items = []
    seen_filenames: Set[str] = set()
    for ef in sorted(equipment_dir.glob("*.json")):
        if ef.name in SKIP_FILES:
            continue
        seen_filenames.add(ef.name)
        try:
            with open(ef, "r", encoding="utf-8") as f:
                data = json.load(f)
                data["_filename"] = ef.name
                items.append(data)
        except (json.JSONDecodeError, OSError):
            pass

    index_file = equipment_dir / "index.json"
    if index_file.exists():
        try:
            raw = json.loads(index_file.read_text(encoding="utf-8"))
            for name, filename in raw.items():
                if filename not in seen_filenames and filename not in SKIP_FILES:
                    items.append(
                        {"common_name": name, "name": name, "_filename": filename}
                    )
        except (json.JSONDecodeError, OSError):
            pass
    return items


def _is_excluded(
    item1: dict, item2: dict, excluded_pairs: set, excluded_names: set
) -> bool:
    """Check if a pair is excluded by filename or name."""
    pair_key = tuple(sorted([item1["_filename"], item2["_filename"]]))
    if pair_key in excluded_pairs:
        return True
    if excluded_names:
        from src.dedup.exclusions import _normalize_exclusion_name

        name1 = item1.get("common_name", item1.get("name", ""))
        name2 = item2.get("common_name", item2.get("name", ""))
        name_pair = tuple(
            sorted([_normalize_exclusion_name(name1), _normalize_exclusion_name(name2)])
        )
        if name_pair in excluded_names:
            return True
    return False


def find_potential_duplicates(equipment_dir: Path) -> List[Dict[str, Any]]:
    """Find potential duplicate equipment based on name similarity and category."""
    excluded_pairs, excluded_names = _load_exclusions(equipment_dir)
    items = _load_equipment_items(equipment_dir)
    logger.info("Analyzing %d equipment items for duplicates...", len(items))

    # Load recently-reviewed pairs to suppress
    from src.dedup.exclusions import load_reviewed_pairs

    reviewed = load_reviewed_pairs("equipment")

    # Incremental: only compare pairs where at least one file is new
    from src.dedup.incremental import get_last_dedup_run, get_new_files, should_compare

    since = get_last_dedup_run("equipment")
    new_files = get_new_files(equipment_dir, since)
    if new_files:
        logger.info(
            "Incremental dedup: %d new equipment files since last run", len(new_files)
        )

    # Score all pairs
    pairs = []
    for i, item1 in enumerate(items):
        for j in range(i + 1, len(items)):
            item2 = items[j]
            if not should_compare(item1["_filename"], item2["_filename"], new_files):
                continue
            if _is_excluded(item1, item2, excluded_pairs, excluded_names):
                continue
            if reviewed:
                pair_key = tuple(sorted([item1["_filename"], item2["_filename"]]))
                if pair_key in reviewed:
                    continue
            confidence, reasons, best_match = _score_pair(item1, item2)
            if confidence > 0.5 and best_match >= 0.7:
                pairs.append((item1, item2, confidence, reasons))

    # Group as pairs only (no chaining)
    duplicates = []
    processed: Set[str] = set()
    for item1, item2, confidence, reasons in pairs:
        if item1["_filename"] in processed or item2["_filename"] in processed:
            continue
        duplicates.append(_make_group(item1, item2, confidence, reasons))
        processed.add(item1["_filename"])
        processed.add(item2["_filename"])

    return duplicates


def generate_duplicate_report(equipment_dir: Path, output_file: Path) -> None:
    """Generate duplicate report."""
    duplicates = find_potential_duplicates(equipment_dir)

    from src.dedup.validation import validate_report_groups

    duplicates = validate_report_groups(duplicates, equipment_dir)
    report = {
        "total_equipment": len(
            [f for f in equipment_dir.glob("*.json") if f.name not in SKIP_FILES]
        ),
        "duplicate_groups": len(duplicates),
        "duplicates": duplicates,
    }
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)
    logger.info("Found %d duplicate group(s)", len(duplicates))
    logger.info("Report saved to %s", output_file)


if __name__ == "__main__":
    script_dir = Path(__file__).parent
    project_root = script_dir.parent if script_dir.name == "scripts" else script_dir
    equipment_dir = project_root / "output" / "equipment"

    if not equipment_dir.exists():
        logger.error("Equipment directory not found: %s", equipment_dir)
        sys.exit(1)

    output_file = equipment_dir / "duplicate_report.json"
    generate_duplicate_report(equipment_dir, output_file)

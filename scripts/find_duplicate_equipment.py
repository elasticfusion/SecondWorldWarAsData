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

SKIP_FILES = {"index.json", ".processed_events.json", "duplicate_report.json", "not_duplicates.json"}


def _normalize(name: str) -> str:
    """Normalize equipment name for comparison."""
    import re
    name = name.lower().strip()
    # Normalize caliber formats: ".50-caliber" -> "50 caliber", "155-mm" -> "155mm"
    name = re.sub(r"^\.(\d)", r"\1", name)
    name = re.sub(r"(\d+)\s*-\s*mm", r"\1mm", name)
    name = re.sub(r"[_\-]+", " ", name)
    return name


def _similarity(a: str, b: str) -> float:
    """Similarity ratio between two strings."""
    return SequenceMatcher(None, _normalize(a), _normalize(b)).ratio()


def _all_names(equip: Dict[str, Any]) -> List[str]:
    """Get primary names for an equipment item (excludes variants)."""
    names = [equip.get("common_name", ""), equip.get("technical_identifier", "")]
    names.extend(equip.get("alternate_names", []))
    return [n for n in names if n]


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
            if len(shorter) >= 8 and shorter in longer and len(shorter) / len(longer) > 0.6:
                return True
    return False


def find_potential_duplicates(equipment_dir: Path) -> List[Dict[str, Any]]:
    """Find potential duplicate equipment based on name similarity and category."""
    # Load exclusions
    exclusion_file = equipment_dir / "not_duplicates.json"
    excluded_pairs: Set[tuple] = set()
    if exclusion_file.exists():
        with open(exclusion_file, "r", encoding="utf-8") as f:
            data = json.load(f)
            for pair in data.get("exclusions", []):
                excluded_pairs.add(tuple(sorted([pair["file1"], pair["file2"]])))

    # Load all equipment
    equipment_files = [
        f for f in sorted(equipment_dir.glob("*.json")) if f.name not in SKIP_FILES
    ]
    items = []
    for ef in equipment_files:
        with open(ef, "r", encoding="utf-8") as f:
            data = json.load(f)
            data["_filename"] = ef.name
            items.append(data)

    logger.info("Analyzing %d equipment items for duplicates...", len(items))

    # Build pairwise matches (no transitive chaining)
    pairs = []
    for i, item1 in enumerate(items):
        for j in range(i + 1, len(items)):
            item2 = items[j]
            pair_key = tuple(sorted([item1["_filename"], item2["_filename"]]))
            if pair_key in excluded_pairs:
                continue

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

            # Require name similarity as primary signal
            if confidence > 0.5 and best_match >= 0.7:
                pairs.append((item1, item2, confidence, reasons))

    # Group as pairs only (no chaining)
    duplicates = []
    processed: Set[str] = set()
    for item1, item2, confidence, reasons in pairs:
        f1, f2 = item1["_filename"], item2["_filename"]
        if f1 in processed or f2 in processed:
            continue
        duplicates.append({
            "people": [
                {"filename": f1, "name": item1.get("common_name", "?"),
                 "EquipmentID": item1.get("EquipmentID", "")},
                {"filename": f2, "name": item2.get("common_name", "?"),
                 "EquipmentID": item2.get("EquipmentID", "")},
            ],
            "confidence": confidence,
            "reasons": list(dict.fromkeys(reasons)),
        })
        processed.add(f1)
        processed.add(f2)

    return duplicates


def generate_duplicate_report(equipment_dir: Path, output_file: Path) -> None:
    """Generate duplicate report."""
    duplicates = find_potential_duplicates(equipment_dir)
    report = {
        "total_equipment": len([
            f for f in equipment_dir.glob("*.json") if f.name not in SKIP_FILES
        ]),
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

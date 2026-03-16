#!/usr/bin/env python3
"""
Identify possible duplicate people based on spelling variations and aliases.
"""

import json
import logging
import sys
import unicodedata
from functools import lru_cache
from pathlib import Path
from typing import List, Dict, Any, Set
from difflib import SequenceMatcher

logger = logging.getLogger(__name__)


@lru_cache(maxsize=10000)
def _normalize_unicode(text: str) -> str:
    """Normalize Unicode to ASCII for comparison (ö -> o, é -> e)."""
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")


def _normalize_german(text: str) -> str:
    """Normalize German umlauts to their common transliterations."""
    replacements = {
        "ö": "oe",
        "ä": "ae",
        "ü": "ue",
        "Ö": "Oe",
        "Ä": "Ae",
        "Ü": "Ue",
        "ß": "ss",
    }
    for umlaut, replacement in replacements.items():
        text = text.replace(umlaut, replacement)
    return text


@lru_cache(maxsize=10000)
def _similarity_ratio(name1: str, name2: str) -> float:
    """Calculate similarity ratio between two names."""
    # Compare both original and ASCII-normalized versions
    original_ratio = SequenceMatcher(None, name1.lower(), name2.lower()).ratio()
    normalized_ratio = SequenceMatcher(
        None, _normalize_unicode(name1).lower(), _normalize_unicode(name2).lower()
    ).ratio()
    return max(original_ratio, normalized_ratio)


@lru_cache(maxsize=5000)
def _extract_last_name(name: str) -> str:
    """Extract likely last name from full name."""
    parts = name.split()
    if len(parts) > 1:
        # Skip titles/ranks
        titles = {
            "general",
            "colonel",
            "major",
            "captain",
            "lieutenant",
            "field",
            "marshal",
            "admiral",
            "commander",
            "sergeant",
        }
        filtered = [p for p in parts if p.lower() not in titles]
        if filtered:
            return filtered[-1].lower()
    return name.lower()


def _has_shared_biographical_data(person1: Dict, person2: Dict) -> bool:
    """Check if two people share biographical data."""
    bio1 = person1.get("biographical_profile", {})
    bio2 = person2.get("biographical_profile", {})

    if not bio1 or not bio2:
        return False

    # Check birth date
    if bio1.get("birth_date") and bio2.get("birth_date"):
        if bio1["birth_date"] == bio2["birth_date"]:
            return True

    # Check nationality + birth year
    if bio1.get("nationality") and bio2.get("nationality"):
        if bio1["nationality"] == bio2["nationality"]:
            birth1 = bio1.get("birth_date") or ""
            birth2 = bio2.get("birth_date") or ""
            if len(birth1) >= 4 and len(birth2) >= 4:
                if birth1[:4] == birth2[:4]:
                    return True

    return False


def _has_shared_positions(person1: Dict, person2: Dict) -> bool:
    """Check if two people held the same positions."""
    positions1 = set()
    positions2 = set()

    for mention in person1.get("event_mentions", []):
        pos = mention.get("position_at_event")
        if pos:
            positions1.add(pos.lower())

    for mention in person2.get("event_mentions", []):
        pos = mention.get("position_at_event")
        if pos:
            positions2.add(pos.lower())

    return bool(positions1 & positions2)


def find_potential_duplicates(people_dir_path: Path) -> List[Dict[str, Any]]:
    """
    Find potential duplicate people based on various heuristics.

    Returns list of duplicate groups with similarity scores.
    """
    # Load exclusion list
    exclusion_file = people_dir_path / "not_duplicates.json"
    excluded_pairs = set()
    if exclusion_file.exists():
        with open(exclusion_file, "r", encoding="utf-8") as f:
            data = json.load(f)
            for pair in data.get("exclusions", []):
                # Store as sorted tuple for bidirectional matching
                excluded_pairs.add(tuple(sorted([pair["person1"], pair["person2"]])))

    # Load all people files
    people_files = list(people_dir_path.glob("*.json"))
    # Exclude index.json and duplicate_report.json
    people_files = [
        f
        for f in people_files
        if f.name not in ["index.json", "duplicate_report.json", "not_duplicates.json"]
    ]

    people_data = []
    for person_file in people_files:
        with open(person_file, "r", encoding="utf-8") as f:
            data = json.load(f)
            data["_filename"] = person_file.name
            people_data.append(data)

    logger.info("Analyzing %d people for duplicates...", len(people_data))

    duplicates = []
    processed: Set[str] = set()

    for i, person1 in enumerate(people_data):
        if person1["_filename"] in processed:
            continue

        # Skip if person doesn't have a name
        if "name" not in person1:
            logger.warning(f"Skipping {person1['_filename']}: missing 'name' field")
            continue

        name1 = person1["name"]
        last_name1 = _extract_last_name(name1)

        group: List[Dict[str, Any]] = []
        group_reasons: List[str] = []
        group_confidence = 0.0

        for person2 in people_data[i + 1 :]:
            if person2["_filename"] in processed:
                continue

            # Skip if person doesn't have a name
            if "name" not in person2:
                logger.warning(f"Skipping {person2['_filename']}: missing 'name' field")
                continue

            # Check if this pair is in exclusion list
            pair_key = tuple(sorted([person1["_filename"], person2["_filename"]]))
            if pair_key in excluded_pairs:
                continue

            name2 = person2["name"]
            last_name2 = _extract_last_name(name2)

            reasons = []
            confidence = 0.0

            # Check 1: High name similarity (lowered threshold from 0.8 to 0.7)
            similarity = _similarity_ratio(name1, name2)
            if similarity > 0.7:
                reasons.append(f"Name similarity: {similarity:.2f}")
                confidence += similarity * 0.5  # Increased weight

            # Check 2: Same last name
            if last_name1 == last_name2 and len(last_name1) > 2:
                reasons.append(f"Same last name: {last_name1}")
                confidence += 0.4  # Increased weight

            # Check 3: Shared biographical data
            if _has_shared_biographical_data(person1, person2):
                reasons.append("Shared biographical data")
                confidence += 0.5

            # Check 4: Shared positions
            if _has_shared_positions(person1, person2):
                reasons.append("Shared positions")
                confidence += 0.3

            # Check 5: One name is substring of other (boosted)
            if last_name1 in name2.lower() or last_name2 in name1.lower():
                if len(last_name1) > 3:  # Lowered from 5
                    reasons.append("Name substring match")
                    confidence += 0.5  # Increased from 0.4

            # Check 5b: Single last name vs full name with same last name + shared bio
            if (
                len(name1.split()) == 1 or len(name2.split()) == 1
            ) and last_name1 == last_name2:
                # One is just a last name, other is full name
                if _has_shared_biographical_data(
                    person1, person2
                ) or _has_shared_positions(person1, person2):
                    reasons.append("Single name match with shared context")
                    confidence += 0.6

            # Check 6: ASCII/Unicode variants (Dönitz vs Doenitz)
            norm1_ascii = _normalize_unicode(name1).lower()
            norm2_ascii = _normalize_unicode(name2).lower()
            norm1_german = _normalize_german(name1).lower()
            norm2_german = _normalize_german(name2).lower()

            if norm1_ascii == norm2_ascii:
                reasons.append("ASCII/Unicode variant")
                confidence += 0.6
            elif (
                norm1_german == norm2_german
                or norm1_german == norm2_ascii
                or norm1_ascii == norm2_german
            ):
                reasons.append("German transliteration variant")
                confidence += 0.6

            # If confidence is high enough, add to group (lowered from 0.6 to 0.5)
            if confidence > 0.5 and reasons:
                if not group:
                    group.append(
                        {
                            "filename": person1["_filename"],
                            "name": name1,
                            "PersonID": person1.get("PersonID", ""),
                        }
                    )

                group.append(
                    {
                        "filename": person2["_filename"],
                        "name": name2,
                        "PersonID": person2.get("PersonID", ""),
                    }
                )

                # Accumulate confidence and reasons for the group
                group_confidence = max(group_confidence, confidence)
                group_reasons.extend(reasons)

                processed.add(person2["_filename"])

        if group:
            # Remove duplicate reasons
            group_reasons = list(set(group_reasons))
            duplicates.append(
                {
                    "confidence": group_confidence,
                    "reasons": group_reasons,
                    "people": group,
                }
            )
            processed.add(person1["_filename"])

    return duplicates


def generate_duplicate_report(people_dir_path: Path, output_file_path: Path) -> None:
    """Generate a report of potential duplicates."""
    duplicates = find_potential_duplicates(people_dir_path)

    # Sort by confidence
    duplicates.sort(key=lambda x: x["confidence"], reverse=True)

    report = {
        "total_people": len(list(people_dir_path.glob("*.json")))
        - 1,  # Exclude index.json
        "duplicate_groups": len(duplicates),
        "duplicates": duplicates,
    }

    with open(output_file_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)

    logger.info("Found %d potential duplicate groups", len(duplicates))
    logger.info("Report saved to: %s", output_file_path)

    # Print summary
    print("\n" + "=" * 80)
    print("POTENTIAL DUPLICATES REPORT")
    print("=" * 80)
    print(f"Total people: {report['total_people']}")
    print(f"Duplicate groups found: {len(duplicates)}")
    print()

    for i, dup in enumerate(duplicates[:10], 1):  # Show top 10
        print(f"{i}. Confidence: {dup['confidence']:.2f}")
        print(f"   Reasons: {', '.join(dup['reasons'])}")
        for person in dup["people"]:
            print(f"   - {person['name']} ({person['filename']})")
        print()

    if len(duplicates) > 10:
        print(f"... and {len(duplicates) - 10} more groups")
        print(f"See full report: {output_file_path}")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    # Find project root (where output/ directory exists)
    script_dir = Path(__file__).parent
    project_root = script_dir.parent if script_dir.name == "scripts" else script_dir

    people_dir = project_root / "output/people"
    output_file = project_root / "output/people/duplicate_report.json"

    if not people_dir.exists():
        logger.error("People directory not found: %s", people_dir)
        logger.info("Run phase2_extract.py first to extract people")
        sys.exit(1)

    # Check if directory has any JSON files
    people_files = [
        f
        for f in people_dir.glob("*.json")
        if f.name not in ["index.json", "duplicate_report.json"]
    ]
    if not people_files:
        logger.error("No people files found in: %s", people_dir)
        logger.info("Run phase2_extract.py first to extract people")
        sys.exit(1)

    logger.info("Found %d people file(s)", len(people_files))
    generate_duplicate_report(people_dir, output_file)

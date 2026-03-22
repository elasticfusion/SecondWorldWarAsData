#!/usr/bin/env python3
"""
Identify possible duplicate people based on spelling variations, aliases,
and text proximity (names appearing near each other in source text).
"""

import json
import logging
import re
import sys
import unicodedata
from functools import lru_cache
from pathlib import Path
from typing import List, Dict, Any, Set, Optional
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


# ---------------------------------------------------------------------------
# Text proximity helpers
# ---------------------------------------------------------------------------

_WORD_SPLIT = re.compile(r"\s+")

# People with more than this many event_mentions are considered high-frequency.
# For these, name-similarity alone is not enough — proximity or biographical
# evidence is required to flag a duplicate.
HIGH_FREQUENCY_THRESHOLD = 15


def _build_text_index(
    output_root: Path,
) -> Dict[str, List[str]]:
    """Build sub-event ID → concatenated fulltext mapping.

    Returns dict mapping Sub-eventID → full text string.
    """
    index: Dict[str, str] = {}
    for event_file in output_root.rglob("*-event.json"):
        if "notes-event" in event_file.name:
            continue
        try:
            data = json.loads(event_file.read_text(encoding="utf-8"))
            event = data.get("Event", data)
            for se in event.get("Sub-events", []):
                seid = se.get("Sub-eventID", "")
                ft = se.get("Sub-event_fulltext", {})
                if seid and ft:
                    index[seid] = " ".join(ft.values())
        except Exception:
            continue
    return index


def _person_sub_event_ids(person: Dict) -> Set[str]:
    """Get set of Sub-eventIDs a person is mentioned in."""
    return {
        m["Sub_eventID"]
        for m in person.get("event_mentions", [])
        if m.get("Sub_eventID")
    }


def _get_person_titles(person: Dict) -> List[str]:
    """Extract distinctive titles/ranks from biographical profile."""
    bp = person.get("biographical_profile", {})
    titles = []
    for rank_entry in bp.get("ranks", []):
        rank = rank_entry.get("rank", "")
        # Only keep multi-word titles (not generic "General", "Colonel")
        if rank and len(rank.split()) > 1:
            titles.append(rank.lower())
    return titles


def _names_within_distance(
    text: str, name1: str, name2: str, max_words: int = 1000
) -> bool:
    """Check if any occurrence of name1 is within max_words of name2 in text."""
    text_lower = text.lower()
    n1 = name1.lower()
    n2 = name2.lower()

    # Also check last-name-only variants
    last1 = _extract_last_name(name1)
    last2 = _extract_last_name(name2)
    variants1 = {n1, last1}
    variants2 = {n2, last2}

    words = _WORD_SPLIT.split(text_lower)
    word_text = " ".join(words)  # normalized spacing

    for v1 in variants1:
        for v2 in variants2:
            if v1 == v2:
                continue  # same string, skip
            # Find all positions of each variant
            pos1 = [m.start() for m in re.finditer(re.escape(v1), word_text)]
            pos2 = [m.start() for m in re.finditer(re.escape(v2), word_text)]
            if not pos1 or not pos2:
                continue
            for p1 in pos1:
                for p2 in pos2:
                    # Count words between the two positions
                    start = min(p1, p2)
                    end = max(p1, p2)
                    between = word_text[start:end]
                    word_count = len(_WORD_SPLIT.split(between))
                    if word_count <= max_words:
                        return True
    return False


def _check_proximity(
    person1: Dict,
    person2: Dict,
    text_index: Dict[str, str],
) -> bool:
    """Check if two people's names or titles appear near each other in source text."""
    name1 = person1.get("name", "")
    name2 = person2.get("name", "")
    if not name1 or not name2:
        return False

    # Get sub-events where either person is mentioned
    se_ids = _person_sub_event_ids(person1) | _person_sub_event_ids(person2)

    # Also check if one person's title appears near the other's name
    titles1 = _get_person_titles(person1)
    titles2 = _get_person_titles(person2)

    for seid in se_ids:
        text = text_index.get(seid, "")
        if not text:
            continue
        # Name-to-name proximity
        if _names_within_distance(text, name1, name2):
            return True
        # Title-to-name proximity (person1's title near person2's name)
        for title in titles1:
            if _names_within_distance(text, title, name2):
                return True
        for title in titles2:
            if _names_within_distance(text, title, name1):
                return True
    return False


def _check_name_similarity(name1: str, name2: str) -> tuple[list[str], float]:
    """Check 1: High name similarity."""
    similarity = _similarity_ratio(name1, name2)
    if similarity > 0.7:
        return [f"Name similarity: {similarity:.2f}"], similarity * 0.5
    return [], 0.0


def _check_same_last_name(last1: str, last2: str) -> tuple[list[str], float]:
    """Check 2: Same last name."""
    if last1 == last2 and len(last1) > 2:
        return [f"Same last name: {last1}"], 0.4
    return [], 0.0


def _check_shared_bio(
    p1: Dict, p2: Dict, has_name_match: bool
) -> tuple[list[str], float]:
    """Check 3: Shared biographical data (only boosts existing name match)."""
    if has_name_match and _has_shared_biographical_data(p1, p2):
        return ["Shared biographical data"], 0.5
    return [], 0.0


def _check_shared_positions(p1: Dict, p2: Dict) -> tuple[list[str], float]:
    """Check 4: Shared positions."""
    if _has_shared_positions(p1, p2):
        return ["Shared positions"], 0.3
    return [], 0.0


def _check_substring_match(
    name1: str, name2: str, last1: str, last2: str
) -> tuple[list[str], float]:
    """Check 5: One name's last name is a whole-word substring of the other."""
    if len(last1) > 3 and last1 != last2:
        pat1 = re.compile(r"\b" + re.escape(last1) + r"\b", re.IGNORECASE)
        pat2 = re.compile(r"\b" + re.escape(last2) + r"\b", re.IGNORECASE)
        if pat1.search(name2) or pat2.search(name1):
            return ["Name substring match"], 0.5
    return [], 0.0


def _check_single_name(
    name1: str, name2: str, last1: str, last2: str, p1: Dict, p2: Dict
) -> tuple[list[str], float]:
    """Check 5b: Single last name vs full name with shared context."""
    if (len(name1.split()) == 1 or len(name2.split()) == 1) and last1 == last2:
        if _has_shared_biographical_data(p1, p2) or _has_shared_positions(p1, p2):
            return ["Single name match with shared context"], 0.6
    return [], 0.0


def _check_unicode_variants(name1: str, name2: str) -> tuple[list[str], float]:
    """Check 6: ASCII/Unicode and German transliteration variants."""
    n1a = _normalize_unicode(name1).lower()
    n2a = _normalize_unicode(name2).lower()
    n1g = _normalize_german(name1).lower()
    n2g = _normalize_german(name2).lower()

    if n1a == n2a:
        return ["ASCII/Unicode variant"], 0.6
    if n1g == n2g or n1g == n2a or n1a == n2g:
        return ["German transliteration variant"], 0.6
    return [], 0.0


def _check_text_proximity(
    p1: Dict,
    p2: Dict,
    text_index: Dict,
    confidence: float,
    last1: str,
    last2: str,
    name1: str,
    name2: str,
) -> tuple[list[str], float]:
    """Check 7: Text proximity — names appear within 1000 words."""
    if not text_index or confidence <= 0.2:
        return [], 0.0
    similarity = _similarity_ratio(name1, name2)
    names_related = (
        last1 == last2
        or similarity > 0.8
        or len(name1.split()) == 1
        or len(name2.split()) == 1
    )
    if names_related and _check_proximity(p1, p2, text_index):
        return ["Text proximity (<1000 words)"], 0.4
    return [], 0.0


def _score_pair(
    person1: Dict, person2: Dict, text_index: Dict[str, str]
) -> tuple[list[str], float]:
    """Score a pair of people for duplicate likelihood."""
    name1 = person1["name"]
    name2 = person2["name"]
    last1 = _extract_last_name(name1)
    last2 = _extract_last_name(name2)

    all_reasons: list[str] = []
    total_confidence = 0.0

    checks = [
        _check_name_similarity(name1, name2),
        _check_same_last_name(last1, last2),
        _check_shared_positions(person1, person2),
        _check_substring_match(name1, name2, last1, last2),
        _check_single_name(name1, name2, last1, last2, person1, person2),
        _check_unicode_variants(name1, name2),
    ]

    for reasons, conf in checks:
        all_reasons.extend(reasons)
        total_confidence += conf

    # Check 3 depends on whether we have a name match
    r, c = _check_shared_bio(person1, person2, total_confidence > 0)
    all_reasons.extend(r)
    total_confidence += c

    # Check 7 depends on accumulated confidence
    r, c = _check_text_proximity(
        person1, person2, text_index, total_confidence, last1, last2, name1, name2
    )
    all_reasons.extend(r)
    total_confidence += c

    # High-frequency gate
    mentions1 = len(person1.get("event_mentions", []))
    mentions2 = len(person2.get("event_mentions", []))
    if mentions1 > HIGH_FREQUENCY_THRESHOLD or mentions2 > HIGH_FREQUENCY_THRESHOLD:
        has_strong = any(
            r
            for r in all_reasons
            if r.startswith(("Shared bio", "Shared pos", "Text prox"))
        )
        if not has_strong:
            total_confidence = 0.0

    return all_reasons, total_confidence


def find_potential_duplicates(
    people_dir_path: Path,
    output_root: Optional[Path] = None,
) -> List[Dict[str, Any]]:
    """
    Find potential duplicate people based on various heuristics.

    Args:
        people_dir_path: Path to output/people directory.
        output_root: Path to output/ root (for text proximity). If None,
                     proximity checking is skipped.

    Returns list of duplicate groups with similarity scores.
    """
    # Build text index for proximity checking
    text_index: Dict[str, str] = {}
    if output_root:
        logger.info("Building text index for proximity checking...")
        text_index = _build_text_index(output_root)
        logger.info("Indexed %d sub-events", len(text_index))
    # Load exclusion list
    exclusion_file = people_dir_path / "not_duplicates.json"
    excluded_pairs = set()
    if exclusion_file.exists():
        with open(exclusion_file, "r", encoding="utf-8") as f:
            data = json.load(f)
            for pair in data.get("exclusions", []):
                excluded_pairs.add(tuple(sorted([pair["person1"], pair["person2"]])))

    # Load all people files
    people_data = _load_people_data(people_dir_path)
    logger.info("Analyzing %d people for duplicates...", len(people_data))

    return _find_duplicate_groups(people_data, excluded_pairs, text_index)


def _load_people_data(people_dir_path: Path) -> List[Dict[str, Any]]:
    """Load all people JSON files."""
    skip = {"index.json", "duplicate_report.json", "not_duplicates.json"}
    people_data = []
    for person_file in people_dir_path.glob("*.json"):
        if person_file.name in skip:
            continue
        with open(person_file, "r", encoding="utf-8") as f:
            data = json.load(f)
            data["_filename"] = person_file.name
            people_data.append(data)
    return people_data


def _find_duplicate_groups(
    people_data: List[Dict[str, Any]],
    excluded_pairs: Set[tuple],
    text_index: Dict[str, str],
) -> List[Dict[str, Any]]:
    """Find duplicate groups from scored pairs."""
    duplicates = []
    processed: Set[str] = set()

    for i, person1 in enumerate(people_data):
        if person1["_filename"] in processed:
            continue
        if "name" not in person1:
            continue

        group: List[Dict[str, Any]] = []
        group_reasons: List[str] = []
        group_confidence = 0.0

        for person2 in people_data[i + 1 :]:
            if person2["_filename"] in processed or "name" not in person2:
                continue

            pair_key = tuple(sorted([person1["_filename"], person2["_filename"]]))
            if pair_key in excluded_pairs:
                continue

            reasons, confidence = _score_pair(person1, person2, text_index)

            if confidence > 0.5 and reasons:
                if not group:
                    group.append(
                        {
                            "filename": person1["_filename"],
                            "name": person1["name"],
                            "PersonID": person1.get("PersonID", ""),
                        }
                    )
                group.append(
                    {
                        "filename": person2["_filename"],
                        "name": person2["name"],
                        "PersonID": person2.get("PersonID", ""),
                    }
                )
                group_confidence = max(group_confidence, confidence)
                group_reasons.extend(reasons)
                processed.add(person2["_filename"])

        if group:
            duplicates.append(
                {
                    "confidence": group_confidence,
                    "reasons": list(set(group_reasons)),
                    "people": group,
                }
            )
            processed.add(person1["_filename"])

    return duplicates


def generate_duplicate_report(
    people_dir_path: Path,
    output_file_path: Path,
    output_root: Optional[Path] = None,
) -> None:
    """Generate a report of potential duplicates."""
    duplicates = find_potential_duplicates(people_dir_path, output_root=output_root)

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
    generate_duplicate_report(
        people_dir, output_file, output_root=project_root / "output"
    )

#!/usr/bin/env python3
"""
Find related people groups based on name similarity, hierarchy, and shared context.
Uses LLM to verify if groups are true duplicates vs hierarchically related.
"""

import json
import logging
import re
import sys
import unicodedata
from functools import lru_cache
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Dict, List, Set

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.grok_client import GrokClient

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def _load_excluded_clusters(groups_dir: Path) -> List[Set[str]]:
    """Load excluded cluster GroupIDs from excluded_merges.md."""
    excluded_file = groups_dir / "excluded_merges.md"
    if not excluded_file.exists():
        return []

    excluded_clusters = []
    current_cluster = set()
    in_code_block = False

    try:
        with open(excluded_file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()

                # Detect code block boundaries
                if line.startswith("```"):
                    if in_code_block and current_cluster:
                        # End of code block - save cluster
                        excluded_clusters.append(current_cluster)
                        current_cluster = set()
                    in_code_block = not in_code_block
                    continue

                # Inside code block - collect GroupIDs
                if in_code_block and line and not line.startswith("#"):
                    current_cluster.add(line)

        return excluded_clusters
    except Exception as e:
        logger.warning("Failed to load excluded clusters: %s", e)
        return []


def _is_cluster_excluded(
    group_ids: Set[str], excluded_clusters: List[Set[str]]
) -> bool:
    """Check if a cluster of GroupIDs matches any excluded cluster."""
    for excluded in excluded_clusters:
        # If all GroupIDs in the cluster are in an excluded set, skip it
        if group_ids.issubset(excluded) or excluded.issubset(group_ids):
            return True
    return False


@lru_cache(maxsize=10000)
def _normalize_unicode(text: str) -> str:
    """Normalize Unicode to ASCII for comparison."""
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")


def _verify_duplicate_with_llm(
    group1: Dict, group2: Dict, grok_client: GrokClient
) -> Dict[str, Any]:
    """Use LLM to verify if groups are true duplicates or hierarchically related."""
    prompt = f"""Analyze these two military organizations and determine their relationship:

Organization 1:
- Name: {_get_group_name(group1)}
- Type: {group1.get('group_type')}
- Country: {group1.get('country_of_origin')}
- Parent: {group1.get('parent_organization', 'None')}
- Description: {group1.get('description', 'None')[:200]}

Organization 2:
- Name: {_get_group_name(group2)}
- Type: {group2.get('group_type')}
- Country: {group2.get('country_of_origin')}
- Parent: {group2.get('parent_organization', 'None')}
- Description: {group2.get('description', 'None')[:200]}

Determine the relationship:
1. TRUE_DUPLICATE - Same entity with different names (e.g., "British Second Army" vs "2nd British Army")
2. HIERARCHICAL - Parent/child or subordinate relationship (e.g., "British Army" vs "British Second Army")
3. PARALLEL - Different entities at same level (e.g., "British Second Army" vs "British First Army")
4. UNRELATED - No meaningful relationship

Respond with JSON:
{{
  "relationship": "TRUE_DUPLICATE|HIERARCHICAL|PARALLEL|UNRELATED",
  "confidence": 0.0-1.0,
  "reasoning": "brief explanation"
}}"""

    try:
        response = grok_client.chat(prompt, response_format={"type": "json_object"})
        return json.loads(response)
    except Exception as e:
        logger.warning(f"LLM verification failed: {e}")
        return {"relationship": "UNKNOWN", "confidence": 0.0, "reasoning": "LLM error"}


@lru_cache(maxsize=10000)
def _similarity_ratio(name1: str, name2: str) -> float:
    """Calculate similarity ratio between two names."""
    original_ratio = SequenceMatcher(None, name1.lower(), name2.lower()).ratio()
    normalized_ratio = SequenceMatcher(
        None, _normalize_unicode(name1).lower(), _normalize_unicode(name2).lower()
    ).ratio()
    ordinal_ratio = SequenceMatcher(
        None, _normalize_ordinals(name1), _normalize_ordinals(name2)
    ).ratio()
    return max(original_ratio, normalized_ratio, ordinal_ratio)


_ORDINAL_WORDS = {
    "first": "1", "second": "2", "third": "3", "fourth": "4", "fifth": "5",
    "sixth": "6", "seventh": "7", "eighth": "8", "ninth": "9", "tenth": "10",
    "eleventh": "11", "twelfth": "12", "thirteenth": "13", "fourteenth": "14",
    "fifteenth": "15", "sixteenth": "16", "seventeenth": "17", "eighteenth": "18",
    "nineteenth": "19", "twentieth": "20",
}
_ORDINAL_SUFFIX = re.compile(r"(\d+)(?:st|nd|rd|th)\b", re.IGNORECASE)

_WORD_TO_NUM = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
    "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15,
    "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19,
    "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50,
    "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90,
    "hundred": 100,
}


def _words_to_number(words: list[str]) -> tuple[int, int]:
    """Convert a sequence of number words to an integer. Returns (value, words_consumed)."""
    total = 0
    current = 0
    consumed = 0
    for w in words:
        w_lower = w.lower()
        if w_lower == "and":
            consumed += 1
            continue
        val = _WORD_TO_NUM.get(w_lower)
        if val is None:
            # Check ordinal forms
            val = _ORDINAL_WORDS.get(w_lower)
            if val is not None:
                current += int(val)
                consumed += 1
                break  # ordinal ends the number
            break
        if val == 100:
            current = (current or 1) * 100
        else:
            current += val
        consumed += 1
    total += current
    return total, consumed


@lru_cache(maxsize=5000)
def _normalize_ordinals(name: str) -> str:
    """Normalize ordinal words and suffixes to digits.

    Handles: 'Fifth'→'5', '1st'→'1', 'eighty second'→'82',
    'one hundred and first'→'101'.
    """
    result = _ORDINAL_SUFFIX.sub(r"\1", name.lower())
    words = result.split()
    out: list[str] = []
    i = 0
    while i < len(words):
        if words[i] in _WORD_TO_NUM or words[i] in _ORDINAL_WORDS:
            val, consumed = _words_to_number(words[i:])
            if val > 0 and consumed > 0:
                out.append(str(val))
                i += consumed
                continue
        out.append(words[i])
        i += 1
    return " ".join(out)


@lru_cache(maxsize=5000)
def _extract_core_name(name: str) -> str:
    """Extract core name without common prefixes/suffixes, with ordinals normalized."""
    # Remove common military prefixes
    prefixes = ["u.s.", "us", "german", "british", "soviet", "the"]
    words = _normalize_ordinals(name).split()
    filtered = [w for w in words if w not in prefixes]
    return " ".join(filtered) if filtered else name.lower()


def _is_parent_child(group1: Dict, group2: Dict) -> bool:
    """Check if groups have parent-child relationship."""
    parent1 = group1.get("parent_organization", "")
    parent2 = group2.get("parent_organization", "")

    name1 = group1.get("group_name") or group1.get("name", "")
    name2 = group2.get("group_name") or group2.get("name", "")

    # Check if one is parent of the other
    if parent1 and parent1.lower() in name2.lower():
        return True
    if parent2 and parent2.lower() in name1.lower():
        return True

    return False


def _has_shared_context(group1: Dict, group2: Dict) -> bool:
    """Check if groups share country/alliance/type."""
    # Same country of origin
    country1 = (group1.get("country_of_origin") or "").lower()
    country2 = (group2.get("country_of_origin") or "").lower()
    if country1 and country2 and country1 == country2:
        return True

    # Same alliance membership
    alliance1 = set(a.lower() for a in group1.get("alliance_membership", []))
    alliance2 = set(a.lower() for a in group2.get("alliance_membership", []))
    if alliance1 and alliance2 and alliance1 & alliance2:
        return True

    return False


def _has_shared_events(group1: Dict, group2: Dict) -> bool:
    """Check if groups appear in same events."""
    events1 = {m["EventID"] for m in group1.get("event_mentions", [])}
    events2 = {m["EventID"] for m in group2.get("event_mentions", [])}

    # If they share 2+ events, likely related
    return len(events1 & events2) >= 2


def _get_group_name(data: Dict) -> str:
    """Get group name from data, trying group_name then name."""
    return data.get("group_name") or data.get("name", "")


def _different_unit_identifiers(name1: str, name2: str) -> bool:
    """Return True if names have different unit numbers, Roman numerals, or letters."""
    n1 = _normalize_ordinals(name1)
    n2 = _normalize_ordinals(name2)
    num1 = re.findall(r"\b(\d+)\b", n1)
    num2 = re.findall(r"\b(\d+)\b", n2)
    if num1 and num2 and num1 != num2:
        return True

    roman1 = re.findall(r"\b([IVXLC]+)\s+(?:Corps|Army|Division|Panzer)", name1)
    roman2 = re.findall(r"\b([IVXLC]+)\s+(?:Corps|Army|Division|Panzer)", name2)
    if roman1 and roman2 and roman1 != roman2:
        return True

    letter1 = re.findall(r"\b(?:group|army|corps)\s+([a-z])\b", name1.lower())
    letter2 = re.findall(r"\b(?:group|army|corps)\s+([a-z])\b", name2.lower())
    if letter1 and letter2 and letter1 != letter2:
        return True

    return False


def _score_group_pair(group1: Dict, group2: Dict) -> tuple[list[str], float]:
    """Score a pair of groups for duplicate likelihood. Returns (reasons, confidence)."""
    name1 = _get_group_name(group1)
    name2 = _get_group_name(group2)
    type1 = group1.get("group_type", "")
    type2 = group2.get("group_type", "")

    reasons: list[str] = []
    confidence = 0.0

    # Check 0: Different countries — skip unless very high similarity
    country1 = (group1.get("country_of_origin") or "").lower()
    country2 = (group2.get("country_of_origin") or "").lower()
    if country1 and country2 and country1 != country2:
        similarity = _similarity_ratio(name1, name2)
        if similarity < 0.90:
            return [], 0.0
        reasons.append(
            f"⚠️ Different countries ({country1} vs {country2}) but very high name similarity"
        )

    # Check 0.5: Different unit numbers/letters — skip
    if _different_unit_identifiers(name1, name2):
        return [], 0.0

    # Check 1: High name similarity
    similarity = _similarity_ratio(name1, name2)
    if similarity > 0.7:
        reasons.append(f"Name similarity: {similarity:.2f}")
        confidence += similarity * 0.5

    # Check 2: Core name match
    core1 = _extract_core_name(name1)
    core2 = _extract_core_name(name2)
    core_similarity = _similarity_ratio(core1, core2)
    if core_similarity > 0.8:
        reasons.append(f"Core name match: {core_similarity:.2f}")
        confidence += 0.6

    # Check 3: Same type
    if type1 == type2 and type1:
        reasons.append(f"Same type: {type1}")
        confidence += 0.3

    # Check 4: Parent-child — skip entirely
    if _is_parent_child(group1, group2):
        return [], 0.0

    # Check 5: Shared context
    if _has_shared_context(group1, group2):
        reasons.append("Shared context")
        confidence += 0.1

    # Check 6: Shared events
    if _has_shared_events(group1, group2):
        reasons.append("Shared events")
        confidence += 0.2

    # Check 7: Substring match (only if very similar)
    if name1.lower() in name2.lower() or name2.lower() in name1.lower():
        if similarity > 0.85 and len(name1) > 5:
            reasons.append("Name substring match")
            confidence += 0.3

    # Check 8: ASCII/Unicode variants
    if _normalize_unicode(name1).lower() == _normalize_unicode(name2).lower():
        reasons.append("ASCII/Unicode variant")
        confidence += 0.6

    return reasons, confidence


def _load_groups_data(groups_dir_path: Path) -> List[Dict[str, Any]]:
    """Load all group JSON files, skipping known non-groups."""
    excluded_files = {
        "index.json",
        "related_groups_report.json",
        "not_related.json",
        "not_groups.json",
        "excluded_merges.md",
        ".processed_events.json",
    }
    # Load not-a-group names
    ng_file = groups_dir_path / "not_groups.json"
    not_group_names: set[str] = set()
    if ng_file.exists():
        not_group_names = set(json.loads(ng_file.read_text(encoding="utf-8")).get("names", []))

    groups_data = []
    for group_file in groups_dir_path.glob("*.json"):
        if group_file.name in excluded_files:
            continue
        with open(group_file, "r", encoding="utf-8") as f:
            data = json.load(f)
            name = (data.get("group_name") or data.get("name", "")).lower()
            if name in not_group_names:
                continue
            data["_filename"] = group_file.name
            groups_data.append(data)
    return groups_data


def _load_excluded_pairs(groups_dir: Path) -> Set[tuple]:
    """Load pairwise exclusions from not_related.json."""
    exclusion_file = groups_dir / "not_related.json"
    pairs = set()
    if exclusion_file.exists():
        try:
            data = json.loads(exclusion_file.read_text(encoding="utf-8"))
            for exc in data.get("exclusions", []):
                pair = tuple(sorted([exc["group1"], exc["group2"]]))
                pairs.add(pair)
        except Exception as e:
            logger.warning("Failed to load not_related.json: %s", e)
    return pairs


def _find_related_clusters(
    groups_data: List[Dict[str, Any]],
    excluded_clusters: List[Set[str]],
    excluded_pairs: Set[tuple],
    use_llm_verification: bool,
    grok_client,
) -> List[Dict[str, Any]]:
    """Find related group clusters from scored pairs."""
    related: list[dict[str, Any]] = []
    processed: Set[str] = set()

    for i, group1 in enumerate(groups_data):
        if group1["_filename"] in processed:
            continue
        if not _get_group_name(group1):
            continue

        cluster: list[dict[str, Any]] = []
        cluster_reasons: list[str] = []
        cluster_confidence = 0.0

        for group2 in groups_data[i + 1 :]:
            if group2["_filename"] in processed or not _get_group_name(group2):
                continue

            pair_key = tuple(sorted([group1["_filename"], group2["_filename"]]))
            if pair_key in excluded_pairs:
                continue

            reasons, confidence = _score_group_pair(group1, group2)

            if confidence <= 0.8 or not reasons:
                continue

            # LLM verification
            if use_llm_verification and grok_client:
                llm_result = _verify_duplicate_with_llm(group1, group2, grok_client)
                if llm_result.get("relationship") != "TRUE_DUPLICATE":
                    logger.info(
                        "LLM rejected match: %s vs %s (relationship=%s, reasoning=%s)",
                        _get_group_name(group1),
                        _get_group_name(group2),
                        llm_result.get("relationship"),
                        llm_result.get("reasoning"),
                    )
                    continue
                llm_conf = llm_result.get("confidence", 0.0)
                reasons.append(
                    f"LLM verified: {llm_result.get('reasoning', '')} "
                    f"(confidence={llm_conf:.2f})"
                )
                confidence = max(confidence, llm_conf)

            if not cluster:
                cluster.append(
                    {
                        "filename": group1["_filename"],
                        "name": _get_group_name(group1),
                        "type": group1.get("group_type", ""),
                        "GroupID": group1["GroupID"],
                    }
                )
            cluster.append(
                {
                    "filename": group2["_filename"],
                    "name": _get_group_name(group2),
                    "type": group2.get("group_type", ""),
                    "GroupID": group2["GroupID"],
                }
            )
            cluster_confidence = max(cluster_confidence, confidence)
            cluster_reasons.extend(reasons)
            processed.add(group2["_filename"])

        if cluster:
            cluster_group_ids = {g["GroupID"] for g in cluster}
            if _is_cluster_excluded(cluster_group_ids, excluded_clusters):
                logger.info(
                    "Skipping excluded cluster: %s",
                    ", ".join(g["name"] for g in cluster),
                )
                continue
            related.append(
                {
                    "confidence": cluster_confidence,
                    "reasons": list(set(cluster_reasons)),
                    "groups": cluster,
                }
            )
            processed.add(group1["_filename"])

    return related


def find_related_groups(
    groups_dir_path: Path, use_llm_verification: bool = True
) -> List[Dict[str, Any]]:
    """
    Find related people groups based on various heuristics.

    Args:
        groups_dir_path: Path to people_groups directory
        use_llm_verification: If True, use LLM to verify relationships (default: True)

    Returns list of related group clusters.
    """
    excluded_clusters = _load_excluded_clusters(groups_dir_path)
    if excluded_clusters:
        logger.info("Loaded %d excluded cluster(s)", len(excluded_clusters))

    excluded_pairs = _load_excluded_pairs(groups_dir_path)
    if excluded_pairs:
        logger.info("Loaded %d excluded pair(s)", len(excluded_pairs))

    grok_client = None
    if use_llm_verification:
        try:
            grok_client = GrokClient()
            logger.info("LLM verification enabled")
        except Exception as e:
            logger.warning(
                "Failed to initialize Grok client: %s. Proceeding without LLM.", e
            )
            use_llm_verification = False

    groups_data = _load_groups_data(groups_dir_path)
    logger.info("Analyzing %d groups for relationships...", len(groups_data))

    return _find_related_clusters(
        groups_data, excluded_clusters, excluded_pairs, use_llm_verification, grok_client
    )


def generate_related_groups_report(
    groups_dir_path: Path, output_file_path: Path
) -> None:
    """Generate a report of related groups."""
    related = find_related_groups(groups_dir_path)

    # Sort by confidence
    related.sort(key=lambda x: x["confidence"], reverse=True)

    report = {
        "total_groups": len(list(groups_dir_path.glob("*.json"))) - 1,  # Exclude index
        "related_clusters": len(related),
        "relationships": related,
    }

    with open(output_file_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)

    logger.info("Found %d related group clusters", len(related))
    logger.info("Report saved to: %s", output_file_path)

    # Print summary
    print("\n" + "=" * 80)
    print("RELATED PEOPLE GROUPS REPORT")
    print("=" * 80)
    print(f"Total groups: {report['total_groups']}")
    print(f"Related clusters found: {len(related)}")
    print()

    for i, cluster in enumerate(related[:10], 1):
        print(f"{i}. Confidence: {cluster['confidence']:.2f}")
        print(f"   Reasons: {', '.join(cluster['reasons'])}")
        for group in cluster["groups"]:
            print(f"   - {group['name']} ({group['type']}) [{group['filename']}]")
        print()

    if len(related) > 10:
        print(f"... and {len(related) - 10} more clusters")
        print(f"See full report: {output_file_path}")


def main():
    """Main entry point."""
    groups_dir = Path("output/people_groups")
    output_file = groups_dir / "related_groups_report.json"

    if not groups_dir.exists():
        logger.error("People groups directory not found: %s", groups_dir)
        logger.info("Run phase2_extract.py first to extract people groups")
        return 1

    # Check if directory has any JSON files
    group_files = list(groups_dir.glob("*.json"))
    if not group_files:
        logger.error("No people group files found in: %s", groups_dir)
        logger.info("Run phase2_extract.py first to extract people groups")
        return 1

    logger.info("Found %d people group file(s)", len(group_files))
    generate_related_groups_report(groups_dir, output_file)
    return 0


if __name__ == "__main__":
    sys.exit(main())

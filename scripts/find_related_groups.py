#!/usr/bin/env python3
"""
Find related people groups based on name similarity, hierarchy, and shared context.
Uses LLM to verify if groups are true duplicates vs hierarchically related.
"""

import json
import logging
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
- Name: {group1.get('group_name')}
- Type: {group1.get('group_type')}
- Country: {group1.get('country_of_origin')}
- Parent: {group1.get('parent_organization', 'None')}
- Description: {group1.get('description', 'None')[:200]}

Organization 2:
- Name: {group2.get('group_name')}
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
    return max(original_ratio, normalized_ratio)


@lru_cache(maxsize=5000)
def _extract_core_name(name: str) -> str:
    """Extract core name without common prefixes/suffixes."""
    # Remove common military prefixes
    prefixes = ["u.s.", "us", "german", "british", "soviet", "the"]
    words = name.lower().split()
    filtered = [w for w in words if w not in prefixes]
    return " ".join(filtered) if filtered else name.lower()


def _is_parent_child(group1: Dict, group2: Dict) -> bool:
    """Check if groups have parent-child relationship."""
    parent1 = group1.get("parent_organization", "")
    parent2 = group2.get("parent_organization", "")

    name1 = group1.get("group_name", "")
    name2 = group2.get("group_name", "")

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
    # Load excluded clusters
    excluded_clusters = _load_excluded_clusters(groups_dir_path)
    if excluded_clusters:
        logger.info("Loaded %d excluded cluster(s)", len(excluded_clusters))

    # Initialize Grok client if verification enabled
    grok_client = None
    if use_llm_verification:
        try:
            grok_client = GrokClient()
            logger.info("LLM verification enabled")
        except Exception as e:
            logger.warning(
                f"Failed to initialize Grok client: {e}. Proceeding without LLM verification."
            )
            use_llm_verification = False

    # Load all group files
    group_files = list(groups_dir_path.glob("*.json"))
    # Exclude system files and hidden files
    excluded_files = {
        "index.json",
        "related_groups_report.json",
        "not_related.json",
        ".processed_events.json",
    }
    group_files = [f for f in group_files if f.name not in excluded_files]

    groups_data = []
    for group_file in group_files:
        with open(group_file, "r", encoding="utf-8") as f:
            data = json.load(f)
            data["_filename"] = group_file.name
            groups_data.append(data)

    logger.info("Analyzing %d groups for relationships...", len(groups_data))

    related = []
    processed: Set[str] = set()

    for i, group1 in enumerate(groups_data):
        if group1["_filename"] in processed:
            continue

        # Skip if group doesn't have a name
        if "group_name" not in group1:
            logger.warning(
                f"Skipping {group1['_filename']}: missing 'group_name' field"
            )
            continue

        name1 = group1["group_name"]
        core1 = _extract_core_name(name1)
        type1 = group1.get("group_type", "")

        cluster: List[Dict[str, Any]] = []
        cluster_reasons: List[str] = []
        cluster_confidence = 0.0

        for group2 in groups_data[i + 1 :]:
            if group2["_filename"] in processed:
                continue

            # Skip if group doesn't have a name
            if "group_name" not in group2:
                logger.warning(
                    f"Skipping {group2['_filename']}: missing 'group_name' field"
                )
                continue

            name2 = group2["group_name"]
            core2 = _extract_core_name(name2)
            type2 = group2.get("group_type", "")

            reasons = []
            confidence = 0.0

            # Check 0: DIFFERENT countries - skip unless very high similarity
            country1 = (group1.get("country_of_origin") or "").lower()
            country2 = (group2.get("country_of_origin") or "").lower()
            if country1 and country2 and country1 != country2:
                # Different countries - only match if name similarity is very high (90%+)
                similarity = _similarity_ratio(name1, name2)
                if similarity < 0.90:
                    continue  # Skip this pair
                reasons.append(
                    f"⚠️ Different countries ({country1} vs {country2}) but very high name similarity"
                )

            # Check 0.5: Different unit numbers/letters - skip
            # Examples: "1st Division" vs "2d Division", "Army Group B" vs "Army Group G"
            import re

            # Extract numbers and letters that differentiate units
            # Match: 1st, 2nd, 3rd, 4th OR 1d, 2d, 3d (common military abbreviations)
            # Also match Roman numerals: I, II, III, IV, V, VI, VII, VIII, IX, X, etc.
            num1 = re.findall(r"\b(\d+)(?:st|nd|rd|th|d)?\b", name1.lower())
            num2 = re.findall(r"\b(\d+)(?:st|nd|rd|th|d)?\b", name2.lower())

            # Extract Roman numerals (I-LXXXVIII covers most WWII units)
            roman1 = re.findall(r"\b([IVXLC]+)\s+(?:Corps|Army|Division|Panzer)", name1)
            roman2 = re.findall(r"\b([IVXLC]+)\s+(?:Corps|Army|Division|Panzer)", name2)

            letter1 = re.findall(r"\b(?:group|army|corps)\s+([a-z])\b", name1.lower())
            letter2 = re.findall(r"\b(?:group|army|corps)\s+([a-z])\b", name2.lower())

            # If they have different unit numbers or letters, they're different units
            if num1 and num2 and num1 != num2:
                # Different unit numbers (e.g., "1st" vs "2d" vs "3d")
                continue
            if roman1 and roman2 and roman1 != roman2:
                # Different Roman numeral units (e.g., "V Corps" vs "VII Corps")
                continue
            if letter1 and letter2 and letter1 != letter2:
                # Different letter designations (e.g., "Group B" vs "Group G")
                continue

            # Check 1: High name similarity (70%+)
            similarity = _similarity_ratio(name1, name2)
            if similarity > 0.7:
                reasons.append(f"Name similarity: {similarity:.2f}")
                confidence += similarity * 0.5

            # Check 2: Core name match (without prefixes)
            core_similarity = _similarity_ratio(core1, core2)
            if core_similarity > 0.8:
                reasons.append(f"Core name match: {core_similarity:.2f}")
                confidence += 0.6

            # Check 3: Same type
            if type1 == type2 and type1:
                reasons.append(f"Same type: {type1}")
                confidence += 0.3

            # Check 4: Parent-child relationship (SKIP - these should NOT be merged)
            # Parent-child means hierarchically related but distinct entities
            if _is_parent_child(group1, group2):
                # Don't add confidence - these are related but should NOT merge
                continue  # Skip this pair entirely

            # Check 5: Shared context (country/alliance) - REDUCE weight
            # Shared context alone doesn't mean they should merge
            if _has_shared_context(group1, group2):
                reasons.append("Shared context")
                confidence += 0.1  # Reduced from 0.4

            # Check 6: Appear in same events - REDUCE weight
            # Many groups appear in same events but are distinct
            if _has_shared_events(group1, group2):
                reasons.append("Shared events")
                confidence += 0.2  # Reduced from 0.5

            # Check 7: One name substring of other (ONLY if very similar)
            # Avoid matching hierarchies like "United States" with "First United States Army"
            if name1.lower() in name2.lower() or name2.lower() in name1.lower():
                # Only count if the names are very similar (not just substring)
                if similarity > 0.85 and len(name1) > 5:
                    reasons.append("Name substring match")
                    confidence += 0.3  # Reduced from 0.5

            # Check 8: ASCII/Unicode variants
            if _normalize_unicode(name1).lower() == _normalize_unicode(name2).lower():
                reasons.append("ASCII/Unicode variant")
                confidence += 0.6

            # If confidence high enough, verify with LLM
            # INCREASED threshold from 0.5 to 0.8 to be more conservative
            if confidence > 0.8 and reasons:
                # LLM verification (if enabled)
                if use_llm_verification and grok_client:
                    llm_result = _verify_duplicate_with_llm(group1, group2, grok_client)
                    relationship = llm_result.get("relationship", "UNKNOWN")
                    llm_confidence = llm_result.get("confidence", 0.0)
                    llm_reasoning = llm_result.get("reasoning", "")

                    # Only add to cluster if LLM confirms TRUE_DUPLICATE
                    if relationship != "TRUE_DUPLICATE":
                        logger.info(
                            f"LLM rejected match: {name1} vs {name2} "
                            f"(relationship={relationship}, reasoning={llm_reasoning})"
                        )
                        continue

                    # Add LLM reasoning to cluster
                    reasons.append(
                        f"LLM verified: {llm_reasoning} (confidence={llm_confidence:.2f})"
                    )
                    confidence = max(confidence, llm_confidence)

                # Add to cluster
                if not cluster:
                    cluster.append(
                        {
                            "filename": group1["_filename"],
                            "name": name1,
                            "type": type1,
                            "GroupID": group1["GroupID"],
                        }
                    )

                cluster.append(
                    {
                        "filename": group2["_filename"],
                        "name": name2,
                        "type": type2,
                        "GroupID": group2["GroupID"],
                    }
                )

                cluster_confidence = max(cluster_confidence, confidence)
                cluster_reasons.extend(reasons)

                processed.add(group2["_filename"])

        if cluster:
            # Check if this cluster is excluded
            cluster_group_ids = {g["GroupID"] for g in cluster}
            if _is_cluster_excluded(cluster_group_ids, excluded_clusters):
                logger.info(
                    "Skipping excluded cluster: %s",
                    ", ".join(g["name"] for g in cluster),
                )
                # Don't add to processed - allow individual groups to match with others
                continue

            cluster_reasons = list(set(cluster_reasons))
            related.append(
                {
                    "confidence": cluster_confidence,
                    "reasons": cluster_reasons,
                    "groups": cluster,
                }
            )
            processed.add(group1["_filename"])

    return related


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

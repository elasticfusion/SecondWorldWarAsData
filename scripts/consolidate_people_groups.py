#!/usr/bin/env python3
"""
Apply people group aliases and consolidate based on YAML configuration.
"""

import json
import logging
import sys
from pathlib import Path
from typing import Dict, List

import yaml

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def load_alias_config(config_path: Path) -> Dict:
    """Load the alias configuration from YAML."""
    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def build_alias_map(config: Dict) -> Dict[str, str]:
    """
    Build a mapping from alias -> canonical name.

    Returns dict like: {"Americans": "United States", "British": "United Kingdom"}
    """
    alias_map = {}

    for entry in config.get("aliases", []):
        canonical = entry["canonical"]
        for alias in entry.get("aliases", []):
            alias_map[alias.lower()] = canonical

    return alias_map


def build_merge_rules(config: Dict) -> Dict[str, List[str]]:
    """
    Build merge rules: canonical -> [names to merge].

    Returns dict like: {"United States": ["Americans", "American forces"]}
    """
    merge_rules = {}

    for rule in config.get("merge_rules", []):
        canonical = rule["canonical"]
        merge_if_found = rule.get("merge_if_found", [])
        merge_rules[canonical] = [name.lower() for name in merge_if_found]

    return merge_rules


def normalize_group_name(name: str, alias_map: Dict[str, str]) -> str:
    """Normalize a group name using the alias map."""
    name_lower = name.lower()

    # Direct match
    if name_lower in alias_map:
        return alias_map[name_lower]

    # Check if name contains any alias
    for alias, canonical in alias_map.items():
        if alias in name_lower:
            return canonical

    return name


def consolidate_groups(groups_dir: Path, config: Dict) -> None:
    """
    Consolidate people groups based on alias configuration.

    - Renames groups to canonical names
    - Merges duplicate groups
    - Updates index
    """
    alias_map = build_alias_map(config)
    merge_rules = build_merge_rules(config)

    logger.info("Loaded %d aliases", len(alias_map))
    logger.info("Loaded %d merge rules", len(merge_rules))

    # Load all group files
    group_files = [
        f
        for f in groups_dir.glob("*.json")
        if f.name not in ["index.json", "related_groups_report.json"]
    ]

    logger.info("Processing %d group files...", len(group_files))

    # Track groups to merge
    canonical_groups: Dict[str, List[Path]] = {}

    for group_file in group_files:
        with open(group_file, "r", encoding="utf-8") as f:
            group_data = json.load(f)

        group_name = group_data.get("group_name", "")
        canonical_name = normalize_group_name(group_name, alias_map)

        if canonical_name not in canonical_groups:
            canonical_groups[canonical_name] = []

        canonical_groups[canonical_name].append(group_file)

    # Merge groups with same canonical name
    merged_count = 0
    for canonical_name, files in canonical_groups.items():
        if len(files) > 1:
            logger.info("Merging %d groups into: %s", len(files), canonical_name)

            # Load all data
            merged_data = None
            for group_file in files:
                with open(group_file, "r", encoding="utf-8") as f:
                    data = json.load(f)

                if merged_data is None:
                    merged_data = data
                    merged_data["group_name"] = canonical_name
                else:
                    # Merge event mentions
                    existing_mentions = merged_data.get("event_mentions", [])
                    new_mentions = data.get("event_mentions", [])

                    mention_ids = {m["MentionID"] for m in existing_mentions}
                    for mention in new_mentions:
                        if mention["MentionID"] not in mention_ids:
                            existing_mentions.append(mention)

                    merged_data["event_mentions"] = existing_mentions

                    # Merge members
                    if "members" in data:
                        existing_members = merged_data.get("members", [])
                        new_members = data.get("members", [])

                        person_ids = {
                            m.get("PersonID")
                            for m in existing_members
                            if m.get("PersonID")
                        }
                        for member in new_members:
                            if (
                                member.get("PersonID")
                                and member["PersonID"] not in person_ids
                            ):
                                existing_members.append(member)

                        merged_data["members"] = existing_members

            # Save merged data to first file
            primary_file = files[0]
            with open(primary_file, "w", encoding="utf-8") as f:
                json.dump(merged_data, f, indent=2, ensure_ascii=False)

            # Delete other files
            for group_file in files[1:]:
                group_file.unlink()
                logger.info("  Deleted: %s", group_file.name)

            merged_count += 1

    logger.info("Merged %d group(s)", merged_count)

    # Rebuild index
    logger.info("Rebuilding index...")
    index = {}
    for group_file in groups_dir.glob("*.json"):
        if group_file.name in ["index.json", "related_groups_report.json"]:
            continue

        with open(group_file, "r", encoding="utf-8") as f:
            data = json.load(f)

        group_name = data.get("group_name", "")
        index[group_name.lower()] = group_file.name

    index_file = groups_dir / "index.json"
    with open(index_file, "w", encoding="utf-8") as f:
        json.dump(index, f, indent=2, ensure_ascii=False)

    logger.info("Index updated with %d entries", len(index))


def main():
    """Main entry point."""
    config_path = Path("config/people_group_aliases.yaml")
    groups_dir = Path("output/people_groups")

    if not config_path.exists():
        logger.error("Config file not found: %s", config_path)
        return 1

    if not groups_dir.exists():
        logger.error("People groups directory not found: %s", groups_dir)
        return 1

    config = load_alias_config(config_path)
    consolidate_groups(groups_dir, config)

    logger.info("✓ Consolidation complete")
    return 0


if __name__ == "__main__":
    sys.exit(main())

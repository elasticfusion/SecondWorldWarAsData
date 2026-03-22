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


def _get_group_name(data):
    """Get group name from data, trying group_name then name."""
    return data.get("group_name") or data.get("name", "")


def _group_by_canonical(groups_dir, alias_map):
    """Load group files and group by canonical name."""
    skip = {"index.json", "related_groups_report.json"}
    canonical_groups: Dict[str, List[Path]] = {}
    for group_file in groups_dir.glob("*.json"):
        if group_file.name in skip:
            continue
        with open(group_file, "r", encoding="utf-8") as f:
            group_data = json.load(f)
        canonical = normalize_group_name(_get_group_name(group_data), alias_map)
        canonical_groups.setdefault(canonical, []).append(group_file)
    return canonical_groups


def _merge_into(merged_data, data):
    """Merge event mentions and members from data into merged_data."""
    # Merge event mentions
    existing = merged_data.get("event_mentions", [])
    ids = {m["MentionID"] for m in existing}
    for mention in data.get("event_mentions", []):
        if mention["MentionID"] not in ids:
            existing.append(mention)
    merged_data["event_mentions"] = existing

    # Merge members
    if "members" in data:
        members = merged_data.get("members", [])
        pids = {m.get("PersonID") for m in members if m.get("PersonID")}
        for member in data.get("members", []):
            if member.get("PersonID") and member["PersonID"] not in pids:
                members.append(member)
        merged_data["members"] = members


def _merge_canonical_groups(canonical_groups):
    """Merge file groups with >1 file. Returns merge count."""
    merged_count = 0
    for canonical_name, files in canonical_groups.items():
        if len(files) <= 1:
            continue
        logger.info("Merging %d groups into: %s", len(files), canonical_name)

        with open(files[0], "r", encoding="utf-8") as f:
            merged_data = json.load(f)
        merged_data["group_name"] = canonical_name

        for group_file in files[1:]:
            with open(group_file, "r", encoding="utf-8") as f:
                _merge_into(merged_data, json.load(f))
            group_file.unlink()
            logger.info("  Deleted: %s", group_file.name)

        with open(files[0], "w", encoding="utf-8") as f:
            json.dump(merged_data, f, indent=2, ensure_ascii=False)
        merged_count += 1

    return merged_count


def _rebuild_group_index(groups_dir):
    """Rebuild the groups index.json."""
    skip = {"index.json", "related_groups_report.json"}
    index = {}
    for group_file in groups_dir.glob("*.json"):
        if group_file.name in skip:
            continue
        with open(group_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        index[_get_group_name(data).lower()] = group_file.name

    with open(groups_dir / "index.json", "w", encoding="utf-8") as f:
        json.dump(index, f, indent=2, ensure_ascii=False)
    logger.info("Index updated with %d entries", len(index))


def consolidate_groups(groups_dir: Path, config: Dict) -> None:
    """
    Consolidate people groups based on alias configuration.

    - Renames groups to canonical names
    - Merges duplicate groups
    - Updates index
    """
    alias_map = build_alias_map(config)
    merge_rules = build_merge_rules(config)
    logger.info("Loaded %d aliases, %d merge rules", len(alias_map), len(merge_rules))

    canonical_groups = _group_by_canonical(groups_dir, alias_map)
    logger.info("Processing %d canonical groups...", len(canonical_groups))

    merged_count = _merge_canonical_groups(canonical_groups)
    logger.info("Merged %d group(s)", merged_count)

    logger.info("Rebuilding index...")
    _rebuild_group_index(groups_dir)


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

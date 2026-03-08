#!/usr/bin/env python3
"""
Suggest new people group aliases based on extracted groups.

Compares extracted groups against existing aliases and suggests additions.
"""

import json
import logging
import sys
from pathlib import Path
from typing import Dict, List, Set

import yaml

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.grok_client import GrokClient

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def load_existing_aliases(config_path: Path) -> Dict[str, Set[str]]:
    """
    Load existing aliases from YAML.

    Returns: {canonical_name: {set of aliases}}
    """
    with open(config_path, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)

    alias_map = {}
    for entry in config.get("aliases", []):
        canonical = entry["canonical"]
        aliases = set(entry.get("aliases", []))
        aliases.add(canonical.lower())  # Include canonical name itself
        alias_map[canonical] = {a.lower() for a in aliases}

    return alias_map


def get_all_extracted_groups(groups_dir: Path) -> List[str]:
    """Get all unique group names from extracted files."""
    group_names = set()

    for group_file in groups_dir.glob("*.json"):
        if group_file.name in ["index.json", "related_groups_report.json"]:
            continue

        with open(group_file, "r", encoding="utf-8") as f:
            data = json.load(f)
            group_names.add(data.get("group_name", ""))

    return sorted(group_names)


def find_unaliased_groups(
    extracted_groups: List[str], existing_aliases: Dict[str, Set[str]]
) -> List[str]:
    """Find groups that don't match any existing alias."""
    unaliased = []

    # Flatten all known aliases
    all_known = set()
    for aliases in existing_aliases.values():
        all_known.update(aliases)

    for group in extracted_groups:
        if group.lower() not in all_known:
            unaliased.append(group)

    return unaliased


def suggest_aliases_with_llm(
    unaliased_groups: List[str], existing_aliases: Dict[str, Set[str]], grok: GrokClient
) -> Dict:
    """
    Use LLM to suggest which canonical group each unaliased group belongs to.

    Returns suggested additions in YAML format.
    """
    prompt = f"""You are a WWII historian. Review these group names and suggest which canonical group they belong to.

EXISTING CANONICAL GROUPS:
{json.dumps(list(existing_aliases.keys()), indent=2)}

UNALIASED GROUPS (need classification):
{json.dumps(unaliased_groups, indent=2)}

For each unaliased group, determine:
1. Which canonical group it belongs to (or if it's a NEW canonical group)
2. Why (brief explanation)

Return ONLY valid JSON:
{{
  "suggestions": [
    {{
      "group_name": "Americans",
      "canonical": "United States",
      "reason": "Common demonym for US citizens/forces",
      "confidence": "high"
    }},
    {{
      "group_name": "XIX Corps",
      "canonical": "NEW",
      "reason": "Specific military unit, not an alias",
      "confidence": "high"
    }}
  ]
}}

Confidence levels: high, medium, low"""

    response = grok.chat_completion(
        prompt, temperature=0.2, cache_type="alias_suggestions"
    )

    try:
        return json.loads(response)
    except json.JSONDecodeError as e:
        logger.error("Failed to parse LLM response: %s", e)
        return {"suggestions": []}


def format_yaml_additions(suggestions: List[Dict]) -> str:
    """Format suggestions as YAML additions."""
    yaml_lines = []

    # Group by canonical
    by_canonical: Dict[str, List[Dict]] = {}
    new_canonicals = []

    for suggestion in suggestions:
        canonical = suggestion["canonical"]
        if canonical == "NEW":
            new_canonicals.append(suggestion)
        else:
            if canonical not in by_canonical:
                by_canonical[canonical] = []
            by_canonical[canonical].append(suggestion)

    # Format additions to existing canonicals
    if by_canonical:
        yaml_lines.append("# Suggested additions to existing canonicals:")
        yaml_lines.append("")
        for canonical, items in sorted(by_canonical.items()):
            yaml_lines.append(f"# Add to '{canonical}':")
            for item in items:
                yaml_lines.append(f"#   - \"{item['group_name']}\"  # {item['reason']}")
            yaml_lines.append("")

    # Format new canonicals
    if new_canonicals:
        yaml_lines.append("# Suggested NEW canonical groups:")
        yaml_lines.append("")
        for item in new_canonicals:
            yaml_lines.append(f"# - canonical: \"{item['group_name']}\"")
            yaml_lines.append("#   aliases: []")
            yaml_lines.append(f"#   note: \"{item['reason']}\"")
            yaml_lines.append("")

    return "\n".join(yaml_lines)


def interactive_review(suggestions: List[Dict], config_path: Path) -> None:
    """Interactively review and apply suggestions."""
    print("\n" + "=" * 80)
    print("ALIAS SUGGESTIONS REVIEW")
    print("=" * 80)

    approved = []

    for i, suggestion in enumerate(suggestions, 1):
        print(f"\n{i}/{len(suggestions)}")
        print(f"Group: {suggestion['group_name']}")
        print(f"Suggested canonical: {suggestion['canonical']}")
        print(f"Reason: {suggestion['reason']}")
        print(f"Confidence: {suggestion['confidence']}")

        response = input("\nApprove? (y/n/skip/edit): ").lower()

        if response == "y":
            approved.append(suggestion)
            print("✓ Approved")
        elif response == "n":
            print("✗ Rejected")
        elif response == "edit":
            # Let user specify alternative canonical
            print("\nAvailable canonical groups:")
            with open(config_path, "r", encoding="utf-8") as f:
                config = yaml.safe_load(f)
                canonicals = [entry["canonical"] for entry in config.get("aliases", [])]
                for idx, canonical in enumerate(canonicals, 1):
                    print(f"  {idx}. {canonical}")

            print(f"  {len(canonicals) + 1}. NEW (create new canonical)")

            choice = input("\nEnter number or type canonical name: ").strip()

            if choice.isdigit():
                choice_idx = int(choice) - 1
                if 0 <= choice_idx < len(canonicals):
                    suggestion["canonical"] = canonicals[choice_idx]
                    approved.append(suggestion)
                    print(f"✓ Changed to: {suggestion['canonical']}")
                elif choice_idx == len(canonicals):
                    suggestion["canonical"] = "NEW"
                    approved.append(suggestion)
                    print("✓ Will create as new canonical")
                else:
                    print("✗ Invalid choice, skipping")
            elif choice:
                # User typed a canonical name
                suggestion["canonical"] = choice
                approved.append(suggestion)
                print(f"✓ Changed to: {choice}")
            else:
                print("⊘ Skipped")
        else:
            print("⊘ Skipped")

    if not approved:
        print("\nNo suggestions approved.")
        return

    # Apply approved suggestions
    print(f"\n{len(approved)} suggestion(s) approved. Updating YAML...")

    with open(config_path, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)

    # Add to existing canonicals
    for suggestion in approved:
        if suggestion["canonical"] == "NEW":
            # Add new canonical entry
            config["aliases"].append(
                {
                    "canonical": suggestion["group_name"],
                    "aliases": [],
                    "note": suggestion["reason"],
                }
            )
        else:
            # Find canonical and add alias
            for entry in config["aliases"]:
                if entry["canonical"] == suggestion["canonical"]:
                    if "aliases" not in entry:
                        entry["aliases"] = []
                    entry["aliases"].append(suggestion["group_name"])
                    break

    # Save updated config
    with open(config_path, "w", encoding="utf-8") as f:
        yaml.dump(
            config, f, default_flow_style=False, allow_unicode=True, sort_keys=False
        )

    print(f"✓ Updated {config_path}")


def main():
    """Main entry point."""
    config_path = Path("config/people_group_aliases.yaml")
    groups_dir = Path("output/people_groups")
    grok = GrokClient(Path("cache/api"))

    if not config_path.exists():
        logger.error("Config file not found: %s", config_path)
        return 1

    if not groups_dir.exists():
        logger.error("People groups directory not found: %s", groups_dir)
        return 1

    # Load existing aliases
    logger.info("Loading existing aliases...")
    existing_aliases = load_existing_aliases(config_path)
    logger.info("Found %d canonical groups", len(existing_aliases))

    # Get all extracted groups
    logger.info("Scanning extracted groups...")
    extracted_groups = get_all_extracted_groups(groups_dir)
    logger.info("Found %d extracted groups", len(extracted_groups))

    # Find unaliased groups
    unaliased = find_unaliased_groups(extracted_groups, existing_aliases)
    logger.info("Found %d unaliased groups", len(unaliased))

    if not unaliased:
        print("\n✓ All extracted groups are already aliased!")
        return 0

    print(f"\nUnaliased groups: {', '.join(unaliased)}")

    # Get LLM suggestions
    logger.info("Getting LLM suggestions...")
    result = suggest_aliases_with_llm(unaliased, existing_aliases, grok)
    suggestions = result.get("suggestions", [])

    if not suggestions:
        logger.error("No suggestions received from LLM")
        return 1

    # Interactive review
    interactive_review(suggestions, config_path)

    return 0


if __name__ == "__main__":
    import sys

    sys.exit(main())

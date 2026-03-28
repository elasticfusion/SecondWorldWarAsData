#!/usr/bin/env python3
"""
Interactive merge tool for related people groups.
Reads related_groups_report.json and prompts user to merge each cluster.
"""

import json
import logging
import sys
from pathlib import Path
from typing import Dict, List, Any

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)


def load_group_file(groups_dir: Path, filename: str) -> Dict[str, Any]:
    """Load a group JSON file."""
    with open(groups_dir / filename, "r", encoding="utf-8") as f:
        return json.load(f)


def save_group_file(groups_dir: Path, filename: str, data: Dict[str, Any]) -> None:
    """Save a group JSON file."""
    with open(groups_dir / filename, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def _merge_mentions(primary: Dict, others: List[Dict]) -> None:
    """Merge event_mentions from others into primary, deduplicating by MentionID."""
    existing = {
        m.get("MentionID") or m.get("mention_id")
        for m in primary.get("event_mentions", [])
    }
    for other in others:
        for mention in other.get("event_mentions", []):
            mid = mention.get("MentionID") or mention.get("mention_id")
            if mid and mid not in existing:
                primary.setdefault("event_mentions", []).append(mention)
                existing.add(mid)


def _merge_names(primary: Dict, others: List[Dict]) -> None:
    """Merge alternate_names and group names from others into primary."""
    existing = set(primary.get("alternate_names", []))
    for other in others:
        for name in other.get("alternate_names", []):
            if name not in existing:
                primary.setdefault("alternate_names", []).append(name)
                existing.add(name)
        other_name = other.get("group_name") or other.get("name", "")
        if other_name and other_name not in existing:
            primary.setdefault("alternate_names", []).append(other_name)


def merge_groups(primary: Dict, others: List[Dict]) -> Dict:
    """Merge other groups into primary group."""
    _merge_mentions(primary, others)
    _merge_names(primary, others)
    return primary


def get_user_action() -> str:
    """Get user action. Returns: 'merge', 'skip', 'exclude', 'notgroup', 'stop'."""
    response = input("\nMerge this cluster? (y/n/skip/exclude/notgroup): ").lower()
    if response == "n":
        return "stop"
    if response == "skip":
        return "skip"
    if response in ["exclude", "e"]:
        return "exclude"
    if response in ["notgroup", "ng"]:
        return "notgroup"
    return "merge"


def get_exclusions_from_user(groups: List[Dict]) -> List[int]:
    """Prompt user to select which groups to exclude.

    Returns list of indices (0-based) to exclude.
    """
    while True:
        response = input(
            "\nEnter group numbers to exclude (comma-separated, or 'all'): "
        ).strip()

        if response.lower() == "all":
            return list(range(len(groups)))

        try:
            indices = [int(x.strip()) - 1 for x in response.split(",")]
            if all(0 <= i < len(groups) for i in indices):
                return indices
            else:
                print(f"❌ Invalid numbers. Enter numbers between 1 and {len(groups)}")
        except ValueError:
            print("❌ Invalid input. Enter numbers separated by commas (e.g., 1,3,5)")


def get_primary_index(groups: List[Dict]) -> int:
    """Prompt user to select primary group."""
    while True:
        choice = input(f"\nSelect primary group (1-{len(groups)}, default=1): ").strip()
        if not choice:
            return 0
        if not choice.isdigit():
            print("❌ Invalid input")
            continue
        idx = int(choice) - 1
        if 0 <= idx < len(groups):
            return idx
        print(f"❌ Enter number between 1 and {len(groups)}")


def add_to_exclusion_list(groups_dir: Path, groups: List[Dict]) -> None:
    """Add group pairs to exclusion list (both JSON and Markdown)."""
    from datetime import datetime

    # Update JSON exclusion file (for backward compatibility)
    exclusion_file = groups_dir / "not_related.json"
    exclusions: Dict = {"comment": "Confirmed non-related groups", "exclusions": []}

    if exclusion_file.exists():
        with open(exclusion_file, "r", encoding="utf-8") as f:
            exclusions = json.load(f)

    # Add all pairs
    for i, group1 in enumerate(groups):
        for group2 in groups[i + 1 :]:
            exclusions["exclusions"].append(
                {"group1": group1["filename"], "group2": group2["filename"]}
            )

    with open(exclusion_file, "w", encoding="utf-8") as f:
        json.dump(exclusions, f, indent=2, ensure_ascii=False)

    # Also add to Markdown exclusion file (human-readable, used by find_related_groups.py)
    md_file = groups_dir / "excluded_merges.md"

    # Create file if it doesn't exist
    if not md_file.exists():
        md_file.write_text("""# Excluded Group Merges

This file records group clusters that have been reviewed and explicitly excluded from merging.
These clusters will be skipped in future runs of `find_related_groups.py`.

Format: One cluster per entry, with GroupIDs of all groups in the cluster.

## Excluded Clusters

""")

    # Append new exclusion
    with open(md_file, "a", encoding="utf-8") as f:
        f.write(f"\n### Excluded on {datetime.now().strftime('%Y-%m-%d %H:%M')}\n")
        f.write(f"**Reason:** User excluded during merge review\n")
        f.write(f"**Date Excluded:** {datetime.now().strftime('%Y-%m-%d')}\n")
        f.write(f"**Groups:**\n")
        for group in groups:
            f.write(f"- {group['name']} ({group['filename']})\n")
        f.write(f"\n**GroupIDs:**\n")
        f.write("```\n")
        for group in groups:
            f.write(f"{group['GroupID']}\n")
        f.write("```\n")
        f.write("\n---\n")


def _save_exclusion_pairs(groups_dir, excluded, remaining):
    """Save pairwise exclusions between excluded and remaining groups."""
    exclusion_file = groups_dir / "not_related.json"
    exclusions: Dict = {"comment": "Confirmed non-related groups", "exclusions": []}
    if exclusion_file.exists():
        with open(exclusion_file, "r", encoding="utf-8") as f:
            exclusions = json.load(f)
    for ex in excluded:
        for rem in remaining:
            exclusions["exclusions"].append(
                {"group1": ex["filename"], "group2": rem["filename"]}
            )
    with open(exclusion_file, "w", encoding="utf-8") as f:
        json.dump(exclusions, f, indent=2, ensure_ascii=False)


def _handle_exclude(groups_dir, groups):
    """Handle the exclude action. Returns groups to merge, or None to skip."""
    exclude_indices = get_exclusions_from_user(groups)

    if not exclude_indices:
        print("❌ No exclusions specified")
        return None

    if len(exclude_indices) == len(groups):
        add_to_exclusion_list(groups_dir, groups)
        print("✓ Added entire cluster to exclusion list")
        return None

    excluded = [groups[i] for i in exclude_indices]
    remaining = [g for i, g in enumerate(groups) if i not in exclude_indices]
    _save_exclusion_pairs(groups_dir, excluded, remaining)
    print(f"✓ Excluded {len(excluded)} group(s)")

    if len(remaining) < 2:
        print("Only 1 group remains - nothing to merge")
        return None

    print(f"\n{len(remaining)} groups remain:")
    for i, g in enumerate(remaining, 1):
        print(f"{i}. {g['name']} ({g['filename']})")
    if input("\nMerge remaining groups? (y/n): ").lower() != "y":
        return None
    return remaining


def _parse_notgroup_indices(response: str, count: int) -> Optional[List[int]]:
    """Parse user input for not-a-group selection. Returns indices or None."""
    if response.lower() == "all":
        return list(range(count))
    try:
        indices = [int(x.strip()) - 1 for x in response.split(",")]
        if all(0 <= i < count for i in indices):
            return indices
    except ValueError:
        pass
    print("❌ Invalid input")
    return None


def _handle_notgroup(groups_dir, groups):
    """Mark items as not-a-group: delete files, update index, remember in not_groups.json."""
    print("\nEnter numbers of items that are NOT groups (comma-separated, or 'all'):")
    for i, g in enumerate(groups, 1):
        print(f"  {i}. {g['name']} ({g['filename']})")

    indices = _parse_notgroup_indices(input("> ").strip(), len(groups))
    if indices is None:
        return None

    # Save to not_groups.json
    ng_file = groups_dir / "not_groups.json"
    data = {"names": []}
    if ng_file.exists():
        data = json.loads(ng_file.read_text(encoding="utf-8"))
    existing = set(data.get("names", []))

    # Load index
    index_file = groups_dir / "index.json"
    index = {}
    if index_file.exists():
        index = json.loads(index_file.read_text(encoding="utf-8"))

    for idx in indices:
        g = groups[idx]
        name = g["name"]
        existing.add(name.lower())
        # Delete file
        fpath = groups_dir / g["filename"]
        if fpath.exists():
            fpath.unlink()
            print(f"  ✓ Deleted {g['filename']}")
        # Remove from index
        norm = name.lower().strip()
        if norm in index:
            del index[norm]

    data["names"] = sorted(existing)
    ng_file.write_text(json.dumps(data, indent=2), encoding="utf-8")
    if index_file.exists():
        index_file.write_text(
            json.dumps(index, indent=2, ensure_ascii=False), encoding="utf-8"
        )

    print(f"✓ Marked {len(indices)} item(s) as not-a-group")

    remaining = [g for i, g in enumerate(groups) if i not in indices]
    if len(remaining) < 2:
        return None
    return None  # Don't auto-merge after removing non-groups


def _replace_id_in_entity_files(output_root: Path, old_id: str, new_id: str):
    """Replace old_id with new_id in logistics, casualties, and weather JSON files."""
    for subdir in ("logistics", "casualties", "weather"):
        entity_dir = output_root / subdir
        if not entity_dir.exists():
            continue
        for f in entity_dir.glob("*.json"):
            try:
                text = f.read_text(encoding="utf-8")
            except OSError:
                continue
            if old_id not in text:
                continue
            f.write_text(text.replace(old_id, new_id), encoding="utf-8")


def _update_event_refs(output_root: Path, old_id: str, new_id: str, ref_key: str):
    """Replace old entity ID with new ID in all event and entity files."""
    for f in sorted(output_root.glob("*/*-event.json")):
        try:
            with open(f, "r", encoding="utf-8") as fh:
                d = json.load(fh)
        except (OSError, json.JSONDecodeError):
            continue
        event = d.get("Event", d)
        changed = False
        for se in event.get("Sub-events", []):
            refs = se.get(ref_key, [])
            if old_id in refs:
                refs[refs.index(old_id)] = new_id
                changed = True
        if changed:
            with open(f, "w", encoding="utf-8") as fh:
                json.dump(d, fh, indent=2, ensure_ascii=False)

    _replace_id_in_entity_files(output_root, old_id, new_id)


def _execute_merge(groups_dir, groups):
    """Prompt for primary, merge, save, and delete others."""
    primary_idx = get_primary_index(groups)
    primary = groups[primary_idx]
    others = [g for i, g in enumerate(groups) if i != primary_idx]

    print(f"\n✓ Merging {len(others)} group(s) into: {primary['name']}")

    primary_data = load_group_file(groups_dir, primary["filename"])
    others_data = [load_group_file(groups_dir, g["filename"]) for g in others]
    merged = merge_groups(primary_data, others_data)
    save_group_file(groups_dir, primary["filename"], merged)

    primary_id = merged.get("GroupID") or merged.get("PeopleGroupID", "")
    output_root = groups_dir.parent

    for other, other_data in zip(others, others_data):
        old_id = other_data.get("GroupID") or other_data.get("PeopleGroupID", "")
        (groups_dir / other["filename"]).unlink()
        print(f"  Deleted: {other['filename']}")
        if old_id and primary_id:
            _update_event_refs(output_root, old_id, primary_id, "peoplegroups")
    print(f"✓ Merged into: {primary['filename']}")


def merge_related_cluster(groups_dir: Path, cluster: Dict) -> bool:
    """Merge a related cluster. Returns True to continue, False to stop."""
    groups = [g for g in cluster["groups"] if (groups_dir / g["filename"]).exists()]

    if len(groups) < 2:
        print(f"\nSkipping cluster - only {len(groups)} file(s) exist")
        return True

    print("\n" + "=" * 80)
    print(f"Related Cluster (Confidence: {cluster['confidence']:.2f})")
    print(f"Reasons: {', '.join(cluster['reasons'])}")
    print("=" * 80)
    for i, group in enumerate(groups, 1):
        print(f"{i}. {group['name']} ({group['filename']})")

    action = get_user_action()
    if action == "stop":
        return False
    if action == "skip":
        return True
    if action == "exclude":
        groups = _handle_exclude(groups_dir, groups)
        if groups is None:
            return True
    if action == "notgroup":
        _handle_notgroup(groups_dir, groups)
        return True

    _execute_merge(groups_dir, groups)
    return True


def main():
    """Main entry point."""
    groups_dir = Path("output/people_groups")
    report_file = groups_dir / "related_groups_report.json"

    if not report_file.exists():
        print("❌ No related groups report found")
        print("Run: python3 scripts/find_related_groups.py")
        sys.exit(1)

    with open(report_file, "r", encoding="utf-8") as f:
        report = json.load(f)

    # Get relationships array (not the count)
    clusters = report.get("relationships", [])

    if not clusters:
        print("✓ No related clusters found")
        return

    print(f"\nFound {len(clusters)} related cluster(s)")
    print("\nOptions:")
    print("  y = merge this cluster")
    print("  n = don't merge, exit")
    print("  skip = skip this cluster, continue to next")
    print("  exclude = mark as NOT related (prevents future detection)")
    print("  notgroup = mark items as NOT a group (deletes files, remembers)")

    merged_count = 0
    for cluster in clusters:
        if not merge_related_cluster(groups_dir, cluster):
            break
        merged_count += 1

    print(f"\n✓ Processed {merged_count} cluster(s)")


if __name__ == "__main__":
    main()

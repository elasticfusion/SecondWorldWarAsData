#!/usr/bin/env python3
"""Merge duplicate equipment files with user confirmation."""

import json
import logging
import subprocess
import sys
from pathlib import Path
from typing import Dict, List

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

SKIP_FILES = {"index.json", ".processed_events.json", "duplicate_report.json", "not_duplicates.json"}


def load_duplicate_report(report_path: Path) -> List[Dict]:
    """Load the duplicate report."""
    with open(report_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data["duplicates"]


def load_equipment(equipment_dir: Path, filename: str) -> Dict:
    """Load an equipment file."""
    with open(equipment_dir / filename, "r", encoding="utf-8") as f:
        return json.load(f)


def merge_equipment_data(primary: Dict, secondary: Dict) -> Dict:
    """Merge secondary equipment into primary, deduplicating all lists."""
    # Mentions by MentionID
    existing_ids = {m["MentionID"] for m in primary.get("mentions", [])}
    for m in secondary.get("mentions", []):
        if m["MentionID"] not in existing_ids:
            primary.setdefault("mentions", []).append(m)

    # Media by URL
    existing_urls = {m["url"] for m in primary.get("media", [])}
    for m in secondary.get("media", []):
        if m["url"] not in existing_urls:
            primary.setdefault("media", []).append(m)

    # Alternate names
    existing_names = set(primary.get("alternate_names", []))
    # Also add secondary's common_name as alternate if different
    sec_name = secondary.get("common_name", "")
    if sec_name and sec_name != primary.get("common_name", ""):
        existing_names_lower = {n.lower() for n in existing_names}
        if sec_name.lower() not in existing_names_lower:
            primary.setdefault("alternate_names", []).append(sec_name)
            existing_names.add(sec_name)
    for n in secondary.get("alternate_names", []):
        if n not in existing_names:
            primary.setdefault("alternate_names", []).append(n)

    # Variants by variant_name
    existing_variants = {v["variant_name"] for v in primary.get("variants", [])}
    for v in secondary.get("variants", []):
        if v["variant_name"] not in existing_variants:
            primary.setdefault("variants", []).append(v)

    return primary


def update_index(index_path: Path, old_name: str, new_filename: str):
    """Update index.json to point old name to new file."""
    if not index_path.exists():
        return
    with open(index_path, "r", encoding="utf-8") as f:
        index = json.load(f)
    index[old_name.lower().strip()] = new_filename
    with open(index_path, "w", encoding="utf-8") as f:
        json.dump(index, f, indent=2, ensure_ascii=False)


def add_to_exclusion_list(equipment_dir: Path, items: List[Dict]):
    """Add pairs to the exclusion list."""
    exclusion_file = equipment_dir / "not_duplicates.json"
    exclusions = {"comment": "Confirmed non-duplicates", "exclusions": []}
    if exclusion_file.exists():
        with open(exclusion_file, "r", encoding="utf-8") as f:
            exclusions = json.load(f)
    for i, item1 in enumerate(items):
        for item2 in items[i + 1:]:
            exclusions["exclusions"].append({
                "file1": item1["filename"],
                "file2": item2["filename"],
            })
    with open(exclusion_file, "w", encoding="utf-8") as f:
        json.dump(exclusions, f, indent=2, ensure_ascii=False)


def _mention_count(equipment_dir: Path, filename: str) -> int:
    """Count mentions for sorting (more mentions = more authoritative)."""
    try:
        data = load_equipment(equipment_dir, filename)
        return len(data.get("mentions", []))
    except Exception:
        return 0


def _get_user_action() -> str:
    """Get user action. Returns: 'merge', 'skip', 'exclude', 'stop'."""
    response = input("\nMerge this group? (y/n/skip/exclude): ").lower()
    if response == "n":
        return "stop"
    if response == "skip":
        return "skip"
    if response in ("exclude", "e"):
        return "exclude"
    return "merge"


def _get_primary_index(items: list) -> int:
    """Get index of primary equipment item."""
    choice = input(
        f"\nKeep which as primary? (1-{len(items)}, default=1): "
    ).strip()
    if not choice:
        return 0
    if not choice.isdigit():
        return -1
    idx = int(choice) - 1
    return idx if 0 <= idx < len(items) else -1


def merge_duplicate_group(equipment_dir: Path, group: Dict):
    """Merge a duplicate group interactively."""
    items = [p for p in group["people"] if (equipment_dir / p["filename"]).exists()]

    if len(items) < 2:
        print(f"\nSkipping group - only {len(items)} file(s) still exist")
        return None

    # Sort by mention count (most mentions first)
    items.sort(key=lambda p: _mention_count(equipment_dir, p["filename"]), reverse=True)

    print("\n" + "=" * 80)
    print(f"Duplicate Group (Confidence: {group['confidence']:.2f})")
    print(f"Reasons: {', '.join(group['reasons'])}")
    print("=" * 80)

    for i, item in enumerate(items, 1):
        count = _mention_count(equipment_dir, item["filename"])
        print(f"{i}. {item['name']} ({item['filename']}) [{count} mentions]")

    action = _get_user_action()
    if action == "stop":
        return False
    if action == "skip":
        return None
    if action == "exclude":
        add_to_exclusion_list(equipment_dir, items)
        print("✓ Added to exclusion list")
        return None

    primary_idx = _get_primary_index(items)
    if primary_idx < 0:
        print("Invalid choice, skipping")
        return None

    primary = items[primary_idx]
    primary_data = load_equipment(equipment_dir, primary["filename"])
    print(f"\nMerging into: {primary['name']}")

    for i, item in enumerate(items):
        if i == primary_idx:
            continue
        print(f"  Merging: {item['name']}")
        secondary_data = load_equipment(equipment_dir, item["filename"])
        primary_data = merge_equipment_data(primary_data, secondary_data)
        update_index(equipment_dir / "index.json", item["name"], primary["filename"])
        (equipment_dir / item["filename"]).unlink()
        print(f"    Deleted: {item['filename']}")

    with open(equipment_dir / primary["filename"], "w", encoding="utf-8") as f:
        json.dump(primary_data, f, indent=2, ensure_ascii=False)

    print(f"✓ Merged {len(items) - 1} duplicate(s) into {primary['name']}")
    return True


def main():
    """Main entry point."""
    script_dir = Path(__file__).parent
    project_root = script_dir.parent if script_dir.name == "scripts" else script_dir

    equipment_dir = project_root / "output" / "equipment"
    report_path = equipment_dir / "duplicate_report.json"

    if not report_path.exists():
        logger.error("No duplicate report found. Run find_duplicate_equipment.py first.")
        return 1

    duplicates = load_duplicate_report(report_path)
    if not duplicates:
        print("No duplicates found in report.")
        return 0

    print(f"\nFound {len(duplicates)} duplicate group(s)")
    print("\nOptions:")
    print("  y = merge this group")
    print("  n = don't merge, exit")
    print("  skip = skip this group, continue to next")
    print("  exclude = mark as NOT duplicates")

    merged_count = 0
    skipped_count = 0

    for group in duplicates:
        result = merge_duplicate_group(equipment_dir, group)
        if result is True:
            merged_count += 1
        elif result is False:
            print("\nStopping merge process.")
            break
        else:
            skipped_count += 1

    print("\n" + "=" * 80)
    print(f"Merge complete: {merged_count} group(s) merged, {skipped_count} skipped")
    print("=" * 80)

    # Regenerate report
    print("\nRegenerating duplicate report...")
    find_script = project_root / "scripts" / "find_duplicate_equipment.py"
    subprocess.run([sys.executable, str(find_script)], check=False)

    return 0


if __name__ == "__main__":
    sys.exit(main())

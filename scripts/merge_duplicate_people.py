#!/usr/bin/env python3
"""
Merge duplicate people files with user confirmation.
"""

import json
import logging
import subprocess
import sys
from pathlib import Path
from typing import Dict, List

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.extraction.people import _merge_person

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def load_duplicate_report(report_path: Path) -> List[Dict]:
    """Load the duplicate report."""
    with open(report_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data["duplicates"]


def load_person(people_dir: Path, filename: str) -> Dict:
    """Load a person file."""
    with open(people_dir / filename, "r", encoding="utf-8") as f:
        return json.load(f)


def merge_people(primary: Dict, secondary: Dict) -> Dict:
    """
    Merge secondary person into primary using the standard merge logic.
    """
    return _merge_person(primary, secondary)


def update_index(index_path: Path, old_name: str, new_filename: str):
    """Update index.json to point old name to new file."""
    with open(index_path, "r", encoding="utf-8") as f:
        index = json.load(f)

    # Normalize name for lookup
    normalized = old_name.lower().strip()
    index[normalized] = new_filename

    with open(index_path, "w", encoding="utf-8") as f:
        json.dump(index, f, indent=2, ensure_ascii=False)


def add_to_exclusion_list(people_dir: Path, people: List[Dict]):
    """Add a pair to the exclusion list."""
    exclusion_file = people_dir / "not_duplicates.json"

    # Load existing exclusions
    exclusions: Dict = {"comment": "Confirmed non-duplicates", "exclusions": []}
    if exclusion_file.exists():
        with open(exclusion_file, "r", encoding="utf-8") as f:
            exclusions = json.load(f)

    # Add all pairs in this group
    for i, person1 in enumerate(people):
        for person2 in people[i + 1 :]:
            exclusions["exclusions"].append(
                {"person1": person1["filename"], "person2": person2["filename"]}
            )

    # Save
    with open(exclusion_file, "w", encoding="utf-8") as f:
        json.dump(exclusions, f, indent=2, ensure_ascii=False)


def name_completeness_score(name: str) -> tuple:
    """Score name completeness for sorting.

    Returns tuple: (has_suffix, word_count, total_length, name)
    Higher scores = more complete names
    """
    # Check for suffixes (Jr., Sr., III, etc.)
    has_suffix = any(
        suffix in name.upper()
        for suffix in ["JR.", "JR", "SR.", "SR", " II", " III", " IV"]
    )

    # Count words (more words = more complete)
    word_count = len(name.split())

    # Total character length
    total_length = len(name)

    return (has_suffix, word_count, total_length, name)


def _get_user_action(auto_confirm: bool) -> str:
    """Get user action for merge group. Returns: 'merge', 'skip', 'exclude', 'stop'."""
    if auto_confirm:
        return "merge"
    response = input("\nMerge this group? (y/n/skip/exclude): ").lower()
    if response == "n":
        return "stop"
    if response == "skip":
        return "skip"
    if response in ["exclude", "e"]:
        return "exclude"
    return "merge"


def _get_exclusions_from_user(people: List[Dict]) -> List[int]:
    """Prompt user to select which people to exclude from the group.
    
    Returns list of indices (0-based) to exclude.
    """
    while True:
        response = input("\nEnter person numbers to exclude (comma-separated, or 'all' for entire group): ").strip()
        
        if response.lower() == "all":
            return list(range(len(people)))
        
        try:
            # Parse comma-separated numbers (1-based from user)
            indices = [int(x.strip()) - 1 for x in response.split(",")]
            
            # Validate indices
            if all(0 <= i < len(people) for i in indices):
                return indices
            else:
                print(f"❌ Invalid numbers. Please enter numbers between 1 and {len(people)}")
        except ValueError:
            print("❌ Invalid input. Please enter numbers separated by commas (e.g., 1,4,5)")



def _get_primary_index(people: list, auto_confirm: bool) -> int:
    """Get index of primary person. Returns -1 if invalid.
    
    Assumes people list is already sorted by completeness (most complete first).
    """
    default_idx = 0  # Most complete name is first after sorting

    if len(people) == 2:
        if auto_confirm:
            return default_idx
        choice = input(
            f"\nKeep which person as primary? (1-{len(people)}, default={default_idx + 1}): "
        ).strip()
        return int(choice) - 1 if choice and choice.isdigit() else default_idx

    # Multiple people, show default
    choice = input(
        f"\nKeep which person as primary? (1-{len(people)}, default={default_idx + 1}): "
    ).strip()
    if not choice:
        return default_idx
    if not choice.isdigit():
        return -1
    idx = int(choice) - 1
    return idx if 0 <= idx < len(people) else -1


def merge_duplicate_group(people_dir: Path, group: Dict, auto_confirm: bool = False):
    """Merge a duplicate group. Prompts user to select primary person, then merges others into it."""
    # Filter out people whose files no longer exist (already merged)
    people = [p for p in group["people"] if (people_dir / p["filename"]).exists()]
    
    if len(people) < 2:
        print(f"\nSkipping group - only {len(people)} file(s) still exist (likely already merged)")
        return None
    
    # Sort by name completeness
    people = sorted(people, key=lambda p: name_completeness_score(p["name"]), reverse=True)

    print("\n" + "=" * 80)
    print(f"Duplicate Group (Confidence: {group['confidence']:.2f})")
    print(f"Reasons: {', '.join(group['reasons'])}")
    print("=" * 80)

    for i, person in enumerate(people, 1):
        print(f"{i}. {person['name']} ({person['filename']})")

    # Get user action
    action = _get_user_action(auto_confirm)
    if action == "stop":
        return False
    if action == "skip":
        return None
    if action == "exclude":
        # Get which people to exclude
        exclude_indices = _get_exclusions_from_user(people)
        
        if not exclude_indices:
            print("❌ No exclusions specified")
            return None
        
        # If excluding all, add all pairs to exclusion list
        if len(exclude_indices) == len(people):
            add_to_exclusion_list(people_dir, people)
            print("✓ Added entire group to exclusion list")
            return None
        
        # Otherwise, add pairs between excluded and remaining people
        excluded_people = [people[i] for i in exclude_indices]
        remaining_people = [p for i, p in enumerate(people) if i not in exclude_indices]
        
        # Add exclusion pairs
        exclusion_file = people_dir / "not_duplicates.json"
        exclusions: Dict = {"comment": "Confirmed non-duplicates", "exclusions": []}
        if exclusion_file.exists():
            with open(exclusion_file, "r", encoding="utf-8") as f:
                exclusions = json.load(f)
        
        for excluded in excluded_people:
            for remaining in remaining_people:
                exclusions["exclusions"].append({
                    "person1": excluded["filename"],
                    "person2": remaining["filename"]
                })
        
        with open(exclusion_file, "w", encoding="utf-8") as f:
            json.dump(exclusions, f, indent=2, ensure_ascii=False)
        
        print(f"✓ Excluded {len(excluded_people)} person(s) from this group")
        
        # Ask if user wants to merge the remaining people
        if len(remaining_people) >= 2:
            print(f"\n{len(remaining_people)} people remain in group:")
            for i, p in enumerate(remaining_people, 1):
                print(f"{i}. {p['name']} ({p['filename']})")
            
            merge_remaining = input("\nMerge remaining people? (y/n): ").lower()
            if merge_remaining != "y":
                return None
            
            # Continue with merge of remaining people
            people = remaining_people
        else:
            print("Only 1 person remains - nothing to merge")
            return None

    # Get primary person
    primary_idx = _get_primary_index(people, auto_confirm)
    if primary_idx < 0:
        print("Invalid choice, skipping group")
        return None

    # Merge
    primary_person = people[primary_idx]
    primary_data = load_person(people_dir, primary_person["filename"])
    print(f"\nMerging into: {primary_person['name']}")

    for i, person in enumerate(people):
        if i == primary_idx:
            continue
        print(f"  Merging: {person['name']}")
        secondary_data = load_person(people_dir, person["filename"])
        primary_data = merge_people(primary_data, secondary_data)
        update_index(
            people_dir / "index.json", person["name"], primary_person["filename"]
        )
        (people_dir / person["filename"]).unlink()
        print(f"    Deleted: {person['filename']}")

    with open(people_dir / primary_person["filename"], "w", encoding="utf-8") as f:
        json.dump(primary_data, f, indent=2, ensure_ascii=False)

    print(f"✓ Merged {len(people)-1} duplicate(s) into {primary_person['name']}")
    return True


def main():
    """Main entry point."""
    # Find project root (where output/ directory exists)
    script_dir = Path(__file__).parent
    project_root = script_dir.parent if script_dir.name == "scripts" else script_dir

    people_dir = project_root / "output/people"
    report_path = people_dir / "duplicate_report.json"

    if not report_path.exists():
        logger.error("No duplicate report found. Run find_duplicate_people.py first.")
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
    print("  exclude = mark as NOT duplicates (prevents future detection)")

    merged_count = 0
    skipped_count = 0

    for group in duplicates:
        result = merge_duplicate_group(people_dir, group)
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

    # Regenerate duplicate report
    print("\nRegenerating duplicate report...")
    find_script = project_root / "scripts" / "find_duplicate_people.py"
    subprocess.run(["python3", str(find_script)], check=False)

    return 0


if __name__ == "__main__":
    import sys

    sys.exit(main())

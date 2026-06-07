#!/usr/bin/env python3
"""Merge duplicate place files with interactive prompts."""

import json
import yaml
from pathlib import Path
from collections import defaultdict
from math import radians, cos, sin, asin, sqrt


def haversine(lat1, lon1, lat2, lon2):
    """Calculate distance between two points in km."""
    lat1, lon1, lat2, lon2 = map(radians, [lat1, lon1, lat2, lon2])
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = sin(dlat / 2) ** 2 + cos(lat1) * cos(lat2) * sin(dlon / 2) ** 2
    return 2 * asin(sqrt(a)) * 6371


def load_config():
    """Load configuration from config/place_aliases.yaml."""
    with open("config/place_aliases.yaml", "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def normalize_name(name, rules):
    """Normalize place name using rules from config."""
    normalized = name.lower()
    for rule in rules:
        normalized = normalized.replace(rule, "")
    return normalized.strip()


def load_exclusions(places_dir):
    """Load exclusion list."""
    exclusion_file = places_dir / "not_duplicates.json"
    if exclusion_file.exists():
        with open(exclusion_file, "r", encoding="utf-8") as f:
            return json.load(f)
    return []


def save_exclusions(places_dir, exclusions):
    """Save exclusion list."""
    exclusion_file = places_dir / "not_duplicates.json"
    with open(exclusion_file, "w", encoding="utf-8") as f:
        json.dump(exclusions, f, indent=2, ensure_ascii=False)


def is_excluded(place_ids, exclusions):
    """Check if this group is in exclusion list."""
    place_set = set(place_ids)
    for excluded_group in exclusions:
        if place_set == set(excluded_group):
            return True
    return False


def get_user_action():
    """Get user action. Returns: 'merge', 'skip', 'exclude', 'stop'."""
    response = input("\nMerge this group? (y/n/skip/exclude): ").lower()
    if response == "n":
        return "stop"
    if response == "skip":
        return "skip"
    if response in ["exclude", "e"]:
        return "exclude"
    return "merge"


def get_exclusion_indices(places):
    """Prompt user to select which places to exclude."""
    while True:
        response = input(
            "\nEnter place numbers to exclude (comma-separated, or 'all'): "
        ).strip()

        if response.lower() == "all":
            return list(range(len(places)))

        try:
            indices = [int(x.strip()) - 1 for x in response.split(",")]
            if all(0 <= i < len(places) for i in indices):
                return indices
            print(f"Invalid indices. Must be between 1 and {len(places)}")
        except ValueError:
            print("Invalid input. Enter comma-separated numbers or 'all'")


def choose_primary(places):
    """Choose which place to keep as primary."""
    if len(places) == 1:
        return 0

    # Default: most mentions
    default_idx = 0
    max_mentions = max(len(p["data"].get("event_mentions", [])) for p in places)
    for i, p in enumerate(places):
        if len(p["data"].get("event_mentions", [])) == max_mentions:
            default_idx = i
            break

    choice = input(
        f"\nKeep which place as primary? (1-{len(places)}, default={default_idx + 1}): "
    ).strip()
    if not choice:
        return default_idx
    if choice.isdigit() and 1 <= int(choice) <= len(places):
        return int(choice) - 1
    return default_idx


def merge_places(places_data):
    """Merge multiple place records into one."""
    base = places_data[0].copy()

    # Collect all aliases
    all_aliases = set(base.get("aliases", []))
    for place in places_data[1:]:
        if place["current_name"] != base["current_name"]:
            all_aliases.add(place["current_name"])
        all_aliases.update(place.get("aliases", []))

    base["aliases"] = sorted(all_aliases)

    # Merge event mentions
    all_mentions = []
    seen_mentions = set()

    for place in places_data:
        for mention in place.get("event_mentions", []):
            mention_id = mention.get("MentionID")
            if mention_id not in seen_mentions:
                all_mentions.append(mention)
                seen_mentions.add(mention_id)

    base["event_mentions"] = all_mentions
    return base


def _handle_exclude(places, places_dir, exclusions):
    """Handle exclude action. Returns places to merge, or None to skip."""
    exclude_indices = get_exclusion_indices(places)
    excluded_ids = [places[i]["data"]["PlaceID"] for i in exclude_indices]
    exclusions.append(excluded_ids)
    save_exclusions(places_dir, exclusions)
    print(f"✓ Added {len(excluded_ids)} place(s) to exclusion list")

    remaining = [p for i, p in enumerate(places) if i not in exclude_indices]
    if len(remaining) < 2:
        print("⊘ Not enough places remaining to merge")
        return None

    print(f"\n{len(remaining)} places remain in group:")
    for i, p in enumerate(remaining, 1):
        print(f"{i}. {p['data']['current_name']} ({p['file'].name})")
    if input("\nMerge remaining places? (y/n): ").lower() != "y":
        return None
    return remaining


def _execute_merge(places, places_dir):
    """Choose primary, merge, save, delete others."""
    primary_idx = choose_primary(places)
    places = [places[primary_idx]] + [
        p for i, p in enumerate(places) if i != primary_idx
    ]

    merged_data = merge_places([p["data"] for p in places])

    with open(places[0]["file"], "w", encoding="utf-8") as f:
        json.dump(merged_data, f, indent=2, ensure_ascii=False)

    print(
        f"\n✓ Kept: {places[0]['file'].name} ({len(merged_data['event_mentions'])} mentions)"
    )
    if merged_data.get("aliases"):
        print(f"  Aliases: {', '.join(merged_data['aliases'])}")

    for p in places[1:]:
        p["file"].unlink()
        print(f"✗ Deleted: {p['file'].name}")


def merge_group(places, places_dir, exclusions):
    """Merge a group of duplicate places."""
    print(f"\n{'='*60}")
    print(f"Found {len(places)} duplicate places:")
    for i, p in enumerate(places, 1):
        mentions = len(p["data"].get("event_mentions", []))
        coords = (
            f"({p['lat']:.4f}, {p['lon']:.4f})"
            if p["lat"] and p["lon"]
            else "no coords"
        )
        print(f"{i}. {p['data']['current_name']}")
        print(f"   File: {p['file'].name}")
        print(f"   PlaceID: {p['data']['PlaceID']}")
        print(f"   Coords: {coords}")
        print(f"   Mentions: {mentions}")

    place_ids = [p["data"]["PlaceID"] for p in places]
    if is_excluded(place_ids, exclusions):
        print("\n⊘ This group is in exclusion list, skipping")
        return None

    action = get_user_action()
    if action == "stop":
        return "stop"
    if action == "skip":
        print("⊘ Skipped")
        return None
    if action == "exclude":
        places = _handle_exclude(places, places_dir, exclusions)
        if places is None:
            return None

    _execute_merge(places, places_dir)
    return "merged"


def _find_duplicates(
    places_dir, normalization_rules, large_region_types, merge_distance
):
    """Load places, group by normalized name, return duplicate groups."""
    by_normalized = defaultdict(list)
    for place_file in places_dir.glob("*.json"):
        if place_file.name == "index.json":
            continue
        with open(place_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        name = data.get("current_name", "")
        normalized = normalize_name(name, normalization_rules)
        coords = data.get("coordinates", {})
        by_normalized[normalized].append(
            {
                "file": place_file,
                "data": data,
                "lat": coords.get("latitude"),
                "lon": coords.get("longitude"),
            }
        )

    duplicates = {}
    for normalized, places in by_normalized.items():
        if len(places) <= 1:
            continue
        is_large = any(
            p["data"].get("geography_type") in large_region_types for p in places
        )
        if is_large:
            duplicates[normalized] = places
        else:
            for i, p1 in enumerate(places):
                for p2 in places[i + 1 :]:
                    if p1["lat"] and p1["lon"] and p2["lat"] and p2["lon"]:
                        if (
                            haversine(p1["lat"], p1["lon"], p2["lat"], p2["lon"])
                            <= merge_distance
                        ):
                            duplicates[normalized] = places
                            break
    return duplicates


def _rebuild_index(places_dir):
    """Rebuild the places index.json from all place files."""
    index = {}
    for place_file in places_dir.glob("*.json"):
        if place_file.name == "index.json":
            continue
        with open(place_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        name = data.get("current_name", "").lower()
        index[name] = place_file.name
        for alias in data.get("aliases", []):
            index[alias.lower()] = place_file.name

    with open(places_dir / "index.json", "w", encoding="utf-8") as f:
        json.dump(index, f, indent=2, ensure_ascii=False)

    print(f"✓ Index rebuilt with {len(set(index.values()))} places")
    print(f"✓ Index entries (including aliases): {len(index)}")


def main():
    config = load_config()
    normalization_rules = config.get("normalization_rules", [])
    large_region_types = config.get("large_region_types", [])
    merge_distance = config.get("merge_distance_km", 50)
    places_dir = Path("output/places")

    exclusions = load_exclusions(places_dir)
    print(f"Loaded {len(exclusions)} exclusion group(s)")

    duplicates = _find_duplicates(
        places_dir, normalization_rules, large_region_types, merge_distance
    )

    if not duplicates:
        print("No duplicates found")
        return

    print(f"\nFound {len(duplicates)} duplicate group(s)")
    print("Commands: y=merge, n=stop, skip=skip group, exclude=exclude from future")

    merged_count = 0
    for _normalized, places in sorted(duplicates.items()):
        result = merge_group(places, places_dir, exclusions)
        if result == "stop":
            print("\n⊘ Stopped by user")
            break
        if result == "merged":
            merged_count += 1

    print(f"\n{'='*60}")
    print("Rebuilding index...")
    _rebuild_index(places_dir)
    print(f"\n✓ Merged {merged_count} duplicate group(s)")


if __name__ == "__main__":
    main()

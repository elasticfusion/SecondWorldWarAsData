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
    a = sin(dlat/2)**2 + cos(lat1) * cos(lat2) * sin(dlon/2)**2
    return 2 * asin(sqrt(a)) * 6371


def load_config():
    """Load configuration from config/place_aliases.yaml."""
    with open("config/place_aliases.yaml", 'r', encoding='utf-8') as f:
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
        with open(exclusion_file, 'r', encoding='utf-8') as f:
            return json.load(f)
    return []


def save_exclusions(places_dir, exclusions):
    """Save exclusion list."""
    exclusion_file = places_dir / "not_duplicates.json"
    with open(exclusion_file, 'w', encoding='utf-8') as f:
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
        response = input("\nEnter place numbers to exclude (comma-separated, or 'all'): ").strip()
        
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
    
    choice = input(f"\nKeep which place as primary? (1-{len(places)}, default={default_idx + 1}): ").strip()
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


def merge_group(places, places_dir, exclusions):
    """Merge a group of duplicate places."""
    print(f"\n{'='*60}")
    print(f"Found {len(places)} duplicate places:")
    for i, p in enumerate(places, 1):
        mentions = len(p["data"].get("event_mentions", []))
        coords = f"({p['lat']:.4f}, {p['lon']:.4f})" if p['lat'] and p['lon'] else "no coords"
        print(f"{i}. {p['data']['current_name']}")
        print(f"   File: {p['file'].name}")
        print(f"   PlaceID: {p['data']['PlaceID']}")
        print(f"   Coords: {coords}")
        print(f"   Mentions: {mentions}")
    
    # Check if excluded
    place_ids = [p["data"]["PlaceID"] for p in places]
    if is_excluded(place_ids, exclusions):
        print("\n⊘ This group is in exclusion list, skipping")
        return None
    
    # Get user action
    action = get_user_action()
    
    if action == "stop":
        return "stop"
    
    if action == "skip":
        print("⊘ Skipped")
        return None
    
    if action == "exclude":
        # Get exclusion indices
        exclude_indices = get_exclusion_indices(places)
        
        # Add to exclusions
        excluded_ids = [places[i]["data"]["PlaceID"] for i in exclude_indices]
        exclusions.append(excluded_ids)
        save_exclusions(places_dir, exclusions)
        print(f"✓ Added {len(excluded_ids)} place(s) to exclusion list")
        
        # Check if any remain
        remaining_places = [p for i, p in enumerate(places) if i not in exclude_indices]
        
        if len(remaining_places) >= 2:
            print(f"\n{len(remaining_places)} places remain in group:")
            for i, p in enumerate(remaining_places, 1):
                print(f"{i}. {p['data']['current_name']} ({p['file'].name})")
            
            merge_remaining = input("\nMerge remaining places? (y/n): ").lower()
            if merge_remaining != "y":
                return None
            
            places = remaining_places
        else:
            print("⊘ Not enough places remaining to merge")
            return None
    
    # Choose primary
    primary_idx = choose_primary(places)
    
    # Reorder so primary is first
    places = [places[primary_idx]] + [p for i, p in enumerate(places) if i != primary_idx]
    
    # Merge data
    merged_data = merge_places([p["data"] for p in places])
    
    # Keep primary file, delete others
    keep_file = places[0]["file"]
    delete_files = [p["file"] for p in places[1:]]
    
    # Write merged data
    with open(keep_file, 'w', encoding='utf-8') as f:
        json.dump(merged_data, f, indent=2, ensure_ascii=False)
    
    print(f"\n✓ Kept: {keep_file.name} ({len(merged_data['event_mentions'])} mentions)")
    if merged_data.get("aliases"):
        print(f"  Aliases: {', '.join(merged_data['aliases'])}")
    
    # Delete duplicates
    for dup_file in delete_files:
        dup_file.unlink()
        print(f"✗ Deleted: {dup_file.name}")
    
    return "merged"


def main():
    config = load_config()
    normalization_rules = config.get("normalization_rules", [])
    large_region_types = config.get("large_region_types", [])
    merge_distance = config.get("merge_distance_km", 50)
    
    places_dir = Path("output/places")
    place_files = [f for f in places_dir.glob("*.json") if f.name != "index.json"]
    
    # Load exclusions
    exclusions = load_exclusions(places_dir)
    print(f"Loaded {len(exclusions)} exclusion group(s)")
    
    # Group by normalized name
    by_normalized = defaultdict(list)
    
    for place_file in place_files:
        with open(place_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        name = data.get("current_name", "")
        normalized = normalize_name(name, normalization_rules)
        coords = data.get("coordinates", {})
        lat = coords.get("latitude")
        lon = coords.get("longitude")
        
        by_normalized[normalized].append({
            "file": place_file,
            "data": data,
            "lat": lat,
            "lon": lon
        })
    
    # Find duplicates
    duplicates = {}
    for normalized, places in by_normalized.items():
        if len(places) > 1:
            is_large_region = any(p["data"].get("geography_type") in large_region_types
                                for p in places)
            
            if is_large_region:
                duplicates[normalized] = places
            else:
                # Check distance for non-large regions
                for i, p1 in enumerate(places):
                    for p2 in places[i+1:]:
                        if p1["lat"] and p1["lon"] and p2["lat"] and p2["lon"]:
                            dist = haversine(p1["lat"], p1["lon"], p2["lat"], p2["lon"])
                            if dist <= merge_distance:
                                duplicates[normalized] = places
                                break
    
    if not duplicates:
        print("No duplicates found")
        return
    
    print(f"\nFound {len(duplicates)} duplicate group(s)")
    print("Commands: y=merge, n=stop, skip=skip group, exclude=exclude from future")
    
    merged_count = 0
    
    for normalized, places in sorted(duplicates.items()):
        result = merge_group(places, places_dir, exclusions)
        
        if result == "stop":
            print("\n⊘ Stopped by user")
            break
        
        if result == "merged":
            merged_count += 1
    
    # Rebuild index
    print(f"\n{'='*60}")
    print("Rebuilding index...")
    index = {}
    for place_file in places_dir.glob("*.json"):
        if place_file.name == "index.json":
            continue
        
        with open(place_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        name = data.get("current_name", "").lower()
        index[name] = place_file.name
        
        for alias in data.get("aliases", []):
            index[alias.lower()] = place_file.name
    
    with open(places_dir / "index.json", 'w', encoding='utf-8') as f:
        json.dump(index, f, indent=2, ensure_ascii=False)
    
    print(f"✓ Index rebuilt with {len(set(index.values()))} places")
    print(f"✓ Index entries (including aliases): {len(index)}")
    print(f"\n✓ Merged {merged_count} duplicate group(s)")


if __name__ == "__main__":
    main()

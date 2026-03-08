#!/usr/bin/env python3
"""Find and report duplicate places based on name and coordinates."""

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


def main():
    config = load_config()
    normalization_rules = config.get("normalization_rules", [])
    large_region_types = config.get("large_region_types", [])
    
    places_dir = Path("output/places")
    place_files = [f for f in places_dir.glob("*.json") if f.name != "index.json"]
    
    # Group by exact name
    by_name = defaultdict(list)
    # Group by normalized name
    by_normalized = defaultdict(list)
    
    for place_file in place_files:
        with open(place_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        name = data.get("current_name", "").lower()
        normalized = normalize_name(name, normalization_rules)
        coords = data.get("coordinates", {})
        lat = coords.get("latitude")
        lon = coords.get("longitude")
        
        place_info = {
            "file": place_file.name,
            "PlaceID": data.get("PlaceID"),
            "name": name,
            "lat": lat,
            "lon": lon,
            "mentions": len(data.get("event_mentions", [])),
            "geography_type": data.get("geography_type")
        }
        
        by_name[name].append(place_info)
        by_normalized[normalized].append(place_info)
    
    # Find exact duplicates
    exact_duplicates = {name: places for name, places in by_name.items() if len(places) > 1}
    
    # Find semantic duplicates (same normalized name, close coords OR both missing coords)
    semantic_duplicates = {}
    for normalized, places in by_normalized.items():
        if len(places) > 1:
            # For large regions/theaters, just group by normalized name
            # For specific places, check distance
            is_large_region = any(p.get("geography_type") in large_region_types
                                for p in places)
            
            if is_large_region:
                # Large regions - group all with same normalized name
                semantic_duplicates[f"{normalized} (semantic)"] = places
            else:
                # Specific places - check distance
                groups = []
                for place in places:
                    added = False
                    for group in groups:
                        if place["lat"] and group[0]["lat"]:
                            dist = haversine(place["lat"], place["lon"], 
                                           group[0]["lat"], group[0]["lon"])
                            if dist < 50:
                                group.append(place)
                                added = True
                                break
                        elif not place["lat"] and not group[0]["lat"]:
                            group.append(place)
                            added = True
                            break
                    if not added:
                        groups.append([place])
                
                for group in groups:
                    if len(group) > 1:
                        semantic_duplicates[f"{normalized} (semantic)"] = group
    
    duplicates = {**exact_duplicates, **semantic_duplicates}
    
    if not duplicates:
        print("✓ No duplicates found")
        return
    
    print(f"Found {len(duplicates)} duplicate place names:\n")
    
    for name, places in sorted(duplicates.items()):
        print(f"📍 {name.title()}")
        for p in places:
            print(f"   - {p['file']}")
            print(f"     Name: {p['name']}")
            print(f"     PlaceID: {p['PlaceID']}")
            print(f"     Coords: ({p['lat']}, {p['lon']})")
            print(f"     Mentions: {p['mentions']}")
        print()
    
    print(f"\nTotal: {len(duplicates)} duplicate place names")
    print(f"Total files: {sum(len(places) for places in duplicates.values())}")


if __name__ == "__main__":
    main()

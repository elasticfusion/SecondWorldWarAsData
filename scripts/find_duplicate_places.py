#!/usr/bin/env python3
"""Find and report duplicate places based on name and coordinates."""

import json
import unicodedata
import yaml
from pathlib import Path
from collections import defaultdict
from difflib import SequenceMatcher
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


# Filler words to strip when comparing place names
_FILLER_WORDS = {" of ", " the ", " de ", " du ", " la ", " le ", " des "}


def _strip_accents(text):
    """Strip Unicode accents (é→e, ç→c, ñ→n)."""
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")


def normalize_name(name, rules):
    """Normalize place name using rules from config + accent/filler stripping."""
    normalized = name.lower()
    for rule in rules:
        normalized = normalized.replace(rule, "")
    # Strip accents
    normalized = _strip_accents(normalized)
    # Strip filler words
    for filler in _FILLER_WORDS:
        normalized = normalized.replace(filler, " ")
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
        with open(place_file, "r", encoding="utf-8") as f:
            data = json.load(f)

        name = data.get("current_name", "") or data.get("name", "")
        if not name:
            # Fall back to filename (e.g. "alençon.json" → "alençon")
            name = place_file.stem.replace("_", " ")
        name = name.lower()
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
            "geography_type": data.get("geography_type"),
        }

        by_name[name].append(place_info)
        by_normalized[normalized].append(place_info)

    # Find exact duplicates
    exact_duplicates = {
        name: places for name, places in by_name.items() if len(places) > 1
    }

    # Find semantic duplicates (same normalized name, close coords OR both missing coords)
    semantic_duplicates = {}
    for normalized, places in by_normalized.items():
        if len(places) > 1:
            # For large regions/theaters, just group by normalized name
            # For specific places, check distance
            is_large_region = any(
                p.get("geography_type") in large_region_types for p in places
            )

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
                            dist = haversine(
                                place["lat"],
                                place["lon"],
                                group[0]["lat"],
                                group[0]["lon"],
                            )
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

    # Fuzzy matching pass — catch near-identical names not caught by normalization
    # Build list of all unique normalized names with their places
    already_grouped = set()
    for places in duplicates.values():
        for p in places:
            already_grouped.add(p["file"])

    all_places = []
    for place_file in place_files:
        with open(place_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        name = data.get("current_name", data.get("name", ""))
        if not name or place_file.name in already_grouped:
            continue
        norm = normalize_name(name, normalization_rules)
        all_places.append((norm, place_file.name, name, data))

    for i, (norm1, file1, name1, data1) in enumerate(all_places):
        for norm2, file2, name2, data2 in all_places[i + 1 :]:
            if norm1 == norm2:
                continue  # Already caught by semantic pass
            ratio = SequenceMatcher(None, norm1, norm2).ratio()
            if ratio >= 0.92 and min(len(norm1), len(norm2)) >= 8:
                key = f"{name1} / {name2} (fuzzy {ratio:.0%})"
                coords1 = data1.get("coordinates", {})
                coords2 = data2.get("coordinates", {})
                duplicates[key] = [
                    {
                        "file": file1,
                        "name": name1,
                        "PlaceID": data1.get("PlaceID"),
                        "lat": coords1.get("latitude"),
                        "lon": coords1.get("longitude"),
                        "mentions": len(data1.get("event_mentions", [])),
                        "geography_type": data1.get("geography_type"),
                    },
                    {
                        "file": file2,
                        "name": name2,
                        "PlaceID": data2.get("PlaceID"),
                        "lat": coords2.get("latitude"),
                        "lon": coords2.get("longitude"),
                        "mentions": len(data2.get("event_mentions", [])),
                        "geography_type": data2.get("geography_type"),
                    },
                ]

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

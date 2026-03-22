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


def _place_info_from_data(data, filename):
    """Build a place_info dict from loaded JSON data."""
    coords = data.get("coordinates", {})
    return {
        "file": filename,
        "PlaceID": data.get("PlaceID"),
        "name": data.get("current_name", "") or data.get("name", ""),
        "lat": coords.get("latitude"),
        "lon": coords.get("longitude"),
        "mentions": len(data.get("event_mentions", [])),
        "geography_type": data.get("geography_type"),
    }


def _load_places(places_dir, normalization_rules):
    """Load all place files and index by name and normalized name."""
    by_name = defaultdict(list)
    by_normalized = defaultdict(list)

    for place_file in places_dir.glob("*.json"):
        if place_file.name == "index.json":
            continue
        with open(place_file, "r", encoding="utf-8") as f:
            data = json.load(f)

        info = _place_info_from_data(data, place_file.name)
        name = info["name"]
        if not name:
            name = place_file.stem.replace("_", " ")
        name = name.lower()
        info["name"] = name

        normalized = normalize_name(name, normalization_rules)
        by_name[name].append(info)
        by_normalized[normalized].append(info)

    return by_name, by_normalized


def _group_by_distance(places):
    """Group places into clusters where members are within 50km of the first."""
    groups = []
    for place in places:
        added = False
        for group in groups:
            if place["lat"] and group[0]["lat"]:
                dist = haversine(
                    place["lat"], place["lon"], group[0]["lat"], group[0]["lon"]
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
    return [g for g in groups if len(g) > 1]


def _find_semantic_duplicates(by_normalized, large_region_types):
    """Find semantic duplicates: same normalized name, close coords or large region."""
    semantic = {}
    for normalized, places in by_normalized.items():
        if len(places) <= 1:
            continue
        is_large = any(p.get("geography_type") in large_region_types for p in places)
        if is_large:
            semantic[f"{normalized} (semantic)"] = places
        else:
            for group in _group_by_distance(places):
                semantic[f"{normalized} (semantic)"] = group
    return semantic


def _find_fuzzy_duplicates(places_dir, normalization_rules, already_grouped):
    """Fuzzy matching pass for near-identical names not caught by normalization."""
    all_places = []
    for place_file in places_dir.glob("*.json"):
        if place_file.name == "index.json" or place_file.name in already_grouped:
            continue
        with open(place_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        name = data.get("current_name", data.get("name", ""))
        if not name:
            continue
        norm = normalize_name(name, normalization_rules)
        all_places.append((norm, place_file.name, name, data))

    fuzzy = {}
    for i, (norm1, file1, name1, data1) in enumerate(all_places):
        for norm2, file2, name2, data2 in all_places[i + 1 :]:
            if norm1 == norm2:
                continue
            ratio = SequenceMatcher(None, norm1, norm2).ratio()
            if ratio >= 0.92 and min(len(norm1), len(norm2)) >= 8:
                key = f"{name1} / {name2} (fuzzy {ratio:.0%})"
                fuzzy[key] = [
                    _place_info_from_data(data1, file1),
                    _place_info_from_data(data2, file2),
                ]
    return fuzzy


def _print_report(duplicates):
    """Print the duplicate places report."""
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


def main():
    config = load_config()
    normalization_rules = config.get("normalization_rules", [])
    large_region_types = config.get("large_region_types", [])
    places_dir = Path("output/places")

    by_name, by_normalized = _load_places(places_dir, normalization_rules)

    exact = {n: ps for n, ps in by_name.items() if len(ps) > 1}
    semantic = _find_semantic_duplicates(by_normalized, large_region_types)
    duplicates = {**exact, **semantic}

    already_grouped = {p["file"] for ps in duplicates.values() for p in ps}
    fuzzy = _find_fuzzy_duplicates(places_dir, normalization_rules, already_grouped)
    duplicates.update(fuzzy)

    _print_report(duplicates)


if __name__ == "__main__":
    main()

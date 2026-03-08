#!/usr/bin/env python3
"""Fix missing map URLs in existing place files."""

import json
from pathlib import Path


def generate_map_urls(lat: float, lon: float) -> dict:
    """Generate map service URLs for coordinates."""
    return {
        "google_maps": f"https://www.google.com/maps?q={lat},{lon}",
        "openstreetmap": f"https://www.openstreetmap.org/?mlat={lat}&mlon={lon}&zoom=12"
    }


def fix_place_file(file_path: Path) -> bool:
    """Fix map URLs in a single place file."""
    with open(file_path, 'r', encoding='utf-8') as f:
        data = json.load(f)
    
    # Check if map_urls is missing or null
    if data.get("map_urls") is None and "coordinates" in data:
        coords = data["coordinates"]
        if coords.get("latitude") and coords.get("longitude"):
            data["map_urls"] = generate_map_urls(
                coords["latitude"],
                coords["longitude"]
            )
            
            # Write back
            with open(file_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            
            return True
    
    return False


def main():
    places_dir = Path("output/places")
    
    if not places_dir.exists():
        print(f"❌ Directory not found: {places_dir}")
        return
    
    place_files = list(places_dir.glob("*.json"))
    place_files = [f for f in place_files if f.name != "index.json"]
    
    print(f"Found {len(place_files)} place files")
    
    fixed = 0
    for place_file in place_files:
        if fix_place_file(place_file):
            print(f"✓ Fixed: {place_file.name}")
            fixed += 1
    
    print(f"\n{'✓' if fixed > 0 else '→'} Fixed {fixed}/{len(place_files)} files")


if __name__ == "__main__":
    main()

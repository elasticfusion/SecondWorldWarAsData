"""Deduplicate and normalize biographical data in existing people files."""

import json
from pathlib import Path

from src.extraction.people import (
    _deduplicate_awards,
    _deduplicate_ranks,
    _deduplicate_units,
)


def deduplicate_person_data(person_file: Path) -> bool:
    """Deduplicate ranks, units, and awards in a person file. Returns True if modified."""
    with open(person_file, encoding="utf-8") as f:
        person_data = json.load(f)

    bio_profile = person_data.get("biographical_profile", {})
    modified = False

    # Deduplicate ranks
    ranks = bio_profile.get("ranks", [])
    if ranks:
        deduplicated = _deduplicate_ranks(ranks)
        if deduplicated != ranks:
            bio_profile["ranks"] = deduplicated
            modified = True

    # Deduplicate units
    units = bio_profile.get("units_served", [])
    if units:
        deduplicated = _deduplicate_units(units)
        if deduplicated != units:
            bio_profile["units_served"] = deduplicated
            modified = True

    # Deduplicate awards
    awards = bio_profile.get("military_awards", [])
    if awards:
        deduplicated = _deduplicate_awards(awards)
        if deduplicated != awards:
            bio_profile["military_awards"] = deduplicated
            modified = True

    if modified:
        person_data["biographical_profile"] = bio_profile
        with open(person_file, "w", encoding="utf-8") as f:
            json.dump(person_data, f, indent=2, ensure_ascii=False)

    return modified


def main():
    """Deduplicate ranks and units in all people files."""
    people_dir = Path("output/people")
    modified_count = 0

    for person_file in people_dir.glob("*.json"):
        if person_file.name in [
            "index.json",
            "duplicate_report.json",
            "not_duplicates.json",
        ]:
            continue

        if deduplicate_person_data(person_file):
            print(f"✅ {person_file.name}")
            modified_count += 1

    print(f"\nModified {modified_count} files")


if __name__ == "__main__":
    main()

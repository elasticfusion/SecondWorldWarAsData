#!/usr/bin/env python3
"""
Migrate existing people JSON files to include new biographical fields.

Adds: ranks, units_served, education, family, aliases, biography_sources
"""

import json
import logging
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)


def migrate_person_file(file_path: Path) -> bool:
    """Add new biographical fields to a person file."""
    try:
        with open(file_path, encoding="utf-8") as f:
            data = json.load(f)

        # Check if biographical_profile exists
        if "biographical_profile" not in data:
            data["biographical_profile"] = {}

        profile = data["biographical_profile"]

        # Add new fields if they don't exist
        new_fields = {
            "ranks": [],
            "units_served": [],
            "education": [],
            "family": {},
            "aliases": [],
            "biography_sources": [],
        }

        modified = False
        for field, default_value in new_fields.items():
            if field not in profile:
                profile[field] = default_value
                modified = True

        # Ensure military_awards exists
        if "military_awards" not in profile:
            profile["military_awards"] = []
            modified = True

        if modified:
            # Write back
            with open(file_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            return True

        return False

    except Exception as e:
        logger.error(f"Error migrating {file_path.name}: {e}")
        return False


def main():
    """Migrate all people files."""
    people_dir = Path("output/people")

    if not people_dir.exists():
        logger.error(f"Directory not found: {people_dir}")
        return

    files = list(people_dir.glob("*.json"))
    logger.info(f"Found {len(files)} people files")
    logger.info("=" * 60)

    migrated = 0
    skipped = 0
    errors = 0

    for file_path in files:
        result = migrate_person_file(file_path)
        if result:
            migrated += 1
            logger.info(f"✅ Migrated: {file_path.name}")
        elif result is False:
            errors += 1
        else:
            skipped += 1

    logger.info("=" * 60)
    logger.info(f"Migration complete:")
    logger.info(f"  Migrated: {migrated}")
    logger.info(f"  Skipped:  {skipped}")
    logger.info(f"  Errors:   {errors}")


if __name__ == "__main__":
    main()

"""Example migrations for schema evolution.

This file demonstrates how to register migrations between schema versions.
"""

from src.utils.schema_evolution import register_migration


# Example: People schema migration from 1.0 to 1.1
@register_migration("people", "1.0", "1.1")
def migrate_people_1_0_to_1_1(data):
    """
    Migrate people schema from 1.0 to 1.1.

    Changes:
    - Add 'verified' field (default: False)
    - Rename 'biography' to 'bio'
    """
    migrated = data.copy()

    # Add new field
    if "verified" not in migrated:
        migrated["verified"] = False

    # Rename field
    if "biography" in migrated:
        migrated["bio"] = migrated.pop("biography")

    return migrated


# Example: Event schema migration from 1.0 to 1.1
@register_migration("event", "1.0", "1.1")
def migrate_event_1_0_to_1_1(data):
    """
    Migrate event schema from 1.0 to 1.1.

    Changes:
    - Add 'tags' field (default: empty array)
    """
    migrated = data.copy()

    if "Event" in migrated and "tags" not in migrated["Event"]:
        migrated["Event"]["tags"] = []

    return migrated


# Example: Equipment schema migration from 1.0 to 2.0
@register_migration("equipment", "1.0", "2.0")
def migrate_equipment_1_0_to_2_0(data):
    """
    Migrate equipment schema from 1.0 to 2.0.

    Changes:
    - Restructure specifications into nested object
    - Add 'category' field
    """
    migrated = data.copy()

    # Add category
    if "category" not in migrated:
        migrated["category"] = "unknown"

    # Restructure specifications
    if "specifications" in migrated and isinstance(migrated["specifications"], list):
        migrated["specifications"] = {
            "items": migrated["specifications"],
            "last_updated": None,
        }

    return migrated

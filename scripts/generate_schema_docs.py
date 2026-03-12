#!/usr/bin/env python3
"""Generate markdown documentation from JSON schemas."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.json_schemas import (
    CASUALTIES_SCHEMA,
    DATE_SCHEMA,
    EQUIPMENT_SCHEMA,
    EVENT_SCHEMA,
    MAP_SCHEMA,
    PEOPLE_GROUPS_SCHEMA,
    PEOPLE_SCHEMA,
    PLACE_SCHEMA,
    SCHEMA_VERSION,
    SUPPLEMENTAL_SCHEMA,
)


def generate_field_docs(properties, required, indent=0):
    """Generate markdown for schema fields."""
    lines = []
    prefix = "  " * indent

    for field, spec in properties.items():
        req = "**required**" if field in required else "optional"
        field_type = spec.get("type", "object")
        pattern = spec.get("pattern", "")

        lines.append(f"{prefix}- `{field}` ({field_type}, {req})")

        if pattern:
            lines.append(f"{prefix}  - Pattern: `{pattern}`")

        if field_type == "array" and "items" in spec:
            items = spec["items"]
            if "properties" in items:
                lines.append(f"{prefix}  - Array items:")
                item_required = items.get("required", [])
                lines.extend(
                    generate_field_docs(items["properties"], item_required, indent + 2)
                )

        if field_type == "object" and "properties" in spec:
            lines.append(f"{prefix}  - Object properties:")
            obj_required = spec.get("required", [])
            lines.extend(
                generate_field_docs(spec["properties"], obj_required, indent + 2)
            )

    return lines


def document_schema(name, schema):
    """Generate markdown documentation for a schema."""
    lines = [
        f"## {name}",
        "",
        f"**Version:** {schema.get('version', 'N/A')}",
        "",
        "### Fields",
        "",
    ]

    required = schema.get("required", [])
    properties = schema.get("properties", {})

    lines.extend(generate_field_docs(properties, required))
    lines.append("")

    return "\n".join(lines)


def main():
    """Generate schema documentation."""
    schemas = {
        "Event Schema": EVENT_SCHEMA,
        "Date Schema": DATE_SCHEMA,
        "Place Schema": PLACE_SCHEMA,
        "Supplemental Schema": SUPPLEMENTAL_SCHEMA,
        "People Schema": PEOPLE_SCHEMA,
        "People Groups Schema": PEOPLE_GROUPS_SCHEMA,
        "Equipment Schema": EQUIPMENT_SCHEMA,
        "Map Schema": MAP_SCHEMA,
        "Casualties Schema": CASUALTIES_SCHEMA,
    }

    output = [
        "# JSON Schema Documentation",
        "",
        f"**Schema Version:** {SCHEMA_VERSION}",
        "",
        "This document describes all JSON schemas used in the WWII data extraction pipeline.",
        "",
    ]

    for name, schema in schemas.items():
        output.append(document_schema(name, schema))

    doc_path = Path(__file__).parent.parent / "docs" / "current" / "SCHEMA_REFERENCE.md"
    doc_path.write_text("\n".join(output), encoding="utf-8")
    print(f"✓ Generated schema documentation: {doc_path}")


if __name__ == "__main__":
    main()

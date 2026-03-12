#!/usr/bin/env python3
"""Generate TypedDict stubs from JSON schemas."""

import sys
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

# pylint: disable=wrong-import-position,import-error
from src.json_schemas import (
    CASUALTIES_SCHEMA,
    DATE_SCHEMA,
    EQUIPMENT_SCHEMA,
    EVENT_SCHEMA,
    MAP_SCHEMA,
    PEOPLE_GROUPS_SCHEMA,
    PEOPLE_SCHEMA,
    PLACE_SCHEMA,
    SUPPLEMENTAL_SCHEMA,
)
from src.utils.type_stub_generator import generate_all_stubs


def main():
    """Generate type stubs."""
    schemas = {
        "event": EVENT_SCHEMA,
        "date": DATE_SCHEMA,
        "place": PLACE_SCHEMA,
        "supplemental": SUPPLEMENTAL_SCHEMA,
        "people": PEOPLE_SCHEMA,
        "people_groups": PEOPLE_GROUPS_SCHEMA,
        "equipment": EQUIPMENT_SCHEMA,
        "map": MAP_SCHEMA,
        "casualties": CASUALTIES_SCHEMA,
    }

    stubs = generate_all_stubs(schemas)

    output_path = Path(__file__).parent.parent / "src" / "types.py"
    output_path.write_text(stubs)

    print(f"✓ Generated type stubs: {output_path}")
    print(f"  Classes: {len(schemas)}")


if __name__ == "__main__":
    main()

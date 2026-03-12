#!/usr/bin/env python3
"""Validate data files without writing (dry-run mode)."""

import argparse
import sys
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

# pylint: disable=wrong-import-position,import-error
from src.utils.json_validator import validate_directory
from src.utils.schema_registry import get_registry


def main():
    """Run validation in dry-run mode."""
    parser = argparse.ArgumentParser(description="Validate JSON files without writing")
    parser.add_argument("directory", type=Path, help="Directory to validate")
    parser.add_argument(
        "--schema",
        required=True,
        choices=get_registry().list_schemas(),
        help="Schema to validate against",
    )
    parser.add_argument(
        "--pattern", default="*.json", help="File pattern (default: *.json)"
    )

    args = parser.parse_args()

    if not args.directory.exists():
        print(f"Error: Directory not found: {args.directory}")
        sys.exit(1)

    schema = get_registry().get_schema(args.schema)
    if not schema:
        print(f"Error: Schema not found: {args.schema}")
        sys.exit(1)

    print(f"Validating {args.directory} against {args.schema} schema...")
    results = validate_directory(args.directory, schema, args.pattern)

    print("\nResults:")
    print(f"  Total files: {results['total']}")
    print(f"  Valid: {results['valid']}")
    print(f"  Invalid: {results['invalid']}")

    if results["errors"]:
        print("\nErrors:")
        for error in results["errors"][:10]:  # Show first 10
            print(f"  {error['file']}: {error['error']}")
        if len(results["errors"]) > 10:
            print(f"  ... and {len(results['errors']) - 10} more")

    sys.exit(0 if results["invalid"] == 0 else 1)


if __name__ == "__main__":
    main()

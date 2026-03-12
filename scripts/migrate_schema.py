#!/usr/bin/env python3
"""Schema migration tool."""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.utils.schema_evolution import (
    generate_migration_report,
    migrate_file,
    scan_versions,
)


def main():
    """Run schema migrations."""
    parser = argparse.ArgumentParser(description="Schema migration tool")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # Scan command
    scan_parser = subparsers.add_parser("scan", help="Scan directory for versions")
    scan_parser.add_argument("directory", type=Path, help="Directory to scan")
    scan_parser.add_argument("--pattern", default="*.json", help="File pattern")

    # Report command
    report_parser = subparsers.add_parser("report", help="Generate migration report")
    report_parser.add_argument("directory", type=Path, help="Directory to analyze")
    report_parser.add_argument("--schema", required=True, help="Schema name")

    # Migrate command
    migrate_parser = subparsers.add_parser("migrate", help="Migrate files")
    migrate_parser.add_argument("directory", type=Path, help="Directory to migrate")
    migrate_parser.add_argument("--schema", required=True, help="Schema name")
    migrate_parser.add_argument("--to-version", required=True, help="Target version")
    migrate_parser.add_argument(
        "--no-backup", action="store_true", help="Skip backup creation"
    )
    migrate_parser.add_argument("--pattern", default="*.json", help="File pattern")

    args = parser.parse_args()

    if not args.directory.exists():
        print(f"Error: Directory not found: {args.directory}")
        sys.exit(1)

    if args.command == "scan":
        versions = scan_versions(args.directory, args.pattern)
        print(f"Version distribution in {args.directory}:")
        for version, count in sorted(versions.items()):
            print(f"  {version}: {count} files")

    elif args.command == "report":
        report = generate_migration_report(args.directory, args.schema)
        print(report)

    elif args.command == "migrate":
        migrated_count = 0
        error_count = 0

        for filepath in args.directory.glob(args.pattern):
            if not filepath.is_file():
                continue

            try:
                if migrate_file(
                    filepath, args.schema, args.to_version, not args.no_backup
                ):
                    migrated_count += 1
            except Exception as e:  # pylint: disable=broad-except
                print(f"Error migrating {filepath.name}: {e}")
                error_count += 1

        print(f"\nMigration complete:")
        print(f"  Migrated: {migrated_count}")
        print(f"  Errors: {error_count}")

        if error_count > 0:
            sys.exit(1)


if __name__ == "__main__":
    main()

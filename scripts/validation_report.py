#!/usr/bin/env python3
"""Generate validation reports."""

import argparse
import sys
from pathlib import Path

from src.utils.json_validator import validate_directory
from src.utils.schema_registry import get_registry
from src.utils.validation_reports import (
    generate_trend_report,
    generate_validation_report,
    save_validation_history,
)


def main():
    """Generate validation reports."""
    parser = argparse.ArgumentParser(description="Generate validation reports")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # Validate and report
    validate_parser = subparsers.add_parser(
        "validate", help="Validate and generate report"
    )
    validate_parser.add_argument("directory", type=Path, help="Directory to validate")
    validate_parser.add_argument(
        "--schema",
        required=True,
        choices=get_registry().list_schemas(),
        help="Schema to validate against",
    )
    validate_parser.add_argument(
        "--format",
        choices=["json", "html"],
        default="html",
        help="Report format (default: html)",
    )
    validate_parser.add_argument(
        "--output", type=Path, help="Output file (default: stdout)"
    )
    validate_parser.add_argument(
        "--save-history",
        action="store_true",
        help="Save to validation history",
    )
    validate_parser.add_argument(
        "--history-file",
        type=Path,
        default=Path("validation_history.json"),
        help="History file path",
    )

    # Trends report
    trends_parser = subparsers.add_parser("trends", help="Generate trends report")
    trends_parser.add_argument(
        "--schema", default="all", help="Schema to filter by (default: all)"
    )
    trends_parser.add_argument(
        "--history-file",
        type=Path,
        default=Path("validation_history.json"),
        help="History file path",
    )
    trends_parser.add_argument(
        "--output", type=Path, help="Output file (default: stdout)"
    )

    args = parser.parse_args()

    if args.command == "validate":
        if not args.directory.exists():
            print(f"Error: Directory not found: {args.directory}")
            sys.exit(1)

        schema = get_registry().get_schema(args.schema)
        if not schema:
            print(f"Error: Schema not found: {args.schema}")
            sys.exit(1)

        # Validate
        print(f"Validating {args.directory}...")
        results = validate_directory(args.directory, schema)

        # Generate report
        report = generate_validation_report(
            results, args.schema, args.directory, args.format
        )

        # Save history
        if args.save_history:
            save_validation_history(
                results, args.schema, args.directory, args.history_file
            )
            print(f"Saved to history: {args.history_file}")

        # Output
        if args.output:
            args.output.write_text(report)
            print(f"Report saved: {args.output}")
        else:
            print(report)

        # Exit code
        sys.exit(0 if results["invalid"] == 0 else 1)

    elif args.command == "trends":
        report = generate_trend_report(args.history_file, args.schema)

        if args.output:
            args.output.write_text(report)
            print(f"Trends report saved: {args.output}")
        else:
            print(report)


if __name__ == "__main__":
    main()

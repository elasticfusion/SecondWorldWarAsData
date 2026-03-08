#!/usr/bin/env python3
"""Validate URLs in supplemental material files."""

import argparse
import logging
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.extraction.validate_supplemental_urls import validate_all_supplemental, validate_supplemental_file


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Validate URLs in supplemental material files"
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("output"),
        help="Output directory (default: output)",
    )
    parser.add_argument(
        "--file",
        type=Path,
        help="Validate a single file instead of all files",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Don't save changes, just report status",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=10.0,
        help="Request timeout in seconds (default: 10.0)",
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Verbose output",
    )

    args = parser.parse_args()

    # Setup logging
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s: %(message)s",
    )

    if args.file:
        # Validate single file
        if not args.file.exists():
            logging.error("File not found: %s", args.file)
            sys.exit(1)
        
        stats = validate_supplemental_file(args.file, save=not args.dry_run)
        
        print("\nValidation Results:")
        print(f"  Validated: {stats.get('validated', 0)}")
        print(f"  Partial: {stats.get('partial', 0)}")
        print(f"  Broken: {stats.get('broken', 0)}")
        print(f"  Timeout: {stats.get('timeout', 0)}")
        print(f"  No URLs: {stats.get('no_urls', 0)}")
        
    else:
        # Validate all files
        if not args.output_dir.exists():
            logging.error("Output directory not found: %s", args.output_dir)
            sys.exit(1)
        
        validate_all_supplemental(args.output_dir, save=not args.dry_run)
    
    if args.dry_run:
        print("\n(Dry run - no changes saved)")


if __name__ == "__main__":
    main()

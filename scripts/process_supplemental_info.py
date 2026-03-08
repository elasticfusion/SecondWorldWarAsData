#!/usr/bin/env python3
"""Process supplemental information through entity extraction pipeline."""

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.extraction.supplemental_info_pipeline import process_supplemental_information
from src.grok_client import GrokClient
from src.utils.config import load_config


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Extract entities from supplemental information"
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
        help="Process a single file instead of all files",
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

    # Load config
    config = load_config()
    
    # Initialize Grok client
    cache_dir = Path(config.get("paths", {}).get("api_cache", "cache/api"))
    cache_dir.mkdir(parents=True, exist_ok=True)
    grok_client = GrokClient(cache_dir)

    if args.file:
        # Process single file
        if not args.file.exists():
            logging.error("File not found: %s", args.file)
            sys.exit(1)
        
        count = process_supplemental_information(
            args.file, grok_client, args.output_dir, config
        )
        
        print(f"\nProcessed {count} supplemental information materials")
        
    else:
        # Process all files
        if not args.output_dir.exists():
            logging.error("Output directory not found: %s", args.output_dir)
            sys.exit(1)
        
        total = 0
        files_processed = 0
        
        for file_path in args.output_dir.rglob("*-endnotes.json"):
            count = process_supplemental_information(
                file_path, grok_client, args.output_dir, config
            )
            total += count
            files_processed += 1
        
        for file_path in args.output_dir.rglob("*-footnotes.json"):
            count = process_supplemental_information(
                file_path, grok_client, args.output_dir, config
            )
            total += count
            files_processed += 1
        
        print(f"\nProcessed {total} supplemental information materials from {files_processed} files")


if __name__ == "__main__":
    main()

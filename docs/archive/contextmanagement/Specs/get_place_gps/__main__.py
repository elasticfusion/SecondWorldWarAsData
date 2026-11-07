#!/usr/bin/env python3
"""
get_place_gps.py

Processes chapter/section review folders to extract place mentions,
geocode them via Grok API (with cache), and save results.
"""

import argparse
import logging
import sys
from pathlib import Path

from .logging_setup import setup_logging
from .paths import validate_prompts_root, BOOK_ROOT, PROJECT_ROOT
from .processing import process_single, find_unprocessed_folders
from .batch_validator import validate_all_event_files

logger = logging.getLogger(__name__)

def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Geocode places from historical sub-events"
    )
    parser.add_argument("chapter", type=int, nargs="?", default=None,
                        help="Chapter number")
    parser.add_argument("section", type=str, nargs="?", default="",
                        help="Section identifier")
    parser.add_argument("--batch", action="store_true",
                        help="Process all unprocessed folders")
    parser.add_argument("--dry-run", action="store_true",
                        help="Simulate without writing files or updating cache")
    parser.add_argument("--force-refresh", action="store_true",
                        help="Ignore cache and force fresh API calls")
    parser.add_argument("--log-dir", type=Path, default=None,
                        help="Directory for log files")
    parser.add_argument("--cache-dir", type=Path, default=None,
                        help="Cache directory")
    parser.add_argument(
        "--log-level",
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        default="INFO",
        help="Set logging level (default: INFO)"
    )
    parser.add_argument(
        "--show-prompts",
        action="store_true",
        help="Log the full prompt text submitted to Grok for each place query",
    )
    parser.add_argument(
        "--validate-all",
        action="store_true",
        help="Validate all event JSON files in directory tree and exit",
    )

    args = parser.parse_args()

    setup_logging(args.log_dir, log_level=args.log_level)
    validate_prompts_root()

    # Set default cache directory if not provided
    if not args.cache_dir:
        args.cache_dir = PROJECT_ROOT / "cache" / "place" / "gps"

    try:
        cache_path = Path(args.cache_dir).resolve()
        cache_path.mkdir(parents=True, exist_ok=True)
        logger.info("Cache directory ensured: %s", cache_path)
    except (OSError, ValueError) as exc:
        logger.error("Failed to create cache directory %s: %s", args.cache_dir, exc)
        sys.exit(1)

    try:
        if args.validate_all:
            logger.info("Validating all event JSON files...")
            valid, failed = validate_all_event_files(BOOK_ROOT)
            logger.info("Validation complete: %d valid, %d failed", len(valid),
                       len(failed))
            if valid:
                logger.info("Valid files (%d):", len(valid))
                for f in valid:
                    logger.info("  ✓ %s", f.relative_to(BOOK_ROOT))
            if failed:
                logger.error("Failed files (%d):", len(failed))
                for item in failed:
                    try:
                        f, err = item
                        rel_path = f.relative_to(BOOK_ROOT)
                    except (ValueError, TypeError) as e:
                        logger.error("Error processing failed file entry: %s", e)
                        continue
                    logger.error("  X %s", rel_path)
                    logger.error("    %s", err)
                sys.exit(1)
            sys.exit(0)
        elif args.batch:
            logger.info("Batch mode – scanning folders")
            todo = find_unprocessed_folders()
            logger.info("Found %d unprocessed folders", len(todo))
            for ch, sec in todo:
                process_single(
                    ch, sec, args.dry_run, args.force_refresh,
                    args.cache_dir, args.show_prompts
                )
        else:
            if args.chapter is None:
                parser.error("Chapter required in non-batch mode")
            process_single(
                args.chapter, args.section, args.dry_run, args.force_refresh,
                args.cache_dir, args.show_prompts
            )
    except (FileNotFoundError, OSError, ValueError) as exc:
        logger.error("Fatal error: %s", exc, exc_info=True)
        sys.exit(1)

if __name__ == "__main__":
    main()

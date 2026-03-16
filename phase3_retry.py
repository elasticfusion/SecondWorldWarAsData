#!/usr/bin/env python3
"""
Phase 3 with automatic retry for transient errors.

Runs phase3_enrich_data.py multiple times until all enrichment is complete
or maximum attempts reached.
"""

import argparse
import json
import logging
import subprocess
import sys
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


def count_unenriched_people(people_dir: Path) -> int:
    """Count how many people files lack enrichment data."""
    if not people_dir.exists():
        return 0
    
    people_files = [
        f for f in people_dir.glob("*.json")
        if f.name not in ["index.json", "duplicate_report.json", "not_duplicates.json"]
    ]
    
    unenriched = 0
    for person_file in people_files:
        try:
            with open(person_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            
            # Check if enrichment data exists
            if not data.get("enrichment_data"):
                unenriched += 1
        except Exception:
            continue
    
    return unenriched


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Run Phase 3 with automatic retry for transient errors"
    )
    parser.add_argument(
        "--max-attempts",
        type=int,
        default=3,
        help="Maximum number of attempts (default: 3)",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("output"),
        help="Output directory (default: output)",
    )
    parser.add_argument(
        "--cache-dir",
        type=Path,
        default=Path("cache/grok_cache"),
        help="Cache directory (default: cache/grok_cache)",
    )
    parser.add_argument(
        "--max-items",
        type=int,
        help="Maximum items per entity type to enrich",
    )
    parser.add_argument(
        "--no-references",
        action="store_true",
        help="Don't follow references",
    )
    parser.add_argument(
        "--people-only",
        action="store_true",
        help="Only enrich people",
    )
    parser.add_argument(
        "--log-level",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        default=None,
        help="Set logging level (passed to phase3_enrich_data.py)",
    )
    args = parser.parse_args()

    base_dir = Path(__file__).parent
    people_dir = args.output_dir / "people"
    
    logger.info("=" * 70)
    logger.info("Phase 3 with Automatic Retry")
    logger.info("=" * 70)
    logger.info(f"Maximum attempts: {args.max_attempts}")
    logger.info("")

    for attempt in range(1, args.max_attempts + 1):
        logger.info(f"\n{'=' * 70}")
        logger.info(f"Attempt {attempt}/{args.max_attempts}")
        logger.info(f"{'=' * 70}\n")

        # Build command
        cmd = [sys.executable, "phase3_enrich_data.py"]
        
        if args.output_dir != Path("output"):
            cmd.extend(["--output-dir", str(args.output_dir)])
        if args.cache_dir != Path("cache/grok_cache"):
            cmd.extend(["--cache-dir", str(args.cache_dir)])
        if args.max_items:
            cmd.extend(["--max-items", str(args.max_items)])
        if args.no_references:
            cmd.append("--no-references")
        if args.people_only:
            cmd.append("--people-only")
        if args.log_level:
            cmd.extend(["--log-level", args.log_level])

        # Run phase3
        result = subprocess.run(cmd, cwd=base_dir)

        if result.returncode != 0:
            if result.returncode < 0:
                import signal
                sig = -result.returncode
                sig_name = signal.Signals(sig).name if sig in signal.valid_signals() else str(sig)
                logger.error("Attempt %d killed by signal %s", attempt, sig_name)
            else:
                logger.error("Attempt %d failed with exit code %d", attempt, result.returncode)
            if attempt < args.max_attempts:
                logger.info("Retrying...")
                continue
            else:
                logger.error("Maximum attempts reached, giving up")
                return 1
        unenriched = count_unenriched_people(people_dir)
        
        if unenriched == 0:
            logger.info(f"\n{'=' * 70}")
            logger.info(f"✓ Success! All people enriched on attempt {attempt}")
            logger.info(f"{'=' * 70}")
            return 0
        else:
            logger.warning(f"\n⚠ {unenriched} people still lack enrichment data")
            if attempt < args.max_attempts:
                logger.info("Retrying...")
            else:
                logger.error(f"✗ Maximum attempts reached, {unenriched} people still unenriched")
                return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())

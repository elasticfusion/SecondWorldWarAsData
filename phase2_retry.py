#!/usr/bin/env python3
"""
Phase 2 with automatic retry for transient errors.

Runs phase2_extract.py multiple times until all files are successfully processed
or maximum attempts reached.
"""

import argparse
import logging
import subprocess
import sys
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


def count_missing_files(output_root: Path) -> int:
    """Count how many parsed files are missing their event files."""
    parsed_files = list(output_root.rglob("*-parsed.json"))
    missing = 0

    for parsed_file in parsed_files:
        event_file = parsed_file.parent / parsed_file.name.replace(
            "-parsed.json", "-event.json"
        )
        if not event_file.exists():
            missing += 1

    return missing


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Run Phase 2 with automatic retry for transient errors"
    )
    parser.add_argument(
        "--max-attempts",
        type=int,
        default=3,
        help="Maximum number of attempts (default: 3)",
    )
    parser.add_argument(
        "--log-level",
        choices=["TRACE", "DEBUG", "INFO", "WARN", "ERROR", "FATAL"],
        default=None,
        help="Set logging level (passed to phase2_extract.py)",
    )
    args = parser.parse_args()

    base_dir = Path(__file__).parent
    output_root = base_dir / "output"

    logger.info("=" * 70)
    logger.info("Phase 2 with Automatic Retry")
    logger.info("=" * 70)
    logger.info(f"Maximum attempts: {args.max_attempts}")
    logger.info("")

    for attempt in range(1, args.max_attempts + 1):
        logger.info(f"\n{'=' * 70}")
        logger.info(f"Attempt {attempt}/{args.max_attempts}")
        logger.info(f"{'=' * 70}\n")

        # Build command
        cmd = [sys.executable, "phase2_extract.py"]
        if args.log_level:
            cmd.extend(["--log-level", args.log_level])

        # Run phase2
        result = subprocess.run(cmd, cwd=base_dir)

        if result.returncode != 0:
            if result.returncode < 0:
                import signal

                sig = -result.returncode
                sig_name = (
                    signal.Signals(sig).name
                    if sig in signal.valid_signals()
                    else str(sig)
                )
                logger.error("Attempt %d killed by signal %s", attempt, sig_name)
            else:
                logger.error(
                    "Attempt %d failed with exit code %d", attempt, result.returncode
                )
            if attempt < args.max_attempts:
                logger.info("Retrying...")
                continue
            else:
                logger.error("Maximum attempts reached, giving up")
                return 1
        missing = count_missing_files(output_root)

        if missing == 0:
            logger.info(f"\n{'=' * 70}")
            logger.info(f"✓ Success! All files processed on attempt {attempt}")
            logger.info(f"{'=' * 70}")
            return 0
        else:
            logger.warning(f"\n⚠ {missing} file(s) still missing event files")
            if attempt < args.max_attempts:
                logger.info("Retrying...")
            else:
                logger.error(
                    f"✗ Maximum attempts reached, {missing} file(s) still incomplete"
                )
                return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())

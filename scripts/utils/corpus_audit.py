#!/usr/bin/env python3
"""Corpus-level data-quality audit — READ-ONLY CLI (block b-3).

Thin wrapper over ``src/utils/corpus_audit.audit_corpus`` (which composes the live write-guard
schema check + the referential-integrity audit + a PK-uniqueness pass). Reports; never mutates.
Intended for a scheduled run; ``--fail-threshold`` lets a job alert when problems exceed N.
"""

import argparse
import json
import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger("corpus_audit")


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    ap = argparse.ArgumentParser(description="Corpus data-quality audit (read-only)")
    ap.add_argument("--output-dir", default="output")
    ap.add_argument(
        "--fail-threshold",
        type=int,
        default=-1,
        help="exit 1 if total problems >= N (-1 = never fail; N=0 fails even on a clean "
        "corpus — use 1 to fail on the first real problem)",
    )
    ap.add_argument("--json-out", default="", help="write the full report to this path")
    args = ap.parse_args()

    from src.utils.config import load_config
    from src.utils.corpus_audit import audit_corpus

    config = load_config()
    if os.environ.get("S3_BUCKET"):
        from src.utils.storage import S3Storage

        storage: Any = S3Storage(
            bucket=os.environ["S3_BUCKET"],
            prefix="output",
            region=config.get("aws", {}).get("region", "us-east-1"),
        )
    else:
        from src.utils.storage import LocalStorage

        storage = LocalStorage(base_dir=Path(args.output_dir))

    try:
        report = audit_corpus(storage)
    except (
        Exception
    ) as e:  # noqa: BLE001 - a backend/transient error is NOT a data-quality fail
        logger.error("AUDIT ERROR (could not complete the scan): %s", e)
        return 2  # distinct from exit 1 (problems found) so a scheduler can tell them apart
    sv = report["schema_validity"]
    ri = report["referential_integrity"]
    pk = report["pk_uniqueness"]
    logger.info("Corpus audit (read-only):")
    logger.info(
        "  schema-invalid records: %d / %d checked",
        sv["invalid_count"],
        sv["records_checked"],
    )
    logger.info("  dangling cross-references: %d", ri["total_dangling"])
    logger.info("  primary-key collisions: %d", pk["collision_count"])
    logger.info("  TOTAL problems: %d", report["total_problems"])

    if args.json_out:
        Path(args.json_out).write_text(json.dumps(report, indent=2), encoding="utf-8")
        logger.info("  report written to %s", args.json_out)

    if args.fail_threshold >= 0 and report["total_problems"] >= args.fail_threshold:
        logger.error(
            "FAIL: %d problems >= threshold %d",
            report["total_problems"],
            args.fail_threshold,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

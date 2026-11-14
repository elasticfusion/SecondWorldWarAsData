#!/usr/bin/env python3
"""Corpus referential-integrity audit — READ-ONLY CLI.

Thin wrapper over the CORE checker in `src/utils/referential_integrity.py` (single source of the
edge model + resolution logic — shared with the end-of-phase enforcement pass so they cannot drift).
Verifies every cross-reference ID resolves to an existing target and reports dangling counts per
edge — the class of corruption the per-write guard is structurally blind to.
"""

import argparse
import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger("referential_integrity_audit")


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    ap = argparse.ArgumentParser(
        description="Corpus referential-integrity audit (read-only)"
    )
    ap.add_argument("--output-dir", default="output")
    ap.add_argument(
        "--fail-threshold",
        type=int,
        default=-1,
        help="exit 1 if total dangling >= N (-1 = never fail)",
    )
    ap.add_argument("--json-out", default="", help="write the full report to this path")
    ap.add_argument(
        "--unsafe-include-ids",
        action="store_true",
        help="include up to 3 raw dangling ID values per edge in the report (OFF by default; "
        "raw IDs are record identifiers — do not sync the output to a PII-restricted sink)",
    )
    args = ap.parse_args()

    import os as _os

    from src.utils.config import load_config
    from src.utils.referential_integrity import audit

    config = load_config()
    if _os.environ.get("S3_BUCKET"):
        from src.utils.storage import S3Storage

        storage: Any = S3Storage(
            bucket=_os.environ["S3_BUCKET"],
            prefix="output",
            region=config.get("aws", {}).get("region", "us-east-1"),
        )
    else:
        from src.utils.backends import create_storage

        storage = create_storage(config, Path(args.output_dir))

    report = audit(storage, include_samples=args.unsafe_include_ids)
    logger.info("Referential-integrity audit — PK counts: %s", report["pk_counts"])
    logger.info("Edges (dangling / total):")
    for e in report["edges"]:
        flag = " WARN" if e["dangling"] else ""
        logger.info(
            "  %-45s %6d / %-6d (%.1f%%)%s",
            e["edge"],
            e["dangling"],
            e["total_refs"],
            e["rate"] * 100,
            flag,
        )
    logger.info("TOTAL dangling references: %d", report["total_dangling"])
    if args.json_out:
        Path(args.json_out).write_text(json.dumps(report, indent=2), encoding="utf-8")
    if args.fail_threshold >= 0 and report["total_dangling"] >= args.fail_threshold:
        logger.error(
            "FAIL: %d dangling refs >= threshold %d",
            report["total_dangling"],
            args.fail_threshold,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

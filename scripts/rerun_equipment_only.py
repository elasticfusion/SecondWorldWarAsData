#!/usr/bin/env python3
"""Equipment-ONLY re-run — fast targeted iteration on the equipment extractor.

Runs ONLY `extract_equipment_from_event` against the already-written `*-event.json` file(s) in the
output dir. Skips the entire Phase-2 Step-1 chain (events -> people -> bio-enrichment) that a normal
Phase-2 resume walks, so an equipment-only re-run takes MINUTES, not the full enrichment time.

Prereqs (all already true after a Phase-2 run): output/content/<book>/*-event.json exists and the
cross-ref indices (people/people_groups/places/dates) are present for linking. Delete
output/equipment/ first for a clean re-extract (the extractor rebuilds its index/trackers).

Usage:
    python scripts/rerun_equipment_only.py [--output-root output] [--book USA-E-XChannel-8]
Honors GROK_CACHE_DISABLED (fresh calls) like the normal pipeline.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

# repo root on path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.grok_client import GrokClient  # noqa: E402
from src.utils.config import load_config  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("rerun_equipment_only")


def _event_files(output_root: Path, book: str | None) -> list[Path]:
    """The *-event.json files the equipment extractor reads (optionally scoped to one book)."""
    content = output_root / "content"
    if not content.is_dir():
        return []
    globber = content.glob(f"{book}/*-event.json") if book else content.rglob("*-event.json")
    return sorted(globber)


def main() -> int:
    ap = argparse.ArgumentParser(description="Equipment-only re-run (skips events/people/enrichment)")
    ap.add_argument("--output-root", default="output", help="Output root (default: output)")
    ap.add_argument("--book", default=None, help="Scope to one book dir (default: all)")
    args = ap.parse_args()

    base_dir = Path(__file__).resolve().parent.parent
    config = load_config(base_dir / "config.yaml")
    output_root = Path(args.output_root)

    if not config.get("equipment", {}).get("enabled", False):
        logger.error("equipment.enabled is false in config.yaml — nothing to do")
        return 2

    events = _event_files(output_root, args.book)
    if not events:
        logger.error(
            "No *-event.json under %s/content%s — run Phase 2 first (equipment reads the event file)",
            output_root,
            f"/{args.book}" if args.book else "",
        )
        return 1

    # Reuse the EXACT per-event equipment path the pipeline uses (same _extract_equipment).
    from phase2_extract import _extract_equipment  # noqa: E402

    cache_dir = output_root.parent / "cache" / "api"
    cache_dir.mkdir(parents=True, exist_ok=True)
    grok_client = GrokClient(cache_dir, batch_mode=False)  # synchronous, honors GROK_CACHE_DISABLED

    logger.info("Equipment-only re-run over %d event file(s)", len(events))
    for ev in events:
        logger.info("Equipment extraction: %s", ev)
        _extract_equipment(ev, grok_client, output_root, config, logger)

    # Report persisted count (the thing that matters post-#663).
    eq_dir = output_root / "equipment"
    persisted = [
        p
        for p in eq_dir.glob("*.json")
        if p.name not in ("index.json", ".processed_events.json", "duplicate_report.json")
    ]
    logger.info("DONE — equipment records persisted: %d", len(persisted))
    for p in sorted(persisted):
        logger.info("  %s", p.name)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

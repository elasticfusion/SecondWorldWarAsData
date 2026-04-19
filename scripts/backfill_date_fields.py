#!/usr/bin/env python3
"""Backfill spec-level fields on existing date files."""

import json
import sys
from pathlib import Path

SPEC_DEFAULTS = {
    "date_end": None,
    "time_start": None,
    "time_end": None,
    "time_precision": None,
    "date_precision": None,
    "time_source": None,
    "original_text": "",
    "normalized_datetime": None,
}


_PRECISION_PREFIXES = (
    "early",
    "mid",
    "late",
    "spring",
    "summer",
    "fall",
    "autumn",
    "winter",
)


def _infer_precision(date_start: str) -> str:
    """Infer date_precision from date_start format."""
    for prefix in _PRECISION_PREFIXES:
        if date_start.startswith(prefix):
            return prefix
    return "exact"


def _backfill_one_file(data: dict) -> bool:
    """Apply backfill changes to one date record. Returns True if changed."""
    changed = False

    # Rename date → date_start
    if "date" in data and "date_start" not in data:
        data["date_start"] = data.pop("date")
        changed = True

    # Add missing spec fields with defaults
    for field, default in SPEC_DEFAULTS.items():
        if field not in data:
            data[field] = default
            changed = True

    # Infer date_precision from date_start format
    if not data.get("date_precision") and data.get("date_start"):
        data["date_precision"] = _infer_precision(data["date_start"])
        changed = True

    return changed


def main():
    dry_run = "--dry-run" in sys.argv
    dates_dir = Path("output/dates")
    updated = 0

    for date_file in sorted(dates_dir.glob("*.json")):
        if date_file.name == "index.json":
            continue

        with open(date_file, encoding="utf-8") as f:
            data = json.load(f)

        if _backfill_one_file(data):
            if dry_run:
                print(f"  Would update: {date_file.name}")
            else:
                with open(date_file, "w", encoding="utf-8") as f:
                    json.dump(data, f, indent=2, ensure_ascii=False)
            updated += 1

    print(f"{'Would update' if dry_run else 'Updated'} {updated} date files")


if __name__ == "__main__":
    main()

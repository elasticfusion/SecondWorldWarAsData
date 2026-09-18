#!/usr/bin/env python3
"""Extract specific division page ranges and merge into existing ETO OOB CSVs."""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

import fitz

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from extract_eto_order_of_battle import (  # noqa: E402
    ETOOrderOfBattleExtractor,
    apply_csv_search_replacements,
    expand_division_page_ranges,
    load_division_page_ranges,
    write_csv,
)

DEFAULT_ROOT = (
    PROJECT_ROOT
    / "contentrepository"
    / "European Thater of Operations - Order of Battle"
)
DEFAULT_CONFIG = (
    PROJECT_ROOT / "config" / "eto_oob_pdf_page_division_overrides.yaml"
)

OUTPUT_FILES = (
    "eto_oob_command_and_staff.csv",
    "eto_oob_statistics.csv",
    "eto_oob_campaigns.csv",
    "eto_oob_organic_units.csv",
    "eto_oob_attachments.csv",
    "eto_oob_detachments.csv",
    "eto_oob_higher_unit_assignments.csv",
    "eto_oob_command_posts.csv",
)


ATTR_BY_FILE = {
    "eto_oob_command_and_staff.csv": "command_staff",
    "eto_oob_statistics.csv": "statistics",
    "eto_oob_campaigns.csv": "campaigns",
    "eto_oob_organic_units.csv": "organic_units",
    "eto_oob_attachments.csv": "attachments",
    "eto_oob_detachments.csv": "detachments",
    "eto_oob_higher_unit_assignments.csv": "higher_units",
    "eto_oob_command_posts.csv": "command_posts",
}


def merge_extracted_rows(
    root: Path,
    extractor: ETOOrderOfBattleExtractor,
    pdf_pages: set[str],
    division: str,
) -> dict[str, dict[str, int]]:
    """Replace rows on target PDF pages with freshly extracted division data."""
    stats: dict[str, dict[str, int]] = {}

    for filename, attr in ATTR_BY_FILE.items():
        path = root / filename
        if not path.exists():
            continue
        with path.open(newline="", encoding="utf-8") as handle:
            reader = csv.DictReader(handle)
            fieldnames = reader.fieldnames or []
            existing = list(reader)
        kept = [
            row
            for row in existing
            if not (
                str(row.get("pdf_page", "")) in pdf_pages
                or row.get("division") == division
            )
        ]
        dropped = len(existing) - len(kept)

        new_rows = getattr(extractor, attr)
        added = [
            row
            for row in new_rows
            if str(row.get("pdf_page", "")) in pdf_pages
            and row.get("division") == division
        ]
        merged = kept + added
        sort_key = lambda row: (  # noqa: E731
            row.get("division", ""),
            int(row.get("pdf_page") or 0),
            int(row.get("source_line") or 0),
        )
        merged.sort(key=sort_key)
        write_csv(path, fieldnames, merged)
        stats[filename] = {"dropped": dropped, "added": len(added), "total": len(merged)}
    return stats


def extract_divisions(
    root: Path,
    config_path: Path,
    divisions: list[str],
) -> dict[str, dict]:
    ranges = load_division_page_ranges(config_path)
    if not ranges:
        raise SystemExit("No division_page_ranges found in config.")

    selected = divisions or list(ranges)
    unknown = [name for name in selected if name not in ranges]
    if unknown:
        raise SystemExit(f"Unknown division_page_ranges for: {', '.join(unknown)}")

    pdf_path = root / "ETO_Order_of_Battle.pdf"
    all_stats: dict[str, dict] = {}

    for division in selected:
        first, last = ranges[division]
        pdf_pages = {str(page) for page in range(first, last + 1)}

        extractor = ETOOrderOfBattleExtractor(pdf_path)
        extractor.extract(start_page=first, end_page=last)
        file_stats = merge_extracted_rows(root, extractor, pdf_pages, division)
        all_stats[division] = {
            "pdf_pages": f"{first}-{last}",
            "files": file_stats,
        }

    apply_csv_search_replacements(root)
    return all_stats


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument(
        "--division",
        action="append",
        dest="divisions",
        help="Canonical division name (default: all division_page_ranges)",
    )
    args = parser.parse_args()

    stats = extract_divisions(args.root, args.config, args.divisions or [])
    for division, info in stats.items():
        print(f"{division} (pdf {info['pdf_pages']}):")
        for filename, counts in sorted(info["files"].items()):
            print(
                f"  {filename}: +{counts['added']} -{counts['dropped']} "
                f"= {counts['total']} rows"
            )


if __name__ == "__main__":
    main()
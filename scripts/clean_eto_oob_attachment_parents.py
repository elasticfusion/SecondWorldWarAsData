#!/usr/bin/env python3
"""Assign parent_unit_name on ETO OOB attachment rows from Group/Gp hierarchy."""

from __future__ import annotations

import argparse
import csv
from pathlib import Path

from extract_eto_order_of_battle import apply_attachment_parents_to_rows

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ATTACHMENTS = (
    PROJECT_ROOT
    / "contentrepository/European Thater of Operations - Order of Battle/eto_oob_attachments.csv"
)


def _sort_key(row: dict[str, str]) -> tuple:
    return (
        row.get("division", ""),
        int(row.get("pdf_page") or 0),
        int(row.get("source_line") or 0),
    )


def clean_attachment_parents(path: Path) -> tuple[int, int]:
    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        fieldnames = reader.fieldnames or []
        rows = list(reader)

    ordered = sorted(rows, key=_sort_key)
    updated_rows = apply_attachment_parents_to_rows(ordered)
    parent_by_key = {
        (
            row.get("division", ""),
            row.get("pdf_page", ""),
            row.get("source_line", ""),
            row.get("unit_name", ""),
        ): row.get("parent_unit_name", "")
        for row in updated_rows
    }

    changed_rows = 0
    for row in rows:
        key = (
            row.get("division", ""),
            row.get("pdf_page", ""),
            row.get("source_line", ""),
            row.get("unit_name", ""),
        )
        new_parent = parent_by_key.get(key, "")
        if row.get("parent_unit_name", "") != new_parent:
            row["parent_unit_name"] = new_parent
            changed_rows += 1

    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    parent_count = sum(1 for row in rows if row.get("parent_unit_name"))
    return changed_rows, parent_count


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--path",
        type=Path,
        default=DEFAULT_ATTACHMENTS,
        help="Path to eto_oob_attachments.csv",
    )
    args = parser.parse_args()
    changed_rows, parent_count = clean_attachment_parents(args.path)
    print(
        f"{args.path.name}: updated {changed_rows} row(s); "
        f"{parent_count} row(s) now have parent_unit_name"
    )


if __name__ == "__main__":
    main()
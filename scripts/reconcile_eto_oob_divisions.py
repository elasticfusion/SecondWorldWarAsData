#!/usr/bin/env python3
"""Remap ETO OOB CSV division labels from PDF page headers and re-parse command staff."""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

import fitz

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from extract_eto_order_of_battle import (  # noqa: E402
    apply_csv_search_replacements,
    build_page_lines,
    detect_section,
    extract_source_page,
    load_pdf_page_division_overrides,
    parse_command_staff_page,
    should_parse_command_staff_page,
    walk_pdf_page_divisions,
)

DEFAULT_ROOT = (
    PROJECT_ROOT
    / "contentrepository"
    / "European Thater of Operations - Order of Battle"
)
DEFAULT_OVERRIDES = (
    PROJECT_ROOT / "config" / "eto_oob_pdf_page_division_overrides.yaml"
)

ETO_CSV_GLOB = "eto_oob_*.csv"


def remap_csv_divisions(
    root: Path,
    page_divisions: dict[str, str],
    *,
    dry_run: bool = False,
) -> dict[str, int]:
    stats = {"files": 0, "rows_updated": 0}
    for csv_path in sorted(root.glob(ETO_CSV_GLOB)):
        with csv_path.open(newline="", encoding="utf-8") as handle:
            reader = csv.DictReader(handle)
            fieldnames = reader.fieldnames or []
            rows = list(reader)
        if "division" not in fieldnames:
            continue

        updated = 0
        for row in rows:
            pdf_page = str(row.get("pdf_page", ""))
            target = page_divisions.get(pdf_page)
            if target and row.get("division") != target:
                row["division"] = target
                updated += 1

        stats["files"] += 1
        stats["rows_updated"] += updated
        if updated and not dry_run:
            with csv_path.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(
                    handle, fieldnames=fieldnames, lineterminator="\n"
                )
                writer.writeheader()
                writer.writerows(rows)

    return stats


def reconcile_command_and_staff(
    root: Path,
    page_divisions: dict[str, str],
    *,
    dry_run: bool = False,
) -> dict[str, int]:
    csv_path = root / "eto_oob_command_and_staff.csv"
    pdf_path = root / "ETO_Order_of_Battle.pdf"

    with csv_path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        fieldnames = reader.fieldnames or []
        all_rows = list(reader)

    doc = fitz.open(pdf_path)
    current_section = ""
    pages_to_reparse: dict[str, str] = {}
    pages_evaluated: set[str] = set()

    for page_index in range(20, 570):
        if page_index >= doc.page_count:
            break
        fitz_page = doc[page_index]
        page_text = fitz_page.get_text("text")
        if "ORGANIC COMPOSITION" in page_text.upper() and "DIVISIONS" in page_text.upper():
            break

        pdf_page = str(page_index + 1)
        division = page_divisions.get(pdf_page)
        if not division:
            continue

        page_lines = build_page_lines(page_text)
        lines = [text for _, text in page_lines]

        page_section = current_section
        for header_line in lines:
            section = detect_section(header_line)
            if section:
                page_section = section
                current_section = section
                break

        pages_evaluated.add(pdf_page)
        if not should_parse_command_staff_page(page_lines, page_section=page_section):
            continue
        pages_to_reparse[pdf_page] = division

    new_rows_by_page: dict[str, list[dict]] = {}
    for pdf_page, division in sorted(pages_to_reparse.items(), key=lambda item: int(item[0])):
        fitz_page = doc[int(pdf_page) - 1]
        page_text = fitz_page.get_text("text")
        page_lines = build_page_lines(page_text)
        parsed = parse_command_staff_page(
            page_lines,
            division,
            extract_source_page(page_text),
            int(pdf_page),
        )
        new_rows_by_page[pdf_page] = parsed

    doc.close()

    reparse_pages = set(pages_to_reparse)
    kept = [
        row
        for row in all_rows
        if str(row.get("pdf_page", "")) not in reparse_pages
        and str(row.get("pdf_page", "")) not in pages_evaluated
    ]
    dropped = len(all_rows) - len(kept)
    reparsed = 0
    for rows in new_rows_by_page.values():
        kept.extend(rows)
        reparsed += len(rows)

    sort_key = lambda row: (  # noqa: E731
        row["division"],
        int(row.get("pdf_page") or 0),
        int(row.get("source_line") or 0),
    )
    kept.sort(key=sort_key)

    stats = {
        "pages_reparsed": len(pages_to_reparse),
        "rows_dropped": dropped,
        "rows_reparsed": reparsed,
        "rows_after": len(kept),
    }

    if not dry_run:
        with csv_path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames, lineterminator="\n")
            writer.writeheader()
            writer.writerows(kept)

    return stats


def reconcile(
    root: Path,
    overrides_path: Path,
    *,
    dry_run: bool = False,
) -> dict[str, int]:
    pdf_path = root / "ETO_Order_of_Battle.pdf"
    overrides = load_pdf_page_division_overrides(overrides_path)

    doc = fitz.open(pdf_path)
    page_divisions = walk_pdf_page_divisions(doc, overrides=overrides)
    doc.close()

    remap_stats = remap_csv_divisions(root, page_divisions, dry_run=dry_run)
    cmd_stats = reconcile_command_and_staff(root, page_divisions, dry_run=dry_run)

    if not dry_run:
        apply_csv_search_replacements(root)

    return {
        "pages_mapped": len(page_divisions),
        **remap_stats,
        **{f"command_staff_{key}": value for key, value in cmd_stats.items()},
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--overrides", type=Path, default=DEFAULT_OVERRIDES)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    stats = reconcile(args.root, args.overrides, dry_run=args.dry_run)
    prefix = "Would reconcile" if args.dry_run else "Reconciled"
    print(
        f"{prefix} divisions on {stats['pages_mapped']} PDF page(s); "
        f"updated {stats['rows_updated']} row(s) across {stats['files']} CSV file(s); "
        f"command staff: {stats['command_staff_pages_reparsed']} page(s), "
        f"{stats['command_staff_rows_dropped']} dropped -> "
        f"{stats['command_staff_rows_reparsed']} re-parsed "
        f"({stats['command_staff_rows_after']} total)"
    )


if __name__ == "__main__":
    main()
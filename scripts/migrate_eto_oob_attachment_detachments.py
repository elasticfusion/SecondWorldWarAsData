#!/usr/bin/env python3
"""Re-parse mixed attachment/detachment PDF pages and fix CSV row placement."""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

import fitz
import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from extract_eto_order_of_battle import (  # noqa: E402
    _collect_attachment_spans,
    _detachment_section_marker,
    _find_attached_to_section_y,
    _find_detachment_section_y,
    build_page_lines,
    clean_text,
    detect_section,
    extract_source_page,
    load_pdf_page_division_overrides,
    match_division,
    normalize_attachment_category,
    parse_attachments_page,
    should_parse_attachments_page,
    walk_pdf_page_divisions,
)

DEFAULT_ROOT = (
    PROJECT_ROOT
    / "contentrepository"
    / "European Thater of Operations - Order of Battle"
)
DEFAULT_101ST_MOVES = (
    PROJECT_ROOT / "config" / "eto_oob_101st_detachment_moves.yaml"
)

DEFAULT_OVERRIDES = (
    PROJECT_ROOT / "config" / "eto_oob_pdf_page_division_overrides.yaml"
)


def attachment_to_detachment_row(row: dict[str, str]) -> dict[str, str]:
    return {
        "division": row["division"],
        "source_page": row.get("source_page", ""),
        "pdf_page": row.get("pdf_page", ""),
        "source_line": row.get("source_line", ""),
        "unit_name": row.get("unit_name", ""),
        "attached_to_organization": row.get("parent_unit_name", ""),
        "date_from": row.get("date_from", ""),
        "date_to": row.get("date_to", ""),
        "scope_note": row.get("scope_note", ""),
    }


def load_101st_move_spec(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def migrate_101st_detachments(
    root: Path,
    spec_path: Path,
    *,
    dry_run: bool = False,
) -> dict[str, int]:
    spec = load_101st_move_spec(spec_path)
    division = spec.get("division", "101st Airborne Division")
    move_keys = {
        (str(pair[0]), str(pair[1])) for pair in (spec.get("row_keys") or [])
    }
    reparse_pages = {
        str(item["pdf_page"]): item for item in (spec.get("reparse_pdf_pages") or [])
    }
    move_pdf_pages = {str(p) for p in (spec.get("move_pdf_pages") or [])}

    att_path = root / "eto_oob_attachments.csv"
    det_path = root / "eto_oob_detachments.csv"
    pdf_path = root / "ETO_Order_of_Battle.pdf"

    with att_path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        att_fields = reader.fieldnames or []
        all_att = list(reader)

    with det_path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        det_fields = reader.fieldnames or []
        all_det = list(reader)

    reparse_pdf_set = set(reparse_pages)
    affected_pdf_pages = reparse_pdf_set | move_pdf_pages

    kept_att: list[dict[str, str]] = []
    moved_det: list[dict[str, str]] = []
    moved_count = 0

    for row in all_att:
        if row.get("division") != division:
            kept_att.append(row)
            continue
        pdf_page = str(row.get("pdf_page", ""))
        source_line = str(row.get("source_line", ""))
        key = (pdf_page, source_line)
        if pdf_page in reparse_pdf_set:
            continue
        if pdf_page in move_pdf_pages and key in move_keys:
            moved_det.append(attachment_to_detachment_row(row))
            moved_count += 1
            continue
        kept_att.append(row)

    kept_det = [
        row
        for row in all_det
        if not (
            row.get("division") == division
            and str(row.get("pdf_page", "")) in affected_pdf_pages
        )
    ]

    doc = fitz.open(pdf_path)
    reparse_att = 0
    reparse_det = 0
    for pdf_page, page_spec in sorted(reparse_pages.items(), key=lambda i: int(i[0])):
        pno = int(pdf_page) - 1
        fitz_page = doc[pno]
        lines = [
            (index, line)
            for index, line in enumerate(fitz_page.get_text("text").splitlines(), 1)
        ]
        att_rows, det_rows, _ = parse_attachments_page(
            lines,
            division,
            page_spec.get("start_category", "Infantry"),
            page_spec.get("source_page", ""),
            int(pdf_page),
            fitz_page=fitz_page,
        )
        for row in att_rows:
            kept_att.append(row)
            reparse_att += 1
        for row in det_rows:
            if is_detach_header(row.get("unit_name", "")):
                continue
            if normalize_attachment_category(row.get("unit_name", "")):
                continue
            moved_det.append(row)
            reparse_det += 1
    doc.close()

    sort_key = lambda row: (  # noqa: E731
        row["division"],
        int(row.get("pdf_page") or 0),
        int(row.get("source_line") or 0),
    )
    kept_att.sort(key=sort_key)
    kept_det.extend(moved_det)
    kept_det.sort(key=sort_key)

    stats = {
        "moved_from_attachments": moved_count,
        "reparse_attachments": reparse_att,
        "reparse_detachments": reparse_det,
        "attachments_before": len(all_att),
        "attachments_after": len(kept_att),
        "detachments_before": len(all_det),
        "detachments_after": len(kept_det),
    }

    if not dry_run:
        with att_path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=att_fields, lineterminator="\n")
            writer.writeheader()
            writer.writerows(kept_att)
        with det_path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=det_fields, lineterminator="\n")
            writer.writeheader()
            writer.writerows(kept_det)
        from extract_eto_order_of_battle import apply_csv_search_replacements  # noqa: PLC0415

        apply_csv_search_replacements(root)

    return stats


def row_page_key(row: dict[str, str]) -> tuple[str, str, str]:
    return (row["division"], row.get("source_page", ""), str(row.get("pdf_page", "")))


def is_detach_header(name: str) -> bool:
    return _detachment_section_marker(name)


def start_category_for_page(
    rows: list[dict[str, str]],
    fitz_page,
) -> str:
    for line in fitz_page.get_text("text").splitlines()[:12]:
        category = normalize_attachment_category(line)
        if category:
            return category
    header_sls = [
        int(r["source_line"])
        for r in rows
        if is_detach_header(r.get("unit_name", ""))
    ]
    cutoff = min(header_sls) if header_sls else 10**9
    category = ""
    for row in sorted(rows, key=lambda item: int(item.get("source_line") or 0)):
        if int(row.get("source_line") or 0) >= cutoff:
            break
        if row.get("category"):
            category = row["category"]
    return category or "Infantry"


def division_from_pdf_header(fitz_page) -> str | None:
    for line in fitz_page.get_text("text").splitlines()[:8]:
        division = match_division(line)
        if division:
            return division
    return None


def keys_on_pdf_page(
    pdf_page: str,
    pages_from_csv: dict[tuple[str, str, str], list[dict[str, str]]],
) -> set[tuple[str, str, str]]:
    return {key for key in pages_from_csv if key[2] == pdf_page}


def division_for_pdf_page(
    pdf_page: str,
    pages_from_csv: dict[tuple[str, str, str], list[dict[str, str]]],
    fitz_page,
) -> tuple[str, str] | None:
    page_overrides = load_pdf_page_division_overrides(DEFAULT_OVERRIDES)
    if pdf_page in page_overrides:
        division = page_overrides[pdf_page]
        for key in pages_from_csv:
            if key[2] == pdf_page and key[0] == division:
                return division, key[1]
        return division, ""
    pdf_div = division_from_pdf_header(fitz_page)
    matches = [key for key in pages_from_csv if key[2] == pdf_page and key[0]]
    if pdf_div:
        for key in matches:
            if key[0] == pdf_div:
                return key[0], key[1]
        return pdf_div, ""
    if len(matches) == 1:
        return matches[0][0], matches[0][1]
    if matches:
        return matches[0][0], matches[0][1]
    return None


def migrate(root: Path, *, dry_run: bool = False) -> dict[str, int]:
    att_path = root / "eto_oob_attachments.csv"
    det_path = root / "eto_oob_detachments.csv"
    pdf_path = root / "ETO_Order_of_Battle.pdf"

    with att_path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        att_fields = reader.fieldnames or []
        all_att = list(reader)

    with det_path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        det_fields = reader.fieldnames or []
        all_det = list(reader)

    pages_from_att: dict[tuple[str, str, str], list[dict[str, str]]] = {}
    for row in all_att:
        pages_from_att.setdefault(row_page_key(row), []).append(row)

    pages_from_det: dict[tuple[str, str, str], list[dict[str, str]]] = {}
    for row in all_det:
        pages_from_det.setdefault(row_page_key(row), []).append(row)

    pages_from_csv = {**pages_from_att}
    for key, rows in pages_from_det.items():
        pages_from_csv.setdefault(key, rows)

    doc = fitz.open(pdf_path)
    pages_to_migrate: set[tuple[str, str, str]] = set()
    keys_to_drop: set[tuple[str, str, str]] = set()
    for pno in range(doc.page_count):
        fitz_page = doc[pno]
        spans = _collect_attachment_spans(fitz_page)
        if (
            _find_detachment_section_y(spans) is None
            and _find_attached_to_section_y(spans) is None
        ):
            continue
        pdf_page = str(pno + 1)
        keys_to_drop |= keys_on_pdf_page(pdf_page, pages_from_att)
        keys_to_drop |= keys_on_pdf_page(pdf_page, pages_from_det)
        resolved = division_for_pdf_page(pdf_page, pages_from_csv, fitz_page)
        if not resolved:
            continue
        division, source_page = resolved
        pages_to_migrate.add((division, source_page, pdf_page))

    for key in pages_from_att:
        if any(is_detach_header(row.get("unit_name", "")) for row in pages_from_att[key]):
            keys_to_drop.add(key)
            pages_to_migrate.add(key)

    new_att_by_page: dict[tuple[str, str, str], list[dict[str, str]]] = {}
    new_det_by_page: dict[tuple[str, str, str], list[dict[str, str]]] = {}

    for division, source_page, pdf_page in sorted(
        pages_to_migrate, key=lambda item: int(item[2] or 0)
    ):
        pno = int(pdf_page) - 1
        fitz_page = doc[pno]
        lines = [
            (index, line)
            for index, line in enumerate(fitz_page.get_text("text").splitlines(), 1)
        ]
        page_rows = pages_from_csv.get((division, source_page, pdf_page), [])
        start_category = start_category_for_page(page_rows, fitz_page)
        att_rows, det_rows, _ = parse_attachments_page(
            lines,
            division,
            start_category,
            source_page,
            int(pdf_page),
            fitz_page=fitz_page,
        )
        key = (division, source_page, pdf_page)
        new_att_by_page[key] = att_rows
        new_det_by_page[key] = det_rows

    doc.close()

    migrated_keys = keys_to_drop | set(pages_to_migrate)
    kept_att = [row for row in all_att if row_page_key(row) not in migrated_keys]
    kept_det = [row for row in all_det if row_page_key(row) not in migrated_keys]

    for rows in new_att_by_page.values():
        kept_att.extend(rows)
    for rows in new_det_by_page.values():
        kept_det.extend(rows)

    sort_key = lambda row: (  # noqa: E731
        row["division"],
        int(row.get("pdf_page") or 0),
        int(row.get("source_line") or 0),
    )
    kept_att.sort(key=sort_key)
    kept_det.sort(key=sort_key)

    stats = {
        "pages_migrated": len(pages_to_migrate),
        "attachments_before": len(all_att),
        "attachments_after": len(kept_att),
        "detachments_before": len(all_det),
        "detachments_after": len(kept_det),
        "detachment_rows_added": sum(len(rows) for rows in new_det_by_page.values()),
    }

    if not dry_run:
        with att_path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=att_fields, lineterminator="\n")
            writer.writeheader()
            writer.writerows(kept_att)
        with det_path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=det_fields, lineterminator="\n")
            writer.writeheader()
            writer.writerows(kept_det)
        from extract_eto_order_of_battle import apply_csv_search_replacements  # noqa: PLC0415

        apply_csv_search_replacements(root)

    return stats


def reconcile_all_attachments(root: Path, *, dry_run: bool = False) -> dict[str, int]:
    """Re-parse every attachments-section PDF page and drop wrong-section attachment rows."""
    att_path = root / "eto_oob_attachments.csv"
    det_path = root / "eto_oob_detachments.csv"
    pdf_path = root / "ETO_Order_of_Battle.pdf"

    with att_path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        att_fields = reader.fieldnames or []
        all_att = list(reader)

    with det_path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        det_fields = reader.fieldnames or []
        all_det = list(reader)

    pages_from_att: dict[tuple[str, str, str], list[dict[str, str]]] = {}
    for row in all_att:
        pages_from_att.setdefault(row_page_key(row), []).append(row)

    doc = fitz.open(pdf_path)
    page_divisions = walk_pdf_page_divisions(doc)
    current_section = ""
    category_by_division: dict[str, str] = {}
    pages_to_reparse: dict[str, tuple[str, str]] = {}
    new_att_by_page: dict[tuple[str, str, str], list[dict]] = {}
    new_det_by_page: dict[tuple[str, str, str], list[dict]] = {}

    for page_index in range(20, 570):
        if page_index >= doc.page_count:
            break
        fitz_page = doc[page_index]
        page_text = fitz_page.get_text("text")
        if "ORGANIC COMPOSITION" in page_text.upper() and "DIVISIONS" in page_text.upper():
            break

        pdf_page = str(page_index + 1)
        pdf_page_int = page_index + 1
        page_division = page_divisions.get(pdf_page, "")
        page_lines = build_page_lines(page_text)
        lines = [text for _, text in page_lines]
        lines_enum = [
            (index, line)
            for index, line in enumerate(page_text.splitlines(), 1)
        ]

        page_section = current_section
        for header_line in lines:
            section = detect_section(header_line)
            if section:
                page_section = section
                current_section = section
                break

        if not page_division:
            continue
        if not should_parse_attachments_page(
            page_lines,
            page_section=page_section,
            current_section=current_section,
        ):
            continue

        source_page = extract_source_page(page_text)
        if not source_page:
            for key in pages_from_att:
                if key[2] == pdf_page and key[0] == page_division:
                    source_page = key[1]
                    break

        start_category = category_by_division.get(page_division, "")
        att_rows, det_rows, end_category = parse_attachments_page(
            lines_enum,
            page_division,
            start_category,
            source_page,
            pdf_page_int,
            fitz_page=fitz_page,
        )
        if end_category:
            category_by_division[page_division] = end_category

        pages_to_reparse[pdf_page] = (page_division, source_page)
        key = (page_division, source_page, pdf_page)
        new_att_by_page[key] = att_rows
        new_det_by_page[key] = [
            row
            for row in det_rows
            if not is_detach_header(row.get("unit_name", ""))
            and not normalize_attachment_category(row.get("unit_name", ""))
        ]

    doc.close()

    reparse_pdf_pages = set(pages_to_reparse)
    keys_to_drop = {
        key for key in pages_from_att if key[2] in reparse_pdf_pages
    }

    wrong_section_dropped = sum(
        1 for row in all_att if str(row.get("pdf_page", "")) not in reparse_pdf_pages
    )
    kept_att: list[dict[str, str]] = []

    kept_det = [row for row in all_det if row_page_key(row) not in keys_to_drop]

    reparse_att = 0
    reparse_det = 0
    for rows in new_att_by_page.values():
        kept_att.extend(rows)
        reparse_att += len(rows)
    for rows in new_det_by_page.values():
        kept_det.extend(rows)
        reparse_det += len(rows)

    sort_key = lambda row: (  # noqa: E731
        row["division"],
        int(row.get("pdf_page") or 0),
        int(row.get("source_line") or 0),
    )
    kept_att.sort(key=sort_key)
    kept_det.sort(key=sort_key)

    stats = {
        "pages_reparsed": len(pages_to_reparse),
        "wrong_section_dropped": wrong_section_dropped,
        "attachments_before": len(all_att),
        "attachments_after": len(kept_att),
        "detachments_before": len(all_det),
        "detachments_after": len(kept_det),
        "reparse_attachments": reparse_att,
        "reparse_detachments": reparse_det,
    }

    if not dry_run:
        with att_path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=att_fields, lineterminator="\n")
            writer.writeheader()
            writer.writerows(kept_att)
        with det_path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=det_fields, lineterminator="\n")
            writer.writeheader()
            writer.writerows(kept_det)
        from extract_eto_order_of_battle import apply_csv_search_replacements  # noqa: PLC0415

        apply_csv_search_replacements(root)

    return stats


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--101st-detachments",
        dest="move_101st_detachments",
        action="store_true",
        help="Move 101st rows from config/eto_oob_101st_detachment_moves.yaml",
    )
    parser.add_argument(
        "--101st-spec",
        dest="spec_101st",
        type=Path,
        default=DEFAULT_101ST_MOVES,
        help="YAML spec for --101st-detachments",
    )
    parser.add_argument(
        "--reconcile-all",
        action="store_true",
        help="Re-parse all attachments-section pages and drop wrong-section rows",
    )
    args = parser.parse_args()
    if args.reconcile_all:
        stats = reconcile_all_attachments(args.root, dry_run=args.dry_run)
        prefix = "Would reconcile" if args.dry_run else "Reconciled"
        print(
            f"{prefix} {stats['pages_reparsed']} attachment page(s); "
            f"dropped {stats['wrong_section_dropped']} wrong-section row(s); "
            f"attachments {stats['attachments_before']} -> {stats['attachments_after']} "
            f"({stats['reparse_attachments']} re-parsed); "
            f"detachments {stats['detachments_before']} -> {stats['detachments_after']} "
            f"({stats['reparse_detachments']} re-parsed)"
        )
        return
    if args.move_101st_detachments:
        spec = load_101st_move_spec(args.spec_101st)
        stats = migrate_101st_detachments(
            args.root, args.spec_101st, dry_run=args.dry_run
        )
        prefix = "Would move" if args.dry_run else "Moved"
        print(
            f"{prefix} {stats['moved_from_attachments']} row(s) by key; "
            f"re-parsed {stats['reparse_detachments']} detachment + "
            f"{stats['reparse_attachments']} attachment row(s) on "
            f"{len(spec.get('reparse_pdf_pages') or [])} page(s); "
            f"attachments {stats['attachments_before']} -> {stats['attachments_after']}; "
            f"detachments {stats['detachments_before']} -> {stats['detachments_after']}"
        )
        return
    stats = migrate(args.root, dry_run=args.dry_run)
    prefix = "Would migrate" if args.dry_run else "Migrated"
    print(
        f"{prefix} {stats['pages_migrated']} page(s); "
        f"attachments {stats['attachments_before']} -> {stats['attachments_after']}; "
        f"detachments {stats['detachments_before']} -> {stats['detachments_after']} "
        f"({stats['detachment_rows_added']} detachment rows from PDF)"
    )


if __name__ == "__main__":
    main()
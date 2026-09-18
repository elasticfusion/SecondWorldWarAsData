#!/usr/bin/env python3
"""Extract U.S. Army Unit Records finding aid PDFs into CSV."""

from __future__ import annotations

import csv
import json
import re
import urllib.request
from pathlib import Path

import fitz

PROJECT_ROOT = Path(__file__).resolve().parents[1]
BASE_URL = "https://www.eisenhowerlibrary.gov"
LANDING_PAGE_URL = f"{BASE_URL}/research/finding-aids/us-army-unit-records"
PDF_CACHE_DIR = (
    PROJECT_ROOT / "contentrepository/EisenhowerPresidentialLibraryFindingAids"
)
OUTPUT_CSV = (
    PROJECT_ROOT / "contentrepository/indexes/eisenhower_us_army_unit_records.csv"
)
MANIFEST_JSON = (
    PROJECT_ROOT
    / "contentrepository/indexes/eisenhower_us_army_unit_records_manifest.json"
)

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

FINDING_AIDS: list[dict[str, str | int]] = [
    {
        "pdf_slug": "us-army-unit-records-1",
        "book_number": 1,
        "box_range": "1-126",
    },
    {
        "pdf_slug": "us-army-unit-records-2",
        "book_number": 2,
        "box_range": "127-489",
    },
    {
        "pdf_slug": "us-army-unit-records-3",
        "book_number": 3,
        "box_range": "490-747",
    },
    {
        "pdf_slug": "us-army-unit-records-4",
        "book_number": 4,
        "box_range": "748-902",
    },
    {
        "pdf_slug": "us-army-unit-records-5",
        "book_number": 5,
        "box_range": "903-1072",
    },
    {
        "pdf_slug": "us-army-unit-records-6",
        "book_number": 6,
        "box_range": "1073-1240",
    },
    {
        "pdf_slug": "us-army-unit-records-7",
        "book_number": 7,
        "box_range": "1241-1403",
    },
    {
        "pdf_slug": "us-army-unit-records-8",
        "book_number": 8,
        "box_range": "1404-1567",
    },
    {
        "pdf_slug": "us-army-82nd-airborne-division",
        "book_number": 0,
        "box_range": "microfilm",
    },
]

CSV_FIELDS = [
    "source_collection_title",
    "source_landing_page_url",
    "source_pdf_url",
    "book_number",
    "book_box_range",
    "unit_name",
    "unit_date_range",
    "approximate_pages",
    "unit_boxes",
    "section",
    "entry_type",
    "subseries",
    "box",
    "reel_number",
    "folder_or_item_title",
    "unit_narrative",
    "chronology_text",
    "accession_number",
    "processed_by",
    "date_completed",
    "linear_feet",
    "microfilm_reels",
    "page_number_in_pdf",
    "raw_entry_text",
]

BOOK_HEADER_RE = re.compile(
    r"U\.S\. Army Unit Records, Book (\d+)(?:,)?\s*\(Boxes ([\d\-]+)\)",
    re.I,
)
PAGES_RE = re.compile(r"^([\d,]+)\s+pages?\s*(?:\(approximate\))?\s*$", re.I)
BOXES_RE = re.compile(r"^Box(?:es)?\s+([\d\-,\s]+)$", re.I)
CONTAINER_LIST_RE = re.compile(r"^CONTAINER LIST\s*$", re.I)
CHRONOLOGY_RE = re.compile(r"^CHRONOLOGY\s*$", re.I)
SUBSERIES_RE = re.compile(r"^SUBSERIES\s+", re.I)
SCOPE_RE = re.compile(r"^SCOPE AND CONTENT NOTE\s*$", re.I)
SKIP_LINE_RE = re.compile(
    r"^(Box Nos?\.?|Folder Title|Reel No\.?\s*Contents?|Contents|Box\s+No\.?)\s*$",
    re.I,
)
BOX_NUM_ONLY_RE = re.compile(r"^(\d{1,4})\s*$")
BOX_NUM_FOLDER_RE = re.compile(r"^(\d{1,4})\s+(.+)$")
UNIT_TYPE_RE = (
    r"DIVISION|REGIMENT|BATTALION|BRIGADE|SQUADRON|DETACHMENT|"
    r"GROUP|CORPS|COMMAND|COMPANY|BATTERY"
)
UNIT_TYPE_CAPS_RE = re.compile(
    r"\b(?:DIVISION|REGIMENT|BATTALION|GROUP|BRIGADE|CORPS|SQUADRON|COMMAND|"
    r"DETACHMENT|TANK|COMPANY|BATTERY)\b"
)
UNIT_HEADER_RE = re.compile(
    rf"^(\d{{1,4}}(?:st|nd|rd|th)\s+)?"
    rf"(.+?(?:{UNIT_TYPE_RE}))"
    rf"(?:\s*\([^)]+\))?"
    rf"(?:\s*[,.\s]\s*([\d\s\-\–—,\.()a-zA-Z]+))?"
    rf"\.?\s*$",
    re.I,
)
SUBSERIES_LINE_RE = re.compile(r"^SUB-?SERIES\s+", re.I)
DATE_EVENT_RE = re.compile(
    r"^([A-Z][a-z]+\s+\d{1,2},?\s+\d{4}|[A-Z][a-z]+\s+\d{4})\s*$"
)


def pdf_url(slug: str) -> str:
    return f"{BASE_URL}/sites/default/files/finding-aids/pdf/{slug}.pdf"


def download_pdf(slug: str) -> Path:
    cache_path = PDF_CACHE_DIR / f"{slug}.pdf"
    if cache_path.exists() and cache_path.stat().st_size > 1000:
        return cache_path
    PDF_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    request = urllib.request.Request(pdf_url(slug), headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=120) as response:
        cache_path.write_bytes(response.read())
    return cache_path


def clean_line(line: str) -> str:
    return re.sub(r"\s+", " ", line.replace("\u00a0", " ")).strip()


def is_unit_header(line: str, *, in_container_list: bool = False) -> bool:
    if len(line) > 120:
        return False
    if not re.match(r"^\d{1,4}(?:st|nd|rd|th)\s+", line, re.I):
        return False
    if not UNIT_TYPE_CAPS_RE.search(line):
        return False
    if not UNIT_HEADER_RE.match(line):
        return False
    without_parens = re.sub(r"\([^)]*\)", "", line)
    if sum(1 for c in without_parens if c.islower()) > 15:
        return False
    if in_container_list:
        return "," in line and bool(re.search(r"\b(19|20)\d{2}", line))
    return True


def is_container_number(value: str) -> bool:
    return value.isdigit() and 1 <= int(value) <= 200


def split_unit_header(line: str) -> tuple[str, str]:
    if "," in line:
        unit_name, date_range = line.split(",", 1)
        return unit_name.strip(), date_range.strip().rstrip(".")
    match = re.match(
        rf"^(.+?(?:{UNIT_TYPE_RE}))\s*[,.\s]\s*(.+)$",
        line,
        re.I,
    )
    if match:
        return match.group(1).strip(), match.group(2).strip().rstrip(".")
    return line, ""


def extract_lines(pdf_path: Path) -> list[tuple[int, str]]:
    doc = fitz.open(pdf_path)
    line_page: list[tuple[int, str]] = []
    for page_index, page in enumerate(doc):
        for line in page.get_text().splitlines():
            cleaned = clean_line(line)
            if cleaned:
                line_page.append((page_index + 1, cleaned))
    return line_page


def make_row(
    *,
    meta: dict[str, str | int],
    entry_type: str,
    page_number: int,
    section: str = "",
    unit_name: str = "",
    unit_date_range: str = "",
    approximate_pages: str = "",
    unit_boxes: str = "",
    subseries: str = "",
    box: str = "",
    reel_number: str = "",
    folder_or_item_title: str = "",
    unit_narrative: str = "",
    chronology_text: str = "",
    accession_number: str = "",
    processed_by: str = "",
    date_completed: str = "",
    linear_feet: str = "",
    microfilm_reels: str = "",
    raw_entry_text: str = "",
) -> dict[str, str]:
    return {
        "source_collection_title": "U.S. ARMY: Unit Records, 1940-50",
        "source_landing_page_url": LANDING_PAGE_URL,
        "source_pdf_url": str(meta["source_pdf_url"]),
        "book_number": str(meta["book_number"]),
        "book_box_range": str(meta["book_box_range"]),
        "unit_name": unit_name,
        "unit_date_range": unit_date_range,
        "approximate_pages": approximate_pages,
        "unit_boxes": unit_boxes,
        "section": section,
        "entry_type": entry_type,
        "subseries": subseries,
        "box": box,
        "reel_number": reel_number,
        "folder_or_item_title": folder_or_item_title,
        "unit_narrative": unit_narrative,
        "chronology_text": chronology_text,
        "accession_number": accession_number,
        "processed_by": processed_by,
        "date_completed": date_completed,
        "linear_feet": linear_feet,
        "microfilm_reels": microfilm_reels,
        "page_number_in_pdf": str(page_number),
        "raw_entry_text": raw_entry_text,
    }


def parse_standard_book(meta: dict[str, str | int], line_page: list[tuple[int, str]]) -> list[dict[str, str]]:
    entries: list[dict[str, str]] = []
    state = "collection_intro"
    current_unit = ""
    current_date_range = ""
    current_pages = ""
    current_unit_boxes = ""
    current_subseries = ""
    current_box = ""
    intro_flushed = False
    narrative_parts: list[str] = []
    chronology_parts: list[str] = []

    def flush_unit_intro(page_number: int) -> None:
        nonlocal narrative_parts, chronology_parts, intro_flushed
        if not current_unit or intro_flushed:
            return
        entries.append(
            make_row(
                meta=meta,
                entry_type="unit_series_intro",
                page_number=page_number,
                section="unit_overview",
                unit_name=current_unit,
                unit_date_range=current_date_range,
                approximate_pages=current_pages,
                unit_boxes=current_unit_boxes,
                unit_narrative=" ".join(narrative_parts).strip(),
                chronology_text=" ".join(chronology_parts).strip(),
                raw_entry_text=f"{current_unit}, {current_date_range}".strip(", "),
            )
        )
        narrative_parts = []
        chronology_parts = []
        intro_flushed = True

    def start_unit(page_number: int, line: str) -> None:
        nonlocal state, current_unit, current_date_range, current_pages
        nonlocal current_unit_boxes, current_subseries, current_box, intro_flushed
        flush_unit_intro(page_number)
        state = "unit_meta"
        current_unit, current_date_range = split_unit_header(line)
        current_pages = ""
        current_unit_boxes = ""
        current_subseries = ""
        current_box = ""
        intro_flushed = False

    for page_number, line in line_page:
        if BOOK_HEADER_RE.search(line):
            continue

        if is_unit_header(line):
            start_unit(page_number, line)
            continue

        if state == "collection_intro" and not current_unit:
            if line.startswith("U.S. ARMY UNIT RECORDS"):
                continue
            continue

        if PAGES_RE.match(line):
            current_pages = PAGES_RE.match(line).group(1).replace(",", "")
            state = "unit_meta"
            continue

        if BOXES_RE.match(line):
            current_unit_boxes = BOXES_RE.match(line).group(1).strip()
            state = "narrative"
            continue

        if CHRONOLOGY_RE.match(line):
            state = "chronology"
            continue

        if CONTAINER_LIST_RE.match(line):
            flush_unit_intro(page_number)
            state = "container_list"
            current_box = ""
            continue

        if state == "chronology":
            chronology_parts.append(line)
            continue

        if state == "container_list":
            if SKIP_LINE_RE.match(line):
                continue
            if SUBSERIES_RE.match(line) or SUBSERIES_LINE_RE.match(line):
                current_subseries = line
                entries.append(
                    make_row(
                        meta=meta,
                        entry_type="subseries_header",
                        page_number=page_number,
                        section="container_list",
                        unit_name=current_unit,
                        unit_date_range=current_date_range,
                        unit_boxes=current_unit_boxes,
                        subseries=line,
                        raw_entry_text=line,
                    )
                )
                continue
            if is_unit_header(line, in_container_list=True):
                start_unit(page_number, line)
                continue
            box_only = BOX_NUM_ONLY_RE.match(line)
            if box_only:
                current_box = box_only.group(1)
                continue
            box_folder = BOX_NUM_FOLDER_RE.match(line)
            if box_folder:
                current_box = box_folder.group(1)
                folder = box_folder.group(2).strip()
                entries.append(
                    make_row(
                        meta=meta,
                        entry_type="folder_item",
                        page_number=page_number,
                        section="container_list",
                        unit_name=current_unit,
                        unit_date_range=current_date_range,
                        unit_boxes=current_unit_boxes,
                        subseries=current_subseries,
                        box=current_box,
                        folder_or_item_title=folder,
                        raw_entry_text=f"Box {current_box} {folder}",
                    )
                )
                continue
            entries.append(
                make_row(
                    meta=meta,
                    entry_type="folder_item",
                    page_number=page_number,
                    section="container_list",
                    unit_name=current_unit,
                    unit_date_range=current_date_range,
                    unit_boxes=current_unit_boxes,
                    subseries=current_subseries,
                    box=current_box,
                    folder_or_item_title=line,
                    raw_entry_text=line,
                )
            )
            continue

        if state == "narrative":
            narrative_parts.append(line)
            continue

    flush_unit_intro(line_page[-1][0] if line_page else 1)
    return entries


def parse_microfilm_finding_aid(
    meta: dict[str, str | int], line_page: list[tuple[int, str]]
) -> list[dict[str, str]]:
    entries: list[dict[str, str]] = []
    unit_name = "82nd AIRBORNE DIVISION"
    unit_date_range = "1943-46"
    scope_parts: list[str] = []
    intro_parts: list[str] = []
    state = "intro"
    current_box = ""
    current_reel = ""
    accession = ""
    processed_by = ""
    date_completed = ""
    linear_feet = ""
    microfilm_reels = ""

    for page_number, line in line_page:
        if line.startswith("DWIGHT D. EISENHOWER LIBRARY"):
            continue
        if line.startswith("U.S. ARMY, 82nd AIRBORNE DIVISION"):
            continue
        if line.startswith("Accession "):
            accession = line.replace("Accession ", "").strip()
            continue
        if line.startswith("Processed by:"):
            processed_by = line.replace("Processed by:", "").strip()
            continue
        if line.startswith("Date Completed:"):
            date_completed = line.replace("Date Completed:", "").strip()
            continue
        if line.startswith("Linear feet"):
            continue
        if line == "4" and not linear_feet:
            linear_feet = "4"
            continue
        if line.startswith("Number of reels"):
            continue
        if line == "62" and not microfilm_reels:
            microfilm_reels = "62"
            continue
        if SCOPE_RE.match(line):
            state = "scope"
            continue
        if CONTAINER_LIST_RE.match(line):
            if scope_parts or intro_parts:
                entries.append(
                    make_row(
                        meta=meta,
                        entry_type="collection_intro",
                        page_number=page_number,
                        section="scope_and_content",
                        unit_name=unit_name,
                        unit_date_range=unit_date_range,
                        unit_narrative=" ".join(intro_parts + scope_parts).strip(),
                        accession_number=accession,
                        processed_by=processed_by,
                        date_completed=date_completed,
                        linear_feet=linear_feet,
                        microfilm_reels=microfilm_reels,
                        raw_entry_text=" ".join(intro_parts + scope_parts).strip(),
                    )
                )
            state = "container_list"
            continue
        if state == "scope":
            scope_parts.append(line)
            continue
        if state == "intro":
            intro_parts.append(line)
            continue
        if state == "container_list":
            if SKIP_LINE_RE.match(line):
                continue
            box_only = BOX_NUM_ONLY_RE.match(line)
            if box_only and is_container_number(box_only.group(1)):
                value = box_only.group(1)
                if not current_box or current_reel:
                    current_box = value
                    current_reel = ""
                else:
                    current_reel = value
                continue
            effective_reel = current_reel or current_box
            if BOX_NUM_FOLDER_RE.match(line):
                current_box = BOX_NUM_FOLDER_RE.match(line).group(1)
                folder = BOX_NUM_FOLDER_RE.match(line).group(2).strip()
                entries.append(
                    make_row(
                        meta=meta,
                        entry_type="microfilm_item",
                        page_number=page_number,
                        section="container_list",
                        unit_name=unit_name,
                        unit_date_range=unit_date_range,
                        box=current_box,
                        reel_number=effective_reel,
                        folder_or_item_title=folder,
                        accession_number=accession,
                        microfilm_reels=microfilm_reels,
                        raw_entry_text=f"Box {current_box} Reel {current_reel} {folder}",
                    )
                )
                continue
            entries.append(
                make_row(
                    meta=meta,
                    entry_type="microfilm_item",
                    page_number=page_number,
                    section="container_list",
                    unit_name=unit_name,
                    unit_date_range=unit_date_range,
                    box=current_box,
                    reel_number=effective_reel,
                    folder_or_item_title=line,
                    accession_number=accession,
                    microfilm_reels=microfilm_reels,
                    raw_entry_text=line,
                )
            )

    return entries


def parse_finding_aid(guide: dict[str, str | int]) -> list[dict[str, str]]:
    slug = str(guide["pdf_slug"])
    meta = {
        "source_pdf_url": pdf_url(slug),
        "book_number": int(guide["book_number"]),
        "book_box_range": str(guide["box_range"]),
    }
    pdf_path = download_pdf(slug)
    line_page = extract_lines(pdf_path)
    if slug == "us-army-82nd-airborne-division":
        return parse_microfilm_finding_aid(meta, line_page)
    return parse_standard_book(meta, line_page)


def write_csv(entries: list[dict[str, str]]) -> None:
    OUTPUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    with OUTPUT_CSV.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(entries)


def main() -> None:
    all_entries: list[dict[str, str]] = []
    manifest_books: list[dict[str, object]] = []

    for guide in FINDING_AIDS:
        entries = parse_finding_aid(guide)
        all_entries.extend(entries)
        by_type: dict[str, int] = {}
        units = {entry["unit_name"] for entry in entries if entry["unit_name"]}
        for entry in entries:
            by_type[entry["entry_type"]] = by_type.get(entry["entry_type"], 0) + 1
        manifest_books.append(
            {
                "pdf_slug": guide["pdf_slug"],
                "book_number": guide["book_number"],
                "box_range": guide["box_range"],
                "pdf_url": pdf_url(str(guide["pdf_slug"])),
                "entry_count": len(entries),
                "unit_count": len(units),
                "entries_by_type": by_type,
            }
        )
        print(
            f"Book {guide['book_number']} ({guide['pdf_slug']}): "
            f"{len(entries)} entries, {len(units)} units"
        )

    write_csv(all_entries)
    manifest = {
        "source_landing_page_url": LANDING_PAGE_URL,
        "collection_note": (
            "Incomplete duplicate of National Archives collection; "
            "Textual Records Branch at Archives II handles reference inquiries "
            "effective June 30, 2025."
        ),
        "pdf_cache_dir": str(PDF_CACHE_DIR.relative_to(PROJECT_ROOT)),
        "output_csv": str(OUTPUT_CSV.relative_to(PROJECT_ROOT)),
        "books": manifest_books,
        "total_entries": len(all_entries),
    }
    MANIFEST_JSON.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"\nWrote {len(all_entries)} entries to {OUTPUT_CSV}")


if __name__ == "__main__":
    main()
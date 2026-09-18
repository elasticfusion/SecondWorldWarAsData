#!/usr/bin/env python3
"""Extract WWII-focused entries from the Eisenhower Library French Language Guide."""

from __future__ import annotations

import csv
import json
import re
import urllib.request
from pathlib import Path

import fitz

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_URL = (
    "https://www.eisenhowerlibrary.gov/sites/default/files/file/French_Language_Guide.pdf"
)
PDF_CACHE = (
    PROJECT_ROOT
    / "contentrepository/EisenhowerPresidentialLibrarySubjectGuides/French_Language_Guide.pdf"
)
OUTPUT_CSV = (
    PROJECT_ROOT
    / "contentrepository/indexes/eisenhower_french_language_guide_wwii.csv"
)
MANIFEST_JSON = (
    PROJECT_ROOT
    / "contentrepository/indexes/eisenhower_french_language_guide_wwii_manifest.json"
)

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

WWII_PROJECT_NUMBERS: dict[int, str] = {
    1: "Operation TORCH: military operations and political intrigue in French North Africa, 1942-43",
    2: "Psychological warfare (AFHQ/SHAEF, World War II)",
    3: "French Resistance and Liberation, 1940-45",
}

READING_SUBSECTIONS: set[tuple[int, str]] = {
    (3, "F"),  # Subsection F is only suggested readings in this project.
}

CSV_FIELDS = [
    "source_title",
    "source_pdf_url",
    "project_number",
    "project_title",
    "wwii_scope_note",
    "record_category",
    "subsection_letter",
    "entry_type",
    "collection_name",
    "series",
    "box",
    "folder_or_item_title",
    "description",
    "language_materials",
    "student_project_note",
    "bibliographic_citation",
    "related_project_reference",
    "page_number_in_guide",
    "raw_entry_text",
]

PROJECT_HEADER_RE = re.compile(r"^(\d{1,2})\.\s*$")
SUBSECTION_ONLY_RE = re.compile(r"^([A-Z])\.\s*$")
SUBSECTION_RE = re.compile(r"^([A-Z])\.\s+(.+)$")
INTRO_START_RE = re.compile(
    r"^(This project|Students|TORCH is|When TORCH|In addition|On September|"
    r"Resistance movements|Webster's|According to a|This directive|"
    r"Various propaganda|Even before|By November|Soon after)",
    re.I,
)
MAX_PROJECT_NUMBER = 7
BOX_FOLDER_RE = re.compile(
    r"^Box(?:es)?\s+([\d,\sA-Za-z&\-]+?)(?:,\s*File\s+Folder[s]?:\s*(.+)|,\s*(.+)|\s+(.+))?$",
    re.I,
)
BOX_ONLY_RE = re.compile(r"^Box\s+(\d+[A-Za-z]?)\s*$", re.I)
SUGGESTED_READING_RE = re.compile(r"^Suggested [Rr]eadings?:?\s*$", re.I)
NEW_CITATION_RE = re.compile(
    r"^(?:"
    r"Sir\s+Llewellyn|"
    r"M\.R\.D\.\s+Foot|"
    r"Henri\s+Michel|"
    r"European\s+Resistance|"
    r"Charles\s+de\s+Gaulle|"
    r"E\.H\.\s+Cookridge|"
    r"Georgette\s+Elgey|"
    r"General\s+Vo|"
    r"David\s+Anderson|"
    r"Bernard\s+B\.\s+Fall|"
    r"United\s+States\s+Department|"
    r"Harry\s+C\.\s+Butcher|"
    r"George\s+F\.\s+Howe|"
    r"Richard\s+W\.\s+Steele|"
    r"James\s+M\.\s+Erdmann|"
    r"Daniel\s+Lerner"
    r")",
    re.I,
)
SKIP_LINE_RE = re.compile(r"^Page\s+\d+\s*$", re.I)
GUIDE_TITLE_RE = re.compile(r"^FRENCH LANGUAGE GUIDE$", re.I)


def download_pdf() -> Path:
    if PDF_CACHE.exists() and PDF_CACHE.stat().st_size > 1000:
        return PDF_CACHE
    PDF_CACHE.parent.mkdir(parents=True, exist_ok=True)
    request = urllib.request.Request(SOURCE_URL, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=120) as response:
        PDF_CACHE.write_bytes(response.read())
    return PDF_CACHE


def clean_line(line: str) -> str:
    line = line.replace("\u00a0", " ").replace("\uf0af", "").replace("\uf0b7", "")
    return re.sub(r"\s+", " ", line).strip()


def is_likely_title_line(line: str) -> bool:
    stripped = line.rstrip(": ")
    return (
        stripped.isupper()
        and len(stripped) < 120
        and not line.startswith("Box ")
        and not SUBSECTION_RE.match(line)
        and not SUBSECTION_ONLY_RE.match(line)
    )


def is_intro_line(line: str) -> bool:
    return not is_likely_title_line(line) and bool(INTRO_START_RE.match(line))


def looks_like_top_level_project(line_page: list[tuple[str, int]], index: int) -> bool:
    match = PROJECT_HEADER_RE.match(line_page[index][0])
    if not match:
        return False
    number = int(match.group(1))
    if number < 1 or number > MAX_PROJECT_NUMBER:
        return False
    for offset in range(1, 4):
        if index + offset >= len(line_page):
            break
        nxt = line_page[index + offset][0]
        if is_likely_title_line(nxt):
            return True
        if nxt and not is_likely_title_line(nxt):
            return False
    return False


def extract_pages(pdf_path: Path) -> list[tuple[int, str]]:
    doc = fitz.open(pdf_path)
    return [(index + 1, page.get_text()) for index, page in enumerate(doc)]


def normalize_page_lines(text: str) -> list[str]:
    return [clean_line(line) for line in text.splitlines() if clean_line(line)]


def parse_projects(pages: list[tuple[int, str]]) -> list[dict[str, str | int]]:
    line_page: list[tuple[str, int]] = []
    for page_number, text in pages:
        for line in normalize_page_lines(text):
            if not SKIP_LINE_RE.match(line):
                line_page.append((line, page_number))

    projects: list[dict[str, str | int]] = []
    current: dict[str, str | int] | None = None
    title_parts: list[str] = []

    for index, (line, page_number) in enumerate(line_page):
        if GUIDE_TITLE_RE.match(line) or line == "Posted 11/2020":
            continue

        project_match = PROJECT_HEADER_RE.match(line)
        if project_match and looks_like_top_level_project(line_page, index):
            if current:
                current["title"] = " ".join(title_parts).strip()
                current["body_lines"] = body_lines
                projects.append(current)
            number = int(project_match.group(1))
            current = {
                "number": number,
                "start_page": page_number,
                "title": "",
            }
            title_parts = []
            body_lines: list[tuple[str, int]] = []
            continue

        if current is None:
            continue

        title_continuation = (
            is_likely_title_line(line)
            and not SUGGESTED_READING_RE.match(line)
        )
        if not body_lines and title_continuation:
            title_parts.append(line)
        elif (
            not title_parts
            and not body_lines
            and not SUBSECTION_RE.match(line)
            and not SUBSECTION_ONLY_RE.match(line)
            and not is_intro_line(line)
            and not SUGGESTED_READING_RE.match(line)
            and not line.startswith("Box ")
            and is_likely_title_line(line)
        ):
            title_parts.append(line)
        else:
            body_lines.append((line, page_number))

    if current:
        current["title"] = " ".join(title_parts).strip()
        current["body_lines"] = body_lines
        projects.append(current)

    return projects


def is_subsection_line(line: str) -> tuple[str, str] | None:
    only = SUBSECTION_ONLY_RE.match(line)
    if only:
        return only.group(1), ""
    match = SUBSECTION_RE.match(line)
    if match:
        return match.group(1), match.group(2).strip()
    return None


def looks_like_new_citation(line: str) -> bool:
    return bool(NEW_CITATION_RE.match(line))


def reading_section_stop(line: str) -> bool:
    return (
        bool(is_subsection_line(line))
        or bool(SUGGESTED_READING_RE.match(line))
        or line.startswith("Box ")
        or bool(PROJECT_HEADER_RE.match(line))
    )


def iter_reading_entries(lines: list[str], start: int) -> list[tuple[str, int]]:
    entries: list[tuple[str, int]] = []
    index = start
    current: list[str] = []

    while index < len(lines):
        line = lines[index]
        if reading_section_stop(line):
            break
        if looks_like_new_citation(line) and current:
            entries.append((" ".join(current).strip(), index))
            current = [line]
        else:
            current.append(line)
        index += 1

    if current:
        entries.append((" ".join(current).strip(), index))
    return entries


def join_paragraph(lines: list[str], start: int) -> tuple[str, int]:
    if start >= len(lines):
        return "", start
    if is_subsection_line(lines[start]) or SUGGESTED_READING_RE.match(lines[start]):
        return "", start
    parts = [lines[start]]
    index = start + 1
    while index < len(lines):
        nxt = lines[index]
        if is_subsection_line(nxt) or SUGGESTED_READING_RE.match(nxt):
            break
        if BOX_ONLY_RE.match(nxt) or BOX_FOLDER_RE.match(nxt):
            break
        if nxt.startswith("Box ") and "File Folder" in nxt:
            break
        if re.match(r"^Box\s+\d+", nxt, re.I):
            break
        parts.append(nxt)
        index += 1
        if nxt.endswith(".") and len(nxt) > 80:
            break
    return " ".join(parts).strip(), index


def extract_language_note(text: str) -> str:
    notes: list[str] = []
    if re.search(r"\bin French\b", text, re.I):
        notes.append("French language materials")
    if re.search(r"translation", text, re.I):
        notes.append("Includes translations")
    if re.search(r"leaflet", text, re.I):
        notes.append("Propaganda leaflets")
    if re.search(r"newspaper", text, re.I):
        notes.append("Newspapers")
    if re.search(r"\bin English\b", text, re.I):
        notes.append("English language materials")
    if re.search(r"\bin German\b", text, re.I):
        notes.append("German language materials")
    return "; ".join(notes)


def record_category_for(entry_type: str) -> str:
    if entry_type == "suggested_reading":
        return "recommended_reading"
    if entry_type == "project_intro":
        return "project_intro"
    return "presidential_library_holding"


def make_row(
    *,
    project: dict[str, str | int],
    entry_type: str,
    page_number: int,
    subsection: str = "",
    collection_name: str = "",
    series: str = "",
    box: str = "",
    folder_or_item_title: str = "",
    description: str = "",
    language_materials: str = "",
    student_project_note: str = "",
    bibliographic_citation: str = "",
    related_project_reference: str = "",
    raw_entry_text: str = "",
) -> dict[str, str]:
    number = int(project["number"])
    return {
        "source_title": "French Language Guide",
        "source_pdf_url": SOURCE_URL,
        "project_number": str(number),
        "project_title": str(project["title"]),
        "wwii_scope_note": WWII_PROJECT_NUMBERS.get(number, ""),
        "record_category": record_category_for(entry_type),
        "subsection_letter": subsection,
        "entry_type": entry_type,
        "collection_name": collection_name,
        "series": series,
        "box": box,
        "folder_or_item_title": folder_or_item_title,
        "description": description,
        "language_materials": language_materials or extract_language_note(
            f"{description} {folder_or_item_title} {raw_entry_text}"
        ),
        "student_project_note": student_project_note,
        "bibliographic_citation": bibliographic_citation,
        "related_project_reference": related_project_reference,
        "page_number_in_guide": str(page_number),
        "raw_entry_text": raw_entry_text,
    }


def parse_box_folder_line(line: str) -> tuple[str, str, str]:
    match = BOX_FOLDER_RE.match(line)
    if match:
        box = match.group(1).strip()
        folder = (match.group(2) or match.group(3) or match.group(4) or "").strip()
        return box, folder, line
    match = re.match(r"^Box\s+(\d+[A-Za-z]?)\s*[-–—]\s*(.+)$", line, re.I)
    if match:
        return match.group(1).strip(), match.group(2).strip(), line
    return "", line, line


def extract_collection_parts(text: str) -> tuple[str, str, str, str]:
    """Return collection_name, series, box, folder from a combined header line."""
    collection_name = text
    series = ""
    box = ""
    folder = ""

    series_match = re.search(
        r"(Series\s+[^,]+(?:,\s*[^,]+)?),\s*Box(?:es)?\s+\d",
        text,
        re.I,
    )
    if series_match:
        series = series_match.group(1).strip()
        collection_name = text[: series_match.start()].strip()

    box_match = re.search(
        r"Box(?:es)?\s+([\d]+(?:\s*[-–—]\s*[\d]+)?)",
        text,
        re.I,
    )
    folder_match = re.search(r"File\s+Folder[s]?:\s*(.+)$", text, re.I)
    if box_match:
        box = box_match.group(1).strip()
        if not series:
            collection_name = text[: box_match.start()].strip().rstrip(",")
    if folder_match:
        folder = folder_match.group(1).strip().rstrip(".")
    return collection_name, series, box, folder


def parse_project_entries(project: dict[str, str | int]) -> list[dict[str, str]]:
    number = int(project["number"])
    if number not in WWII_PROJECT_NUMBERS:
        return []

    body_lines = project["body_lines"]
    lines = [line for line, _ in body_lines]
    page_map = {index: page for index, (_, page) in enumerate(body_lines)}

    entries: list[dict[str, str]] = []
    index = 0
    current_subsection = ""
    current_collection = ""
    current_series = ""
    in_suggested_reading = False
    intro_parts: list[str] = []

    while index < len(lines):
        line = lines[index]
        page_number = page_map[index]

        if SUGGESTED_READING_RE.match(line):
            in_suggested_reading = True
            if (number, current_subsection) not in READING_SUBSECTIONS:
                current_subsection = ""
            index += 1
            continue

        subsection_match = is_subsection_line(line)
        if subsection_match and not in_suggested_reading:
            letter, remainder = subsection_match

            if (number, letter) in READING_SUBSECTIONS:
                current_subsection = letter
                index += 1
                if index < len(lines) and SUGGESTED_READING_RE.match(lines[index]):
                    in_suggested_reading = True
                    index += 1
                continue

            current_subsection = letter
            if intro_parts:
                intro_text = " ".join(intro_parts).strip()
                entries.append(
                    make_row(
                        project=project,
                        entry_type="project_intro",
                        page_number=page_map[0],
                        description=intro_text,
                        student_project_note=intro_text
                        if "student" in intro_text.lower()
                        else "",
                        raw_entry_text=intro_text,
                    )
                )
                intro_parts.clear()

            if remainder:
                collection_name, series, box, folder = extract_collection_parts(remainder)
                current_collection = collection_name or remainder
                current_series = series
                desc, next_index = join_paragraph(lines, index + 1)
                while next_index < len(lines) and is_subsection_line(lines[next_index]):
                    break
                while next_index < len(lines) and lines[next_index].startswith('"'):
                    quote, next_index = join_paragraph(lines, next_index)
                    folder = f"{folder} {quote}".strip() if folder else quote
                entries.append(
                    make_row(
                        project=project,
                        entry_type="collection_reference",
                        page_number=page_number,
                        subsection=letter,
                        collection_name=current_collection,
                        series=current_series,
                        box=box,
                        folder_or_item_title=folder,
                        description=desc,
                        raw_entry_text=f"{line} {desc}".strip(),
                    )
                )
                index = next_index
                continue

            index += 1
            if index < len(lines) and not is_subsection_line(lines[index]):
                header_line = lines[index]
                if header_line.startswith("Box "):
                    box, folder, raw = parse_box_folder_line(header_line)
                    desc, next_index = join_paragraph(lines, index + 1)
                    entries.append(
                        make_row(
                            project=project,
                            entry_type="folder_reference",
                            page_number=page_number,
                            subsection=letter,
                            collection_name=current_collection,
                            series=current_series,
                            box=box,
                            folder_or_item_title=folder,
                            description=desc,
                            raw_entry_text=f"{raw} {desc}".strip(),
                        )
                    )
                    index = next_index
                else:
                    collection_name, series, box, folder = extract_collection_parts(
                        header_line
                    )
                    current_collection = collection_name or header_line
                    current_series = series
                    desc, next_index = join_paragraph(lines, index + 1)
                    while next_index < len(lines) and is_subsection_line(
                        lines[next_index]
                    ):
                        break
                    while next_index < len(lines) and lines[next_index].startswith('"'):
                        quote, next_index = join_paragraph(lines, next_index)
                        folder = f"{folder} {quote}".strip() if folder else quote
                        if next_index < len(lines) and not lines[next_index].startswith(
                            "Box "
                        ):
                            extra, next_index = join_paragraph(lines, next_index)
                            desc = f"{desc} {extra}".strip()
                    entries.append(
                        make_row(
                            project=project,
                            entry_type="collection_reference",
                            page_number=page_number,
                            subsection=letter,
                            collection_name=current_collection,
                            series=current_series,
                            box=box,
                            folder_or_item_title=folder,
                            description=desc,
                            raw_entry_text=f"{header_line} {desc}".strip(),
                        )
                    )
                    index = next_index
            continue

        if in_suggested_reading:
            if not line:
                index += 1
                continue
            reading_batch = iter_reading_entries(lines, index)
            if not reading_batch:
                index += 1
                continue
            for desc, _ in reading_batch:
                entries.append(
                    make_row(
                        project=project,
                        entry_type="suggested_reading",
                        page_number=page_number,
                        subsection=current_subsection,
                        bibliographic_citation=desc,
                        raw_entry_text=desc,
                    )
                )
            index = reading_batch[-1][1]
            continue

        if not current_subsection:
            if not PROJECT_HEADER_RE.match(line):
                intro_parts.append(line)
            index += 1
            continue

        if line.startswith("Box ") or BOX_ONLY_RE.match(line):
            if BOX_ONLY_RE.match(line):
                box = BOX_ONLY_RE.match(line).group(1)
                folder = ""
                next_index = index + 1
                if next_index < len(lines) and not lines[next_index].startswith("Box "):
                    folder, next_index = join_paragraph(lines, next_index)
                entries.append(
                    make_row(
                        project=project,
                        entry_type="folder_reference",
                        page_number=page_number,
                        subsection=current_subsection,
                        collection_name=current_collection,
                        series=current_series,
                        box=box,
                        folder_or_item_title=folder,
                        raw_entry_text=f"Box {box} {folder}".strip(),
                    )
                )
                index = next_index
                continue

            box, folder, raw = parse_box_folder_line(line)
            desc, next_index = join_paragraph(lines, index + 1)
            entries.append(
                make_row(
                    project=project,
                    entry_type="folder_reference",
                    page_number=page_number,
                    subsection=current_subsection,
                    collection_name=current_collection,
                    series=current_series,
                    box=box,
                    folder_or_item_title=folder,
                    description=desc,
                    raw_entry_text=f"{raw} {desc}".strip(),
                )
            )
            index = next_index
            continue

        if current_collection and re.match(r"^See (also |Chapter |Project )", line, re.I):
            entries.append(
                make_row(
                    project=project,
                    entry_type="cross_reference",
                    page_number=page_number,
                    subsection=current_subsection,
                    related_project_reference=line,
                    raw_entry_text=line,
                )
            )
            index += 1
            continue

        if (
            current_collection
            and not line.startswith("Box ")
            and len(line) > 3
            and '"' in line
            and index + 1 < len(lines)
            and lines[index + 1].startswith('"')
        ):
            folder_title = line
            index += 1
            while index < len(lines) and lines[index].startswith('"'):
                folder_title += " " + lines[index]
                index += 1
            entries.append(
                make_row(
                    project=project,
                    entry_type="folder_reference",
                    page_number=page_number,
                    subsection=current_subsection,
                    collection_name=current_collection,
                    folder_or_item_title=folder_title,
                    raw_entry_text=folder_title,
                )
            )
            continue

        desc, next_index = join_paragraph(lines, index)
        if desc:
            entries.append(
                make_row(
                    project=project,
                    entry_type="subsection_narrative",
                    page_number=page_number,
                    subsection=current_subsection,
                    collection_name=current_collection,
                    description=desc,
                    student_project_note=desc if "student" in desc.lower() else "",
                    raw_entry_text=desc,
                )
            )
        index = next_index

    if intro_parts:
        intro_text = " ".join(intro_parts).strip()
        entries.insert(
            0,
            make_row(
                project=project,
                entry_type="project_intro",
                page_number=page_map.get(0, int(project["start_page"])),
                description=intro_text,
                student_project_note=intro_text if "student" in intro_text.lower() else "",
                raw_entry_text=intro_text,
            ),
        )

    return entries


def write_csv(entries: list[dict[str, str]]) -> None:
    OUTPUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    with OUTPUT_CSV.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(entries)


def main() -> None:
    pdf_path = download_pdf()
    pages = extract_pages(pdf_path)
    projects = parse_projects(pages)

    all_entries: list[dict[str, str]] = []
    manifest_projects: list[dict[str, object]] = []

    for project in projects:
        number = int(project["number"])
        if number not in WWII_PROJECT_NUMBERS:
            continue
        entries = parse_project_entries(project)
        all_entries.extend(entries)
        by_type: dict[str, int] = {}
        by_category: dict[str, int] = {}
        for entry in entries:
            by_type[entry["entry_type"]] = by_type.get(entry["entry_type"], 0) + 1
            by_category[entry["record_category"]] = (
                by_category.get(entry["record_category"], 0) + 1
            )
        manifest_projects.append(
            {
                "project_number": number,
                "project_title": project["title"],
                "wwii_scope_note": WWII_PROJECT_NUMBERS[number],
                "entry_count": len(entries),
                "entries_by_type": by_type,
                "entries_by_category": by_category,
            }
        )
        print(f"Project {number}: {project['title'][:60]} -> {len(entries)} entries")

    write_csv(all_entries)
    manifest = {
        "source_url": SOURCE_URL,
        "pdf_path": str(PDF_CACHE.relative_to(PROJECT_ROOT)),
        "wwii_projects_included": list(WWII_PROJECT_NUMBERS.keys()),
        "reading_only_subsections": [f"{n}{l}" for n, l in sorted(READING_SUBSECTIONS)],
        "projects": manifest_projects,
        "total_entries": len(all_entries),
        "entries_by_category": {
            category: sum(1 for e in all_entries if e["record_category"] == category)
            for category in sorted({e["record_category"] for e in all_entries})
        },
    }
    MANIFEST_JSON.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"\nWrote {len(all_entries)} entries to {OUTPUT_CSV}")


if __name__ == "__main__":
    main()
#!/usr/bin/env python3
"""Extract WWII-focused entries from the Eisenhower Library German Language Guide."""

from __future__ import annotations

import csv
import json
import re
import urllib.request
from pathlib import Path

import fitz

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_URL = (
    "https://www.eisenhowerlibrary.gov/sites/default/files/file/German_Language_Guide.pdf"
)
PDF_CACHE = (
    PROJECT_ROOT
    / "contentrepository/EisenhowerPresidentialLibrarySubjectGuides/German_Language_Guide.pdf"
)
OUTPUT_CSV = (
    PROJECT_ROOT
    / "contentrepository/indexes/eisenhower_german_language_guide_wwii.csv"
)
MANIFEST_JSON = (
    PROJECT_ROOT
    / "contentrepository/indexes/eisenhower_german_language_guide_wwii_manifest.json"
)

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

# Whole projects whose primary focus is World War II (1939-1945) or wartime Germany.
WWII_PROJECT_NUMBERS: dict[int, str] = {
    13: "Illustrierter Beobachter (Nazi periodical)",
    14: "Interviews and interrogations of high-ranking German officials",
    15: "Psychological warfare against Nazi Germany",
    16: "The Grand Alliance during World War II",
    17: "SHAEF Selected Records, 1943-45",
    18: "Food shortages in Germany (end of WWII / immediate aftermath)",
    19: "Operation Crossbow (V-1/V-2)",
    21: "OSS intelligence reports on Germany and Austria during WWII",
    22: "Adolf Hitler",
}

# Subsections excluded from otherwise-WWII projects (postwar / non-WWII content).
EXCLUDED_SUBSECTIONS: set[tuple[int, str]] = {
    (16, "B"),  # 1960 Eisenhower interview with Herbert Feis
    (18, "B"),  # 1947 FitzGerald food report
}

CSV_FIELDS = [
    "source_title",
    "source_pdf_url",
    "project_number",
    "project_title",
    "wwii_scope_note",
    "subsection_letter",
    "entry_type",
    "collection_name",
    "series",
    "box",
    "folder_or_item_title",
    "description",
    "language_materials",
    "student_project_note",
    "suggested_reading",
    "related_project_reference",
    "page_number_in_guide",
    "raw_entry_text",
]

PROJECT_HEADER_RE = re.compile(r"^(\d{1,2})\.\s*$")
SUBSECTION_ONLY_RE = re.compile(r"^([A-Z])\.\s*$")
SUBSECTION_RE = re.compile(r"^([A-Z])\.\s+(.+)$")
INTRO_START_RE = re.compile(
    r"^(This project|Students|The Office|On May|The presence|"
    r"The Library contains|Consists of|These folders|NSC \d|"
    r"Germany and Europe)",
    re.I,
)
BOX_FOLDER_RE = re.compile(
    r"^Box(?:es)?\s+([\d,\-\sA-Za-z]+?)(?:,\s*File\s+Folder[s]?:\s*(.+)|,\s*(.+)|\s+(.+))?$",
    re.I,
)
BOX_ONLY_RE = re.compile(r"^Box\s+(\d+[A-Za-z]?)\s*$", re.I)
SUGGESTED_READING_RE = re.compile(r"^Suggested [Rr]eading:?\s*$", re.I)
ORAL_HISTORY_RE = re.compile(r"^Oral History\b", re.I)
SKIP_LINE_RE = re.compile(r"^Page\s+\d+\s*$", re.I)


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

    for line, page_number in line_page:
        if line == "GERMAN LANGUAGE GUIDE":
            continue

        project_match = PROJECT_HEADER_RE.match(line)
        if project_match:
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
            line.isupper()
            and len(line) < 120
            and not line.startswith("Box ")
            and not SUBSECTION_RE.match(line)
            and not SUBSECTION_ONLY_RE.match(line)
            and not INTRO_START_RE.match(line)
            and not SUGGESTED_READING_RE.match(line)
        )
        if not body_lines and title_continuation:
            title_parts.append(line)
        elif (
            not title_parts
            and not body_lines
            and not SUBSECTION_RE.match(line)
            and not SUBSECTION_ONLY_RE.match(line)
            and not INTRO_START_RE.match(line)
            and not SUGGESTED_READING_RE.match(line)
            and not line.startswith("Box ")
        ):
            title_parts.append(line)
        else:
            body_lines.append((line, page_number))

    if current:
        current["title"] = " ".join(title_parts).strip()
        current["body_lines"] = body_lines
        projects.append(current)

    return projects


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
    if re.search(r"\bin German\b", text, re.I):
        notes.append("German language materials")
    if re.search(r"translation", text, re.I):
        notes.append("Includes translations")
    if re.search(r"leaflet", text, re.I):
        notes.append("Propaganda leaflets")
    if re.search(r"newspaper", text, re.I):
        notes.append("Newspapers")
    return "; ".join(notes)


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
    suggested_reading: str = "",
    related_project_reference: str = "",
    raw_entry_text: str = "",
) -> dict[str, str]:
    number = int(project["number"])
    return {
        "source_title": "German Language Guide",
        "source_pdf_url": SOURCE_URL,
        "project_number": str(number),
        "project_title": str(project["title"]),
        "wwii_scope_note": WWII_PROJECT_NUMBERS.get(number, ""),
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
        "suggested_reading": suggested_reading,
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


def is_subsection_line(line: str) -> tuple[str, str] | None:
    only = SUBSECTION_ONLY_RE.match(line)
    if only:
        return only.group(1), ""
    match = SUBSECTION_RE.match(line)
    if match:
        return match.group(1), match.group(2).strip()
    return None


def split_shaef_box_items(text: str) -> list[tuple[str, str, str]]:
    items: list[tuple[str, str, str]] = []
    for part in re.split(r"(?=\b[A-Z]\.\s+Box\s+)", text):
        part = part.strip()
        if not part:
            continue
        match = re.match(r"^([A-Z])\.\s+Box\s+(\d+[A-Za-z]?),\s*(.+)$", part, re.I)
        if match:
            items.append((match.group(1), match.group(2), match.group(3).strip()))
    return items


def parse_oss_box_line(line: str) -> tuple[str, str] | None:
    match = re.match(r"^Box\s+(\d+)\s*[-–—]\s*(.+)$", line, re.I)
    if match:
        return match.group(1).strip(), match.group(2).strip()
    match = re.match(r"^Box\s+(\d+)\s+(.+)$", line, re.I)
    if match:
        return match.group(1).strip(), match.group(2).strip()
    return None


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
            index += 1
            continue

        subsection_match = is_subsection_line(line)
        if subsection_match and not in_suggested_reading:
            letter, remainder = subsection_match
            if (number, letter) in EXCLUDED_SUBSECTIONS:
                current_subsection = ""
                index += 1
                while index < len(lines):
                    if is_subsection_line(lines[index]) or SUGGESTED_READING_RE.match(
                        lines[index]
                    ):
                        break
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
                        raw_entry_text=intro_text,
                    )
                )
                intro_parts.clear()

            if number == 17 and remainder:
                shaef_items = split_shaef_box_items(line)
                if not shaef_items:
                    box_match = re.match(
                        r"^Box\s+(\d+[A-Za-z]?),\s*(.+)$", remainder, re.I
                    )
                    if box_match:
                        shaef_items = [
                            (letter, box_match.group(1), box_match.group(2).strip())
                        ]
                for sub_letter, box, title in shaef_items:
                    entries.append(
                        make_row(
                            project=project,
                            entry_type="shaef_box_item",
                            page_number=page_number,
                            subsection=sub_letter,
                            collection_name="SHAEF Selected Records, 1943-45",
                            box=box,
                            folder_or_item_title=title,
                            description=title,
                            raw_entry_text=f"{sub_letter}. Box {box}, {title}",
                        )
                    )
                index += 1
                continue

            if remainder:
                current_collection = remainder
                desc, next_index = join_paragraph(lines, index + 1)
                while next_index < len(lines) and is_subsection_line(lines[next_index]):
                    break
                entries.append(
                    make_row(
                        project=project,
                        entry_type="collection_reference",
                        page_number=page_number,
                        subsection=letter,
                        collection_name=remainder,
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
                    entries.append(
                        make_row(
                            project=project,
                            entry_type="box_item",
                            page_number=page_number,
                            subsection=letter,
                            box=box,
                            folder_or_item_title=folder,
                            raw_entry_text=raw,
                        )
                    )
                    index += 1
                else:
                    current_collection = header_line
                    desc, next_index = join_paragraph(lines, index + 1)
                    while next_index < len(lines) and is_subsection_line(lines[next_index]):
                        break
                    entries.append(
                        make_row(
                            project=project,
                            entry_type="collection_reference",
                            page_number=page_number,
                            subsection=letter,
                            collection_name=header_line,
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
            desc, next_index = join_paragraph(lines, index)
            entries.append(
                make_row(
                    project=project,
                    entry_type="suggested_reading",
                    page_number=page_number,
                    subsection=current_subsection,
                    suggested_reading=desc,
                    raw_entry_text=desc,
                )
            )
            index = next_index
            continue

        if not current_subsection:
            if (
                number == 21
                and line.startswith("Dwight D. Eisenhower Library Collection")
            ):
                desc, next_index = join_paragraph(lines, index)
                entries.append(
                    make_row(
                        project=project,
                        entry_type="collection_reference",
                        page_number=page_number,
                        collection_name=line,
                        series="Series VII, European Advisory Committee Material",
                        description=desc,
                        raw_entry_text=f"{line} {desc}".strip(),
                    )
                )
                index = next_index
                continue
            oss_box = parse_oss_box_line(line)
            if number == 21 and oss_box:
                box, title = oss_box
                title = re.sub(r"^[-–—]\s*", "", title).strip()
                next_i = index + 1
                if next_i < len(lines):
                    nxt = lines[next_i]
                    if (
                        not parse_oss_box_line(nxt)
                        and not is_subsection_line(nxt)
                        and not SUGGESTED_READING_RE.match(nxt)
                        and not nxt.startswith("Box ")
                        and (
                            title.rstrip().endswith("-")
                            or not title.rstrip().endswith(".")
                        )
                    ):
                        title += " " + nxt
                        next_i += 1
                entries.append(
                    make_row(
                        project=project,
                        entry_type="oss_report",
                        page_number=page_number,
                        collection_name=(
                            "Dwight D. Eisenhower Library Collection of 20th Century "
                            "Military Records, Series VII, European Advisory Committee Material"
                        ),
                        series="Series VII, European Advisory Committee Material",
                        box=box,
                        folder_or_item_title=title,
                        description=title,
                        raw_entry_text=f"Box {box} -- {title}",
                    )
                )
                index = next_i
                continue
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
                    raw_entry_text=raw,
                )
            )
            index += 1
            continue

        if current_collection and re.match(r"^See (also |Chapter )", line, re.I):
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
            entry_type = "subsection_narrative"
            if number == 13 and "bound volumes" in desc.lower():
                entry_type = "periodical_holdings"
            entries.append(
                make_row(
                    project=project,
                    entry_type=entry_type,
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
        entry_type = "project_intro"
        if number == 13 and "bound volumes" in intro_text.lower():
            entry_type = "periodical_holdings"
        entries.insert(
            0,
            make_row(
                project=project,
                entry_type=entry_type,
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
        for entry in entries:
            by_type[entry["entry_type"]] = by_type.get(entry["entry_type"], 0) + 1
        manifest_projects.append(
            {
                "project_number": number,
                "project_title": project["title"],
                "wwii_scope_note": WWII_PROJECT_NUMBERS[number],
                "entry_count": len(entries),
                "entries_by_type": by_type,
            }
        )
        print(f"Project {number}: {project['title'][:60]} -> {len(entries)} entries")

    write_csv(all_entries)
    manifest = {
        "source_url": SOURCE_URL,
        "pdf_path": str(PDF_CACHE.relative_to(PROJECT_ROOT)),
        "wwii_projects_included": list(WWII_PROJECT_NUMBERS.keys()),
        "excluded_subsections": [f"{n}{l}" for n, l in sorted(EXCLUDED_SUBSECTIONS)],
        "projects": manifest_projects,
        "total_entries": len(all_entries),
    }
    MANIFEST_JSON.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"\nWrote {len(all_entries)} entries to {OUTPUT_CSV}")


if __name__ == "__main__":
    main()
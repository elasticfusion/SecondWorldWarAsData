#!/usr/bin/env python3
"""Extract Eisenhower Presidential Library WWII subject guide PDFs into CSV."""

from __future__ import annotations

import csv
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

import fitz

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_URL = "https://www.eisenhowerlibrary.gov/research/online-documents/subject-guides"
BASE_URL = "https://www.eisenhowerlibrary.gov"
PDF_CACHE_DIR = (
    PROJECT_ROOT / "contentrepository/EisenhowerPresidentialLibrarySubjectGuides"
)
OUTPUT_CSV = (
    PROJECT_ROOT / "contentrepository/indexes/eisenhower_wwii_subject_guides.csv"
)
MANIFEST_JSON = (
    PROJECT_ROOT / "contentrepository/indexes/eisenhower_wwii_subject_guides_manifest.json"
)

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
DOWNLOAD_DELAY_SEC = 0.75

# Fallback URLs when the WWII list link is empty or broken.
URL_FALLBACKS: dict[str, str] = {
    "Nazi War Crimes and Japanese Imperial Government Records": (
        "/sites/default/files/2019-07/Nazi_Japanese_Records.pdf"
    ),
}

WWII_GUIDES: list[tuple[str, str]] = [
    (
        "African Americans in World War II",
        "/sites/default/files/2020-10/African%20Americans%20in%20World%20War%20II.pdf",
    ),
    (
        "Algeria, Tunisia, Morocco",
        "/sites/default/files/research/subject-guides/pdf/algeria-tunisia-morocco.pdf",
    ),
    (
        "Ardennes / Battle of the Bulge",
        "/sites/default/files/research/subject-guides/pdf/ardennes-battle-of-the-bulge.pdf",
    ),
    (
        "Berlin 1945-1962",
        "/sites/default/files/research/subject-guides/pdf/berlin-1945-1962.pdf",
    ),
    (
        "Churchill, Winston",
        "/sites/default/files/research/subject-guides/pdf/churchill-winston.pdf",
    ),
    (
        "D-Day, The Invasion",
        "/sites/default/files/research/subject-guides/pdf/dday-invasion.pdf",
    ),
    (
        "D-Day, The Planning of Overlord",
        "/sites/default/files/research/subject-guides/pdf/dday-planning-overlord.pdf",
    ),
    (
        "de Gaulle, Charles",
        "/sites/default/files/research/subject-guides/pdf/de-gaulle-charles.pdf",
    ),
    (
        "Eisenhower's World War II Train",
        "/sites/default/files/research/subject-guides/pdf/eisenhower-train.pdf",
    ),
    (
        "Germany, Occupation by the U.S. Army",
        "/sites/default/files/research/subject-guides/pdf/germany-occupation-by-us.pdf",
    ),
    (
        "Hitler Youth, Volkstrum",
        "/sites/default/files/research/subject-guides/pdf/hitler-youth-volkstrum.pdf",
    ),
    (
        "Holocaust",
        "/sites/default/files/research/subject-guides/pdf/holocaust.pdf",
    ),
    (
        "Italian Front",
        "/sites/default/files/research/subject-guides/pdf/italian-front.pdf",
    ),
    (
        "Italy, Monte Cassino",
        "/sites/default/files/research/subject-guides/pdf/monte-cassino-italy.pdf",
    ),
    (
        "Japan, World War II Against",
        "/sites/default/files/2020-09/World%20War%20II%20Against%20Japan.pdf",
    ),
    (
        "Japanese Americans (Nisei) During WWII",
        "/sites/default/files/research/subject-guides/pdf/japanese-americans--nisei.pdf",
    ),
    (
        "Katyn Forest Massacre",
        "/sites/default/files/2020-11/Katyn%20Forest%20Massacre.pdf",
    ),
    (
        "Mulberry, The Artificial Harbor",
        "/sites/default/files/research/subject-guides/pdf/mulberry-artificial-harbor.pdf",
    ),
    (
        "Nazi War Crimes and Japanese Imperial Government Records",
        "/sites/default/files/research/subject-guides/pdf/nazi-japanese-records.pdf",
    ),
    (
        "North African Campaign of World War II",
        "/sites/default/files/research/subject-guides/pdf/north-african-campaign.pdf",
    ),
    (
        "North African Relations with de Gaulle and Vichy",
        "/sites/default/files/research/subject-guides/pdf/north-african-relations-with-degaulle-or-vichy.pdf",
    ),
    (
        "Operation Husky",
        "/sites/default/files/research/subject-guides/pdf/operation-husky.pdf",
    ),
    (
        "Operation Market Garden",
        "/sites/default/files/research/subject-guides/pdf/operation-market-garden.pdf",
    ),
    (
        "Operation Market Garden Reports",
        "/sites/default/files/research/subject-guides/pdf/operation-market-garden-reports.pdf",
    ),
    (
        "Operation Overlord",
        "/sites/default/files/research/subject-guides/pdf/operation-overlord.pdf",
    ),
    (
        "Operation Pastorus",
        "/sites/default/files/research/subject-guides/pdf/operation-pastorus.pdf",
    ),
    (
        "Operation Shingle, Anzio",
        "/sites/default/files/research/subject-guides/pdf/operation-shingle-anzio.pdf",
    ),
    (
        "Pearl Harbor Attack",
        "/sites/default/files/2020-11/Pearl%20Harbor%20Attack.pdf",
    ),
    (
        "POWs and MIAs",
        "/sites/default/files/research/subject-guides/pdf/pow-mia.pdf",
    ),
]

CSV_FIELDS = [
    "subject_guide_title",
    "subject_guide_pdf_url",
    "compiled_by",
    "guide_date",
    "section",
    "entry_type",
    "entry_number",
    "collection_name",
    "creator",
    "date_range",
    "series",
    "box",
    "folder_or_item_title",
    "description",
    "access_notes",
    "finding_aid_type",
    "item_id",
    "page_number_in_guide",
    "raw_entry_text",
]

SECTION_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("introduction", re.compile(r"^INTRODUCTION\s*$", re.I)),
    ("table_of_contents", re.compile(r"^Table of Contents\s*$", re.I)),
    ("overview_of_collections", re.compile(r"^Overview of Collections\s*$", re.I)),
    ("detailed_folder_lists", re.compile(r"^Detailed Folder Lists\s*$", re.I)),
    ("oral_history_transcripts", re.compile(r"^Oral History Transcripts\s*$", re.I)),
    (
        "audiovisual_still_photographs",
        re.compile(r"^Audiovisual:\s*Still Photographs\s*$", re.I),
    ),
    (
        "audiovisual_audio_recordings",
        re.compile(r"^Audiovisual:\s*Audio Recordings\s*$", re.I),
    ),
    (
        "audiovisual_motion_picture_film",
        re.compile(
            r"^(?:Audiovisual:\s*)?Motion Picture Film\s*$",
            re.I,
        ),
    ),
    (
        "select_bibliography",
        re.compile(r"^Select Bibliography(?:\s+of Print Materials)?\s*$", re.I),
    ),
    ("books", re.compile(r"^Books\s*$", re.I)),
    ("vertical_file", re.compile(r"^Vertical File\s*$", re.I)),
]

PAGE_MARKER_RE = re.compile(r"^Page\s+\d+\s*$", re.I)
BOX_RE = re.compile(r"^Box(?:es)?\s+(\d+[A-Za-z]?)\s*$", re.I)
BOX_INLINE_RE = re.compile(
    r"^Box(?:es)?\s+([\d,\-\sA-Za-z=]+?)\s*[:=\-\u2013]\s*(.+)$",
    re.I,
)
BOX_RANGE_INLINE_RE = re.compile(
    r"^Box(?:es)?\s+([\d,\-\s]+)\s*[\-=\u2013]?\s*(.+)$",
    re.I,
)
NUMBERED_ENTRY_RE = re.compile(r"^(\d+)\.\s+(.+)$")
NUMBERED_ONLY_RE = re.compile(r"^(\d{1,2})\.\s*$")
PAPERS_DATE_TAIL_RE = re.compile(
    r"^(.+):\s*(Papers|Records),\s*([\d\-]+(?:-\d+)?)\s*\.?\s*$",
    re.I,
)
COLLECTION_SUFFIX_RE = re.compile(
    r"^(Papers|Records|Collection|Diaries|Post-Presidential Papers|"
    r"Pre-Presidential Papers|Records as President|Small Manuscript Collections|"
    r"Collection of .+|WORLD WAR II PARTICIPANTS AND CONTEMPORARIES:  Papers)",
    re.I,
)
COLLECTION_LINE_RE = re.compile(
    r"^(.+?):\s+"
    r"(Papers|Records|Collection|Diaries|Post-Presidential Papers|"
    r"Pre-Presidential Papers|Records as President[^,]*|"
    r"Small Manuscript Collections|Collection of[^,]+|"
    r"WORLD WAR II PARTICIPANTS AND CONTEMPORARIES:\s+Papers)"
    r"(?:,\s*([\d\-]+(?:-\d+)?))?\s*$",
    re.I,
)
EISENHOWER_PAPERS_RE = re.compile(
    r"^Eisenhower,\s+Dwight D\.:\s+"
    r"(Pre-Presidential Papers|Post-Presidential Papers|Records as President[^,]*)"
    r"(?:,\s*([\d\-]+(?:-\d+)?))?\s*$",
    re.I,
)
LIBRARY_COLLECTION_RE = re.compile(
    r"^Dwight D\. Eisenhower Library:\s+(.+)$",
    re.I,
)
LIBRARY_MILITARY_RECORDS_RE = re.compile(
    r"^Dwight D\. Eisenhower Library Collection of 20th Century Military Records\s*$",
    re.I,
)
PAPERS_OF_RE = re.compile(r"^Papers of (.+)$", re.I)
DDE_LOOSE_RE = re.compile(
    r"^Dwight D\. Eisenhower(?::|,)\s+(.+)$",
    re.I,
)
GENERIC_COLLECTION_RE = re.compile(
    r"^(.+?):\s+"
    r"(Papers(?:\s+As\s+President[^,]*)?|Records(?:\s+as\s+President[^,]*)?|"
    r"Collection(?:\s+of\s+World\s+War\s+II\s+Documents)?|"
    r"Conference\s+Proceedings|Drafts\s+and\s+Other\s+Materials[^,]*)"
    r"(?:,\s*([\d\-]+(?:-\d+)?))?\.?\s*$",
    re.I,
)
SMITH_COLLECTION_RE = re.compile(
    r"^(.+),\s+Collection of World War II Documents"
    r"(?:,?\s*([\d\-]+(?:-\d+)?))?\.?\s*$",
    re.I,
)
US_ARMY_UNIT_RE = re.compile(r"^US Army Unit Records\b", re.I)
SHAEF_COLLECTION_RE = re.compile(r"^(?:Records of )?SHAEF\b", re.I)
GUIDE_TITLE_RE = re.compile(
    r"^(?:Charles DeGaulle|Italian Front in World War II|Monte Cassino|"
    r"Operation Pastorius|Reports on Operation Market-Garden|"
    r"Volkstrum/Hitler Youth in later stages of World War II)\s*$",
    re.I,
)
LOOSE_SERIES_RE = re.compile(
    r"^(?:Series\s+[IVX\d\-=]+|International Series|DDE Diary Series|"
    r"Dulles-Herter Series|NSC Series|Administration Series|"
    r"Confidential Files|Pre-Presidential Papers contain)\s*[-=]?\s*$",
    re.I,
)
NUMBERED_EISENHOWER_RE = re.compile(
    r"^(\d+)\.\s+Eisenhower,\s+Dwight D\.:\s+"
    r"(Pre-Presidential Papers|Post-Presidential Papers)"
    r"(?:,\s*([\d\-]+(?:-\d+)?))?\s*$",
    re.I,
)
AUDIOVISUAL_ID_RE = re.compile(r"^(EL-[A-Z]+\d+-\d+)\s+(.+)$", re.I)
ORAL_HISTORY_RE = re.compile(
    r"^\*?\s*(.+?)\s*\(OH-(\d+)\)(?:\s+.*)?$",
    re.I,
)
ACCESS_NOTES_RE = re.compile(
    r"(Open for research|Preliminary inventory|Shelf list|Accession registration sheet|"
    r"Portions undergoing pending processing|Items closed in accordance)",
    re.I,
)
DATE_RANGE_RE = re.compile(r"\b(\d{4}(?:-\d{2,4})?)\b")
COMPILED_BY_RE = re.compile(r"Compiled by\s+(.+?)(?:\n|Dwight)", re.I | re.S)
GUIDE_DATE_RE = re.compile(
    r"(January|February|March|April|May|June|July|August|September|October|November|December)"
    r"\s+(\d{4})",
    re.I,
)
SERIES_LINE_RE = re.compile(
    r"^(Principal File|Subject File|Butcher Diary Series|Telephone Calls Series|"
    r"JFD Chronological Series|President's Personal File|Signature File|"
    r"\d{4}\s+Principal File(?:\s+Series)?|Series\s+[IVX\d]+(?:,\s*.+)?|"
    r"International Series|DDE Diary Series|Dulles-Herter Series|NSC Series|"
    r"Administration Series|Confidential Files)\s*[-=]?\s*$",
    re.I,
)
SUBUNIT_HEADER_RE = re.compile(
    r"^(\d+(?:st|nd|rd|th)\s+.+|(?:\d+th\s+)?(?:Infantry|Armored|Airborne|Cavalry|"
    r"Tank(?:\s+Destroyer)?|Parachute|Glider|Medical|Signal|Ordnance|Quartermaster)\s+.+|"
    r"Campaign Index.+|U\.S\.\s+WAR DEPARTMENT.+)$",
    re.I,
)
SKIP_LINE_RE = re.compile(
    r"^(Section|Page\s|OO$|DWIGHT D\. EISENHOWER LIBRARY|Abilene, Kansas|"
    r"A Guide(?:\s+To|\s+to)\s+Historical Holdings|in the|Eisenhower Library|"
    r"Historical Holdings|These topical subject guides|Also see|For further information|"
    r"RESOURCES IN THE|RELATING TO|http://|eisenhower\.|@copyright|Copyright:)",
    re.I,
)
CREATOR_NAME_RE = re.compile(
    r"([A-Z][A-Z'\.\-]*(?:,\s+[A-Z][A-Za-z' \.\-]+)+):"
)


@dataclass
class ParserState:
    section: str = "body"
    collection_name: str = ""
    creator: str = ""
    date_range: str = ""
    series: str = ""
    box: str = ""
    subunit: str = ""
    pending_description: list[str] = field(default_factory=list)
    pending_oral_history: dict[str, str] | None = None


def fetch_html() -> str:
    request = urllib.request.Request(SOURCE_URL, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=120) as response:
        return response.read().decode("utf-8", errors="replace")


def slugify(title: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", title.lower()).strip("-")
    return slug[:120] or "guide"


def download_pdf(url: str, dest: Path) -> bool:
    if dest.exists() and dest.stat().st_size > 1000:
        return True
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            data = response.read()
    except urllib.error.URLError:
        return False
    if len(data) < 1000:
        return False
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)
    return True


def resolve_pdf_path(title: str, path: str) -> tuple[str, Path]:
    urls = [BASE_URL + path]
    if title in URL_FALLBACKS:
        urls.append(BASE_URL + URL_FALLBACKS[title])
    filename = Path(urllib.parse.unquote(path)).name
    if not filename.lower().endswith(".pdf"):
        filename = slugify(title) + ".pdf"
    dest = PDF_CACHE_DIR / filename
    for url in urls:
        if download_pdf(url, dest):
            return url, dest
        time.sleep(DOWNLOAD_DELAY_SEC)
    raise RuntimeError(f"Failed to download PDF for {title}")


def extract_pages(pdf_path: Path) -> list[tuple[int, str]]:
    doc = fitz.open(pdf_path)
    pages: list[tuple[int, str]] = []
    for index, page in enumerate(doc):
        pages.append((index + 1, page.get_text()))
    return pages


def clean_line(line: str) -> str:
    line = line.replace("\u00a0", " ").replace("\uf0af", "").replace("\uf0b7", "")
    line = re.sub(r"\s+", " ", line).strip()
    return line


def normalize_lines(text: str) -> list[str]:
    return [clean_line(line) for line in text.splitlines() if clean_line(line)]


def parse_header_metadata(full_text: str) -> tuple[str, str]:
    compiled_by = ""
    guide_date = ""
    compiled_match = COMPILED_BY_RE.search(full_text)
    if compiled_match:
        compiled_by = re.sub(r"\s+", " ", compiled_match.group(1)).strip(" .")
    date_match = GUIDE_DATE_RE.search(full_text[:4000])
    if date_match:
        guide_date = f"{date_match.group(1)} {date_match.group(2)}"
    return compiled_by, guide_date


def split_collection_header(line: str) -> tuple[str, str, str, str] | None:
    match = PAPERS_DATE_TAIL_RE.match(line)
    if match:
        prefix = match.group(1).strip()
        if re.match(r"^(\d{4}|Commanding|Normandy)", prefix) and "," not in line.split(":", 1)[0]:
            return None
        name_matches = CREATOR_NAME_RE.findall(line)
        creator = name_matches[0] if name_matches else line.split(":", 1)[0].strip()
        collection = line.rstrip(".")
        return collection, creator, match.group(3) or "", match.group(2).strip()
    match = COLLECTION_LINE_RE.match(line)
    if match:
        creator = match.group(1).strip()
        collection = f"{creator}: {match.group(2).strip()}"
        if match.group(3):
            collection += f", {match.group(3)}"
        return collection, creator, match.group(3) or "", match.group(2).strip()
    match = EISENHOWER_PAPERS_RE.match(line)
    if match:
        creator = "Eisenhower, Dwight D."
        collection = f"{creator}: {match.group(1).strip()}"
        if match.group(2):
            collection += f", {match.group(2)}"
        return collection, creator, match.group(2) or "", match.group(1).strip()
    match = LIBRARY_COLLECTION_RE.match(line)
    if match:
        creator = "Dwight D. Eisenhower Library"
        collection = line
        return collection, creator, "", match.group(1).strip()
    match = LIBRARY_MILITARY_RECORDS_RE.match(line)
    if match:
        creator = "Dwight D. Eisenhower Library"
        collection = line
        return collection, creator, "", "Collection of 20th Century Military Records"
    match = PAPERS_OF_RE.match(line)
    if match:
        creator = match.group(1).strip()
        collection = f"Papers of {creator}"
        return collection, creator, "", "Papers"
    match = DDE_LOOSE_RE.match(line)
    if match:
        creator = "Dwight D. Eisenhower"
        suffix = match.group(1).strip().rstrip(".")
        collection = f"{creator}: {suffix}"
        dates = DATE_RANGE_RE.findall(suffix)
        return collection, creator, dates[0] if dates else "", suffix
    match = GENERIC_COLLECTION_RE.match(line)
    if match:
        creator = match.group(1).strip()
        collection = line.rstrip(".")
        return collection, creator, match.group(3) or "", match.group(2).strip()
    match = SMITH_COLLECTION_RE.match(line)
    if match:
        creator = match.group(1).strip()
        collection = line.rstrip(".")
        return collection, creator, match.group(2) or "", "Collection of World War II Documents"
    if US_ARMY_UNIT_RE.match(line):
        return line, "US Army Unit Records", "", "Unit Records"
    if SHAEF_COLLECTION_RE.match(line):
        return line, "SHAEF", "", "Records"
    return None


def flush_description(state: ParserState) -> str:
    description = " ".join(state.pending_description).strip()
    state.pending_description.clear()
    return description


def make_entry(
    *,
    guide_title: str,
    guide_url: str,
    compiled_by: str,
    guide_date: str,
    page_number: int,
    state: ParserState,
    entry_type: str,
    entry_number: str = "",
    folder_or_item_title: str = "",
    description: str = "",
    access_notes: str = "",
    finding_aid_type: str = "",
    item_id: str = "",
    raw_entry_text: str = "",
) -> dict[str, str]:
    collection_name = state.collection_name
    if state.subunit and folder_or_item_title:
        collection_name = (
            f"{state.collection_name} / {state.subunit}"
            if state.collection_name
            else state.subunit
        )
    return {
        "subject_guide_title": guide_title,
        "subject_guide_pdf_url": guide_url,
        "compiled_by": compiled_by,
        "guide_date": guide_date,
        "section": state.section,
        "entry_type": entry_type,
        "entry_number": entry_number,
        "collection_name": collection_name,
        "creator": state.creator,
        "date_range": state.date_range,
        "series": state.series,
        "box": state.box,
        "folder_or_item_title": folder_or_item_title,
        "description": description,
        "access_notes": access_notes,
        "finding_aid_type": finding_aid_type,
        "item_id": item_id,
        "page_number_in_guide": str(page_number),
        "raw_entry_text": raw_entry_text,
    }


def parse_numbered_entry(
    line: str,
    guide_title: str,
    guide_url: str,
    compiled_by: str,
    guide_date: str,
    page_number: int,
    state: ParserState,
) -> dict[str, str] | None:
    match = NUMBERED_EISENHOWER_RE.match(line)
    if match:
        state.collection_name = (
            f"Eisenhower, Dwight D.: {match.group(2)}"
            + (f", {match.group(3)}" if match.group(3) else "")
        )
        state.creator = "Eisenhower, Dwight D."
        state.date_range = match.group(3) or ""
        state.series = ""
        state.box = ""
        state.subunit = ""
        return make_entry(
            guide_title=guide_title,
            guide_url=guide_url,
            compiled_by=compiled_by,
            guide_date=guide_date,
            page_number=page_number,
            state=state,
            entry_type="numbered_collection",
            entry_number=match.group(1),
            raw_entry_text=line,
        )

    match = NUMBERED_ENTRY_RE.match(line)
    if not match:
        return None

    entry_number = match.group(1)
    body = match.group(2).strip()
    access_notes = ""
    finding_aid_type = ""
    access_match = ACCESS_NOTES_RE.search(body)
    if access_match:
        start = access_match.start()
        access_notes = body[start:].strip()
        body = body[:start].strip(" .")

    for aid in (
        "Preliminary inventory",
        "Shelf list",
        "Accession registration sheet description only",
        "Portions undergoing pending processing",
    ):
        if aid.lower() in access_notes.lower():
            finding_aid_type = aid
            break

    creator = ""
    date_range = ""
    collection_name = ""
    header = split_collection_header(body)
    if header:
        collection_name, creator, date_range, _ = header
        description = body
    else:
        colon_idx = body.find(":")
        if colon_idx > 0:
            creator = body[:colon_idx].strip()
            collection_name = creator + ": " + body[colon_idx + 1 :].split(".")[0].strip()
            dates = DATE_RANGE_RE.findall(body)
            date_range = dates[0] if dates else ""
        description = body

    state.collection_name = collection_name
    state.creator = creator
    state.date_range = date_range
    state.series = ""
    state.box = ""
    state.subunit = ""

    return make_entry(
        guide_title=guide_title,
        guide_url=guide_url,
        compiled_by=compiled_by,
        guide_date=guide_date,
        page_number=page_number,
        state=state,
        entry_type="numbered_collection",
        entry_number=entry_number,
        description=description,
        access_notes=access_notes,
        finding_aid_type=finding_aid_type,
        raw_entry_text=line,
    )


def join_numbered_entry(lines: list[str], line_index: int) -> tuple[str, str, int] | None:
    line = lines[line_index]
    match = NUMBERED_ENTRY_RE.match(line)
    if match:
        return match.group(1), match.group(2).strip(), line_index + 1

    only = NUMBERED_ONLY_RE.match(line)
    if not only:
        return None

    entry_number = only.group(1)
    body_parts: list[str] = []
    next_index = line_index + 1
    while next_index < len(lines):
        nxt = lines[next_index]
        if (
            NUMBERED_ONLY_RE.match(nxt)
            or NUMBERED_ENTRY_RE.match(nxt)
            or any(p.match(nxt) for _, p in SECTION_PATTERNS)
            or PAGE_MARKER_RE.match(nxt)
        ):
            break
        body_parts.append(nxt)
        next_index += 1
    if not body_parts:
        return None
    return entry_number, " ".join(body_parts).strip(), next_index


def join_collection_header(lines: list[str], line_index: int) -> tuple[str, int] | None:
    line = lines[line_index]
    if split_collection_header(line):
        return line, line_index + 1

    if PAPERS_DATE_TAIL_RE.match(line) and re.match(r"^\d{4}", line):
        prefix_lines: list[str] = []
        back = line_index - 1
        while back >= 0 and line_index - back <= 8:
            prev = lines[back]
            if (
                SKIP_LINE_RE.match(prev)
                or GUIDE_TITLE_RE.match(prev)
                or BOX_INLINE_RE.match(prev)
                or BOX_RE.match(prev)
                or NUMBERED_ENTRY_RE.match(prev)
                or any(p.match(prev) for _, p in SECTION_PATTERNS)
            ):
                break
            prefix_lines.insert(0, prev)
            back -= 1
        combined = " ".join(prefix_lines + [line])
        if split_collection_header(combined):
            return combined, line_index + 1

    if not re.match(r"^[A-Z]", line):
        return None

    parts = [line]
    next_index = line_index + 1
    while next_index < len(lines) and next_index - line_index < 8:
        nxt = lines[next_index]
        if PAPERS_DATE_TAIL_RE.match(nxt) and re.match(r"^\d{4}", nxt):
            parts.append(nxt)
            combined = " ".join(parts)
            if split_collection_header(combined):
                return combined, next_index + 1
            break
        if (
            split_collection_header(nxt)
            or BOX_INLINE_RE.match(nxt)
            or BOX_RE.match(nxt)
            or NUMBERED_ENTRY_RE.match(nxt)
            or NUMBERED_ONLY_RE.match(nxt)
            or any(p.match(nxt) for _, p in SECTION_PATTERNS)
            or PAGE_MARKER_RE.match(nxt)
        ):
            combined = " ".join(parts)
            if PAPERS_DATE_TAIL_RE.match(combined) or split_collection_header(combined):
                return combined, next_index
            break
        parts.append(nxt)
        combined = " ".join(parts)
        if PAPERS_DATE_TAIL_RE.match(combined) or split_collection_header(combined):
            return combined, next_index + 1
        next_index += 1
    return None


def parse_guide(
    guide_title: str,
    guide_url: str,
    pages: list[tuple[int, str]],
) -> list[dict[str, str]]:
    full_text = "\n".join(text for _, text in pages)
    compiled_by, guide_date = parse_header_metadata(full_text)
    entries: list[dict[str, str]] = []
    state = ParserState()
    found_content_section = False
    pending_folder_parts: list[str] = []

    def flush_pending_folder(page_number: int) -> None:
        nonlocal pending_folder_parts
        if not pending_folder_parts or not state.collection_name:
            pending_folder_parts = []
            return
        title = " ".join(pending_folder_parts).strip()
        pending_folder_parts = []
        if not title:
            return
        entries.append(
            make_entry(
                guide_title=guide_title,
                guide_url=guide_url,
                compiled_by=compiled_by,
                guide_date=guide_date,
                page_number=page_number,
                state=state,
                entry_type="folder_item",
                folder_or_item_title=title,
                raw_entry_text=title,
            )
        )

    for page_number, page_text in pages:
        lines = normalize_lines(page_text)
        line_index = 0
        while line_index < len(lines):
            line = lines[line_index]

            if PAGE_MARKER_RE.match(line):
                line_index += 1
                continue

            section_match = next(
                (name for name, pattern in SECTION_PATTERNS if pattern.match(line)),
                None,
            )
            if section_match:
                flush_pending_folder(page_number)
                if state.pending_description and state.collection_name:
                    desc = flush_description(state)
                    entries.append(
                        make_entry(
                            guide_title=guide_title,
                            guide_url=guide_url,
                            compiled_by=compiled_by,
                            guide_date=guide_date,
                            page_number=page_number,
                            state=state,
                            entry_type="collection_overview",
                            description=desc,
                            raw_entry_text=f"{state.collection_name} {desc}".strip(),
                        )
                    )
                state.section = section_match
                state.collection_name = ""
                state.creator = ""
                state.date_range = ""
                state.series = ""
                state.box = ""
                state.subunit = ""
                state.pending_description.clear()
                found_content_section = True
                line_index += 1
                continue

            if SKIP_LINE_RE.match(line) or GUIDE_TITLE_RE.match(line):
                line_index += 1
                continue

            if state.section == "table_of_contents":
                line_index += 1
                continue

            joined_numbered = join_numbered_entry(lines, line_index)
            if joined_numbered:
                flush_pending_folder(page_number)
                entry_number, body, line_index = joined_numbered
                numbered = parse_numbered_entry(
                    f"{entry_number}. {body}",
                    guide_title,
                    guide_url,
                    compiled_by,
                    guide_date,
                    page_number,
                    state,
                )
                if numbered:
                    state.section = "numbered_holdings"
                    entries.append(numbered)
                    state.pending_description.clear()
                continue

            av_match = AUDIOVISUAL_ID_RE.match(line)
            if av_match and state.section.startswith("audiovisual"):
                description_lines = [av_match.group(2).strip()]
                line_index += 1
                while line_index < len(lines):
                    nxt = lines[line_index]
                    if (
                        AUDIOVISUAL_ID_RE.match(nxt)
                        or any(p.match(nxt) for _, p in SECTION_PATTERNS)
                        or PAGE_MARKER_RE.match(nxt)
                    ):
                        break
                    description_lines.append(nxt)
                    line_index += 1
                raw_text = " ".join(description_lines)
                entries.append(
                    make_entry(
                        guide_title=guide_title,
                        guide_url=guide_url,
                        compiled_by=compiled_by,
                        guide_date=guide_date,
                        page_number=page_number,
                        state=state,
                        entry_type="audiovisual_item",
                        item_id=av_match.group(1).upper(),
                        folder_or_item_title=av_match.group(2).strip(),
                        description=raw_text,
                        raw_entry_text=f"{av_match.group(1)} {raw_text}",
                    )
                )
                continue

            oh_match = ORAL_HISTORY_RE.match(line)
            if oh_match and state.section == "oral_history_transcripts":
                if state.pending_oral_history:
                    entries.append(
                        make_entry(
                            guide_title=guide_title,
                            guide_url=guide_url,
                            compiled_by=compiled_by,
                            guide_date=guide_date,
                            page_number=page_number,
                            state=state,
                            entry_type="oral_history",
                            item_id=f"OH-{state.pending_oral_history['oh_id']}",
                            folder_or_item_title=state.pending_oral_history["name"],
                            description=state.pending_oral_history.get("description", ""),
                            raw_entry_text=state.pending_oral_history["raw"],
                        )
                    )
                state.pending_oral_history = {
                    "name": oh_match.group(1).strip(),
                    "oh_id": oh_match.group(2),
                    "description": "",
                    "raw": line,
                }
                line_index += 1
                if line_index < len(lines) and not ORAL_HISTORY_RE.match(lines[line_index]):
                    desc = lines[line_index]
                    if not desc.startswith("*") and not PAGE_MARKER_RE.match(desc):
                        state.pending_oral_history["description"] = desc
                        state.pending_oral_history["raw"] += " " + desc
                        line_index += 1
                continue

            joined_header = join_collection_header(lines, line_index)
            used_joined_header = joined_header is not None
            if joined_header:
                line, line_index = joined_header
                header = split_collection_header(line)
            else:
                header = split_collection_header(line)
            if header:
                flush_pending_folder(page_number)
                if state.pending_description and state.collection_name:
                    desc = flush_description(state)
                    entries.append(
                        make_entry(
                            guide_title=guide_title,
                            guide_url=guide_url,
                            compiled_by=compiled_by,
                            guide_date=guide_date,
                            page_number=page_number,
                            state=state,
                            entry_type="collection_overview"
                            if state.section == "overview_of_collections"
                            else "collection_header",
                            description=desc,
                            raw_entry_text=f"{state.collection_name} {desc}".strip(),
                        )
                    )
                state.collection_name, state.creator, state.date_range, _ = header
                state.box = ""
                state.subunit = ""
                state.pending_description.clear()
                if state.section in {"body", "introduction"} and not found_content_section:
                    state.section = "detailed_folder_lists"
                if state.section == "overview_of_collections":
                    if not used_joined_header:
                        line_index += 1
                    while line_index < len(lines):
                        nxt = lines[line_index]
                        if (
                            split_collection_header(nxt)
                            or any(p.match(nxt) for _, p in SECTION_PATTERNS)
                            or PAGE_MARKER_RE.match(nxt)
                            or BOX_RE.match(nxt)
                            or NUMBERED_ENTRY_RE.match(nxt)
                        ):
                            break
                        state.pending_description.append(nxt)
                        line_index += 1
                    continue
                entries.append(
                    make_entry(
                        guide_title=guide_title,
                        guide_url=guide_url,
                        compiled_by=compiled_by,
                        guide_date=guide_date,
                        page_number=page_number,
                        state=state,
                        entry_type="collection_header",
                        raw_entry_text=line,
                    )
                )
                if not used_joined_header:
                    line_index += 1
                continue

            if SERIES_LINE_RE.match(line) or LOOSE_SERIES_RE.match(line):
                state.series = line.rstrip("-= ").strip()
                line_index += 1
                continue

            box_inline = BOX_INLINE_RE.match(line) or BOX_RANGE_INLINE_RE.match(line)
            if box_inline:
                state.box = box_inline.group(1).strip()
                title = box_inline.group(2).strip()
                line_index += 1
                while line_index < len(lines):
                    nxt = lines[line_index]
                    if (
                        BOX_INLINE_RE.match(nxt)
                        or BOX_RE.match(nxt)
                        or split_collection_header(nxt)
                        or any(p.match(nxt) for _, p in SECTION_PATTERNS)
                        or PAGE_MARKER_RE.match(nxt)
                    ):
                        break
                    if nxt and not SERIES_LINE_RE.match(nxt):
                        title += " " + nxt
                        line_index += 1
                        continue
                    break
                entries.append(
                    make_entry(
                        guide_title=guide_title,
                        guide_url=guide_url,
                        compiled_by=compiled_by,
                        guide_date=guide_date,
                        page_number=page_number,
                        state=state,
                        entry_type="folder_item",
                        folder_or_item_title=title,
                        raw_entry_text=f"Box {state.box} {title}".strip(),
                    )
                )
                continue

            box_match = BOX_RE.match(line)
            if box_match:
                state.box = box_match.group(1)
                line_index += 1
                continue

            if SUBUNIT_HEADER_RE.match(line) and state.section == "detailed_folder_lists":
                state.subunit = line
                line_index += 1
                continue

            if state.section == "books":
                entries.append(
                    make_entry(
                        guide_title=guide_title,
                        guide_url=guide_url,
                        compiled_by=compiled_by,
                        guide_date=guide_date,
                        page_number=page_number,
                        state=state,
                        entry_type="book",
                        folder_or_item_title=line,
                        raw_entry_text=line,
                    )
                )
                line_index += 1
                continue

            if state.section == "select_bibliography":
                if line.startswith(prose_prefixes):
                    entries.append(
                        make_entry(
                            guide_title=guide_title,
                            guide_url=guide_url,
                            compiled_by=compiled_by,
                            guide_date=guide_date,
                            page_number=page_number,
                            state=state,
                            entry_type="bibliography_intro",
                            description=line,
                            raw_entry_text=line,
                        )
                    )
                    line_index += 1
                    continue
                bib_parts = [line]
                line_index += 1
                while line_index < len(lines):
                    nxt = lines[line_index]
                    if (
                        any(p.match(nxt) for _, p in SECTION_PATTERNS)
                        or PAGE_MARKER_RE.match(nxt)
                        or (not nxt and line_index + 1 < len(lines))
                    ):
                        break
                    if not nxt:
                        line_index += 1
                        break
                    bib_parts.append(nxt)
                    line_index += 1
                    if re.search(r"\d{4}\.\s*$", nxt):
                        break
                raw_bib = " ".join(bib_parts).strip()
                entries.append(
                    make_entry(
                        guide_title=guide_title,
                        guide_url=guide_url,
                        compiled_by=compiled_by,
                        guide_date=guide_date,
                        page_number=page_number,
                        state=state,
                        entry_type="bibliography_item",
                        folder_or_item_title=bib_parts[0],
                        description=raw_bib,
                        raw_entry_text=raw_bib,
                    )
                )
                continue

            prose_prefixes = (
                "This ",
                "The ",
                "Also ",
                "Other ",
                "Because ",
                "For ",
                "These ",
                "In ",
                "However ",
                "References ",
                "Pre-Presidential Papers contain",
                "Confidential Files contains",
            )
            is_folder_context = state.section in {
                "detailed_folder_lists",
                "vertical_file",
                "body",
            } and (
                state.collection_name
                or state.box
                or (
                    state.section == "body"
                    and not line.startswith(prose_prefixes)
                    and len(line) < 300
                )
            )
            if (
                is_folder_context
                and state.collection_name
                and not re.match(r"^Box(?:es)?\s+", line, re.I)
                and len(line) > 2
            ):
                if state.section == "detailed_folder_lists" and pending_folder_parts:
                    if split_collection_header(line) or PAPERS_DATE_TAIL_RE.search(line):
                        flush_pending_folder(page_number)
                    elif line[0].islower() or line.startswith(
                        ("Continent,", "of ", "and ", "to ")
                    ):
                        pending_folder_parts.append(line)
                        line_index += 1
                        continue
                    else:
                        flush_pending_folder(page_number)

                bracket_note = ""
                title = line
                bracket_match = re.search(r"\s*(\[[^\]]+\])\s*$", line)
                if bracket_match:
                    bracket_note = bracket_match.group(1)
                    title = line[: bracket_match.start()].strip()

                if state.section == "detailed_folder_lists":
                    pending_folder_parts = [title]
                    if bracket_note:
                        pending_folder_parts.append(bracket_note)
                    line_index += 1
                    if line_index < len(lines):
                        nxt = lines[line_index]
                        if (
                            nxt
                            and not split_collection_header(nxt)
                            and not BOX_RE.match(nxt)
                            and not BOX_INLINE_RE.match(nxt)
                            and not any(p.match(nxt) for _, p in SECTION_PATTERNS)
                            and (nxt[0].islower() or nxt.startswith(("Continent,", "of ")))
                        ):
                            pending_folder_parts.append(nxt)
                            line_index += 1
                    flush_pending_folder(page_number)
                    continue

                entries.append(
                    make_entry(
                        guide_title=guide_title,
                        guide_url=guide_url,
                        compiled_by=compiled_by,
                        guide_date=guide_date,
                        page_number=page_number,
                        state=state,
                        entry_type="folder_item",
                        folder_or_item_title=title,
                        description=bracket_note,
                        raw_entry_text=line,
                    )
                )
                line_index += 1
                continue

            if (state.collection_name or state.series) and (
                line.startswith(prose_prefixes) or len(line) >= 300
            ):
                entry_type = (
                    "series_description"
                    if state.series and not state.box
                    else "narrative_entry"
                )
                entries.append(
                    make_entry(
                        guide_title=guide_title,
                        guide_url=guide_url,
                        compiled_by=compiled_by,
                        guide_date=guide_date,
                        page_number=page_number,
                        state=state,
                        entry_type=entry_type,
                        description=line,
                        raw_entry_text=line,
                    )
                )
            elif state.section == "introduction":
                entries.append(
                    make_entry(
                        guide_title=guide_title,
                        guide_url=guide_url,
                        compiled_by=compiled_by,
                        guide_date=guide_date,
                        page_number=page_number,
                        state=state,
                        entry_type="introduction_text",
                        description=line,
                        raw_entry_text=line,
                    )
                )
            line_index += 1

    if state.pending_oral_history:
        entries.append(
            make_entry(
                guide_title=guide_title,
                guide_url=guide_url,
                compiled_by=compiled_by,
                guide_date=guide_date,
                page_number=pages[-1][0] if pages else "",
                state=state,
                entry_type="oral_history",
                item_id=f"OH-{state.pending_oral_history['oh_id']}",
                folder_or_item_title=state.pending_oral_history["name"],
                description=state.pending_oral_history.get("description", ""),
                raw_entry_text=state.pending_oral_history["raw"],
            )
        )

    if state.pending_description and state.collection_name:
        desc = flush_description(state)
        entries.append(
            make_entry(
                guide_title=guide_title,
                guide_url=guide_url,
                compiled_by=compiled_by,
                guide_date=guide_date,
                page_number=pages[-1][0] if pages else "",
                state=state,
                entry_type="collection_overview",
                description=desc,
                raw_entry_text=f"{state.collection_name} {desc}".strip(),
            )
        )

    if pending_folder_parts:
        flush_pending_folder(pages[-1][0] if pages else 0)

    return entries


def write_csv(entries: list[dict[str, str]]) -> None:
    OUTPUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    with OUTPUT_CSV.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(entries)


def main() -> None:
    all_entries: list[dict[str, str]] = []
    manifest: dict[str, object] = {
        "source_url": SOURCE_URL,
        "guides": [],
        "total_entries": 0,
    }

    for title, path in WWII_GUIDES:
        print(f"Processing: {title}")
        guide_url, pdf_path = resolve_pdf_path(title, path)
        time.sleep(DOWNLOAD_DELAY_SEC)
        pages = extract_pages(pdf_path)
        entries = parse_guide(title, guide_url, pages)
        all_entries.extend(entries)

        by_type: dict[str, int] = {}
        by_section: dict[str, int] = {}
        for entry in entries:
            by_type[entry["entry_type"]] = by_type.get(entry["entry_type"], 0) + 1
            by_section[entry["section"]] = by_section.get(entry["section"], 0) + 1

        manifest["guides"].append(
            {
                "title": title,
                "pdf_url": guide_url,
                "pdf_path": str(pdf_path.relative_to(PROJECT_ROOT)),
                "page_count": len(pages),
                "entry_count": len(entries),
                "entries_by_type": by_type,
                "entries_by_section": by_section,
            }
        )
        print(f"  -> {len(entries)} entries from {len(pages)} pages")

    write_csv(all_entries)
    manifest["total_entries"] = len(all_entries)
    MANIFEST_JSON.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"\nWrote {len(all_entries)} entries to {OUTPUT_CSV}")
    print(f"Manifest: {MANIFEST_JSON}")


if __name__ == "__main__":
    main()
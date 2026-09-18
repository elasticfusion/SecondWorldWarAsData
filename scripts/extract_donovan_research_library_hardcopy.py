#!/usr/bin/env python3
"""Extract Donovan Research Library Historical Paper Documents index to CSV."""

from __future__ import annotations

import csv
import json
import re
import urllib.parse
import urllib.request
from html.parser import HTMLParser
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
WAYBACK_TIMESTAMP = "20250615140925"
ORIGINAL_LANDING_URL = (
    "https://www.benning.army.mil/Library/Documents/Hardcopy/index.html"
)
WAYBACK_LANDING_URL = (
    f"https://web.archive.org/web/{WAYBACK_TIMESTAMP}/{ORIGINAL_LANDING_URL}"
)
HTML_CACHE = (
    PROJECT_ROOT
    / "contentrepository/DonovanResearchLibrary/Hardcopy/index.html"
)
OUTPUT_CSV = (
    PROJECT_ROOT
    / "contentrepository/indexes/donovan_research_library_hardcopy_documents.csv"
)
MANIFEST_JSON = (
    PROJECT_ROOT
    / "contentrepository/indexes/donovan_research_library_hardcopy_documents_manifest.json"
)

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

SOURCE_PARENT_ORGANIZATION = "Maneuver Center of Excellence Libraries"
SOURCE_LIBRARY = "MCoE HQ Donovan Research Library"
COLLECTION_TITLE = "Historical Paper Documents Collection"
COLLECTION_DESCRIPTION = (
    "This historical paper collection of After Action Reports, Case Studies, "
    "Unit Actions dates from pre-World War II to post Vietnam. This collection "
    "is in no way a complete collection but reflects what is housed in the "
    "MCoE HQ Donovan Research Library Archives. Items are currently being "
    "assessed and cataloged. Additional items to this collection will be "
    "posted in the near future."
)

CSV_FIELDS = [
    "source_parent_organization",
    "source_library",
    "collection_title",
    "collection_description",
    "source_landing_page_url_wayback",
    "source_landing_page_url_original",
    "wayback_capture_timestamp",
    "entry_number",
    "catalog_number",
    "catalog_system",
    "link_text",
    "title",
    "part_section",
    "enclosure_note",
    "document_type",
    "submitting_organization",
    "report_date_text",
    "operations_mentioned",
    "theater_mentioned",
    "units_mentioned",
    "authors_mentioned",
    "pdf_url_wayback",
    "pdf_url_original",
    "pdf_filename",
    "raw_entry_text",
]

DOCUMENT_TYPE_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("after_action_report", re.compile(r"after\s+action\s+report", re.I)),
    ("unit_history", re.compile(r"unit\s+history", re.I)),
    ("combat_operations_report", re.compile(r"combat\s+operations?\s+after\s+action", re.I)),
    ("field_order", re.compile(r"field\s+order", re.I)),
    ("observer_report", re.compile(r"observer'?s?\s+report", re.I)),
    ("lessons_learned", re.compile(r"lessons?\s+(?:learned|from)", re.I)),
    ("interview", re.compile(r"\binterview\b", re.I)),
    ("historical_report", re.compile(r"historical\s+report", re.I)),
    ("operational_report", re.compile(r"operational\s+report", re.I)),
    ("case_study", re.compile(r"case\s+study", re.I)),
    ("war_diary", re.compile(r"war\s+diary", re.I)),
]

OPERATION_RE = re.compile(
    r'Operation\s+["\']?([A-Za-z][A-Za-z\s\-]+?)["\']?(?:\s*[,\.]|\s+for\b|\s+in\b|\s+on\b|$)',
    re.I,
)
THEATER_RE = re.compile(
    r"(European Theater(?:\s+of\s+Operations)?|Pacific Theater|China Burma India Theater|"
    r"Southwest Pacific|Philippine Islands|North Africa|Tunisia|Sicily|Italy|Normandy|"
    r"Vietnam|Korea)",
    re.I,
)
UNIT_RE = re.compile(
    r"(\d{1,3}(?:st|nd|rd|th)?\s+(?:"
    r"Armored|Airborne|Infantry|Cavalry|Tank|Engineer|Artillery|Marine|Ranger|"
    r"Parachute|Air Forces|Army Group|Corps|Division|Battalion|Regiment|Brigade|"
    r"Command|Group"
    r")(?:\s+(?:Division|Regiment|Battalion|Brigade|Corps|Command|Group|Force))?)",
    re.I,
)
AUTHOR_RE = re.compile(
    r"(?:by|prepared by|signed by|submitted by)\s+([^,;.]+(?:,\s*[^,;.]+)?)",
    re.I,
)


class PdfLinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.links: list[tuple[str, str]] = []
        self._in_anchor = False
        self._text_parts: list[str] = []
        self._href = ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "a":
            return
        self._in_anchor = True
        self._text_parts = []
        self._href = dict(attrs).get("href", "") or ""

    def handle_endtag(self, tag: str) -> None:
        if tag != "a" or not self._in_anchor:
            return
        text = "".join(self._text_parts).strip()
        if text and ".pdf" in self._href.lower():
            self.links.append((text, self._href))
        self._in_anchor = False

    def handle_data(self, data: str) -> None:
        if self._in_anchor:
            self._text_parts.append(data)


def clean_text(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace("\u00a0", " ")).strip()


def download_html() -> str:
    HTML_CACHE.parent.mkdir(parents=True, exist_ok=True)
    if HTML_CACHE.exists() and HTML_CACHE.stat().st_size > 1000:
        return HTML_CACHE.read_text(encoding="utf-8", errors="replace")
    request = urllib.request.Request(WAYBACK_LANDING_URL, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=120) as response:
        html = response.read().decode("utf-8", errors="replace")
    HTML_CACHE.write_text(html, encoding="utf-8")
    return html


def unwrap_wayback_url(url: str) -> str:
    cleaned = clean_text(url).strip("<>").strip()
    match = re.search(
        rf"https?://web\.archive\.org/web/{WAYBACK_TIMESTAMP}(?:im_)?/(https?://.+)",
        cleaned,
        re.I,
    )
    if match:
        return urllib.parse.unquote(match.group(1))
    return urllib.parse.unquote(cleaned)


def normalize_wayback_url(url: str) -> str:
    original = unwrap_wayback_url(url)
    if original.startswith("http"):
        return f"https://web.archive.org/web/{WAYBACK_TIMESTAMP}/{original}"
    return clean_text(url)


def pdf_filename(url: str) -> str:
    path = urllib.parse.urlparse(unwrap_wayback_url(url)).path
    return Path(path).name


def classify_catalog(catalog_number: str) -> str:
    if catalog_number.startswith("D "):
        return "library_D_series"
    if catalog_number.startswith("DS "):
        return "library_DS_series"
    if catalog_number.startswith("UB "):
        return "library_UB_series"
    if catalog_number.startswith("U "):
        return "library_U_series"
    if catalog_number.startswith("E"):
        return "library_E_series"
    if re.match(r"^\d", catalog_number):
        return "unit_file_number"
    return "other"


def split_catalog_and_description(link_text: str) -> tuple[str, str]:
    text = clean_text(link_text)
    match = re.match(r"^(.+?)\s+-\s+(.+)$", text)
    if match:
        return match.group(1).strip(), match.group(2).strip()
    return text, ""


def extract_part_section(description: str) -> str:
    match = re.match(r"^(Part\s+\d+\s+[^:]*):", description, re.I)
    return match.group(1).strip() if match else ""


def extract_enclosure_note(catalog_number: str, description: str) -> str:
    match = re.search(r"Enclosure\s+No\.?\s*\d+", catalog_number, re.I)
    if match:
        return match.group(0)
    match = re.search(r"Enclosure\s+No\.?\s*\d+", description, re.I)
    return match.group(0) if match else ""


def extract_title(description: str, part_section: str, submitting_organization: str) -> str:
    title = description
    if part_section and title.lower().startswith(part_section.lower()):
        title = title[len(part_section) :].lstrip(": ").strip()
    if submitting_organization:
        title = re.sub(
            r",?\s*submitted by\s+.+?(?=,\s*dated\b|,\s*date\b|\.|$)",
            "",
            title,
            flags=re.I,
        ).strip(" ,;-")
    title = re.sub(
        r",?\s*(?:dated|Date of report,?|Report dated)\s+.+$",
        "",
        title,
        flags=re.I,
    ).strip(" ,;-")
    return title


def extract_submitting_organization(description: str) -> str:
    match = re.search(
        r"submitted by\s+(.+?)(?:,\s*dated\b|,\s*date\b|\.|$)",
        description,
        re.I,
    )
    if match:
        return clean_text(match.group(1))
    match = re.search(
        r"report(?:ed)?\s+by\s+(.+?)(?:,\s*Report\b|,\s*dated\b|\.|$)",
        description,
        re.I,
    )
    return clean_text(match.group(1)) if match else ""


def extract_report_date(description: str, catalog_number: str = "") -> str:
    patterns = [
        r"dated\s+(.+)$",
        r"Date of report,?\s+(.+)$",
        r"Report dated\s+(.+)$",
        r"report dated\s+(.+)$",
        r"dated\s+(.+?)\.",
    ]
    for pattern in patterns:
        match = re.search(pattern, description, re.I)
        if match:
            return clean_text(match.group(1)).rstrip(".")
    if re.search(r"\bundated\b", description, re.I):
        return "undated"
    for source in (description, catalog_number):
        paren = re.search(r"\((\d{1,2}/\d{1,2}/\d{2,4}|\d{1,2}/\d{2,4}|\d{4})\)", source)
        if paren:
            return clean_text(paren.group(1))
    return ""


def extract_document_type(description: str) -> str:
    matched: list[str] = []
    for label, pattern in DOCUMENT_TYPE_PATTERNS:
        if pattern.search(description):
            matched.append(label)
    return "; ".join(matched)


def unique_join(matches: list[str], limit: int = 8) -> str:
    seen: set[str] = set()
    ordered: list[str] = []
    for value in matches:
        normalized = clean_text(value)
        key = normalized.lower()
        if not normalized or key in seen:
            continue
        seen.add(key)
        ordered.append(normalized)
        if len(ordered) >= limit:
            break
    return "; ".join(ordered)


def parse_entry(entry_number: int, link_text: str, href: str) -> dict[str, str]:
    catalog_number, description = split_catalog_and_description(link_text)
    part_section = extract_part_section(description)
    enclosure_note = extract_enclosure_note(catalog_number, description)
    submitting_organization = extract_submitting_organization(description)
    title = extract_title(description, part_section, submitting_organization)
    pdf_original = unwrap_wayback_url(href)
    pdf_wayback = normalize_wayback_url(href)

    operations = unique_join(
        f"Operation {name.strip()}" for name in OPERATION_RE.findall(description)
    )
    theaters = unique_join(THEATER_RE.findall(description))
    units = unique_join(UNIT_RE.findall(description))
    authors = unique_join(AUTHOR_RE.findall(description))

    return {
        "source_parent_organization": SOURCE_PARENT_ORGANIZATION,
        "source_library": SOURCE_LIBRARY,
        "collection_title": COLLECTION_TITLE,
        "collection_description": COLLECTION_DESCRIPTION,
        "source_landing_page_url_wayback": WAYBACK_LANDING_URL,
        "source_landing_page_url_original": ORIGINAL_LANDING_URL,
        "wayback_capture_timestamp": WAYBACK_TIMESTAMP,
        "entry_number": str(entry_number),
        "catalog_number": catalog_number,
        "catalog_system": classify_catalog(catalog_number),
        "link_text": clean_text(link_text),
        "title": title,
        "part_section": part_section,
        "enclosure_note": enclosure_note,
        "document_type": extract_document_type(description),
        "submitting_organization": submitting_organization,
        "report_date_text": extract_report_date(description, catalog_number),
        "operations_mentioned": operations,
        "theater_mentioned": theaters,
        "units_mentioned": units,
        "authors_mentioned": authors,
        "pdf_url_wayback": pdf_wayback,
        "pdf_url_original": pdf_original,
        "pdf_filename": pdf_filename(href),
        "raw_entry_text": clean_text(link_text),
    }


def extract_entries(html: str) -> list[dict[str, str]]:
    parser = PdfLinkParser()
    parser.feed(html)
    return [
        parse_entry(index, link_text, href)
        for index, (link_text, href) in enumerate(parser.links, start=1)
    ]


def write_csv(entries: list[dict[str, str]]) -> None:
    OUTPUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    with OUTPUT_CSV.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS)
        writer.writeheader()
        writer.writerows(entries)


def main() -> None:
    html = download_html()
    entries = extract_entries(html)

    by_catalog: dict[str, int] = {}
    by_system: dict[str, int] = {}
    by_type: dict[str, int] = {}
    for entry in entries:
        by_catalog[entry["catalog_number"]] = by_catalog.get(entry["catalog_number"], 0) + 1
        by_system[entry["catalog_system"]] = by_system.get(entry["catalog_system"], 0) + 1
        for doc_type in filter(None, entry["document_type"].split("; ")):
            by_type[doc_type] = by_type.get(doc_type, 0) + 1

    write_csv(entries)
    manifest = {
        "source_parent_organization": SOURCE_PARENT_ORGANIZATION,
        "source_library": SOURCE_LIBRARY,
        "collection_title": COLLECTION_TITLE,
        "collection_description": COLLECTION_DESCRIPTION,
        "source_landing_page_url_wayback": WAYBACK_LANDING_URL,
        "source_landing_page_url_original": ORIGINAL_LANDING_URL,
        "wayback_capture_timestamp": WAYBACK_TIMESTAMP,
        "html_cache": str(HTML_CACHE.relative_to(PROJECT_ROOT)),
        "output_csv": str(OUTPUT_CSV.relative_to(PROJECT_ROOT)),
        "total_entries": len(entries),
        "duplicate_catalog_numbers": {
            catalog: count for catalog, count in sorted(by_catalog.items()) if count > 1
        },
        "entries_by_catalog_system": dict(sorted(by_system.items())),
        "entries_by_document_type": dict(
            sorted(by_type.items(), key=lambda item: (-item[1], item[0]))
        ),
    }
    MANIFEST_JSON.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"Extracted {len(entries)} entries to {OUTPUT_CSV}")


if __name__ == "__main__":
    main()
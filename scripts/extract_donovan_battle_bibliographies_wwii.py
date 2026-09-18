#!/usr/bin/env python3
"""Extract WWII battle bibliographies from Donovan Research Library index."""

from __future__ import annotations

import csv
import json
import re
import time
import urllib.parse
import urllib.request
from html.parser import HTMLParser
from pathlib import Path

import fitz

PROJECT_ROOT = Path(__file__).resolve().parents[1]
WAYBACK_TIMESTAMP = "20250602023633"
ORIGINAL_LANDING_URL = "https://www.benning.army.mil/Library/Bibliographies.html"
WAYBACK_LANDING_URL = (
    f"https://web.archive.org/web/{WAYBACK_TIMESTAMP}/{ORIGINAL_LANDING_URL}"
)
HTML_CACHE = (
    PROJECT_ROOT / "contentrepository/DonovanResearchLibrary/Bibliographies.html"
)
PDF_CACHE_DIR = (
    PROJECT_ROOT / "contentrepository/DonovanResearchLibrary/Bibliographies"
)
OUTPUT_CSV = (
    PROJECT_ROOT
    / "contentrepository/indexes/donovan_research_library_wwii_battle_bibliographies.csv"
)
MANIFEST_JSON = (
    PROJECT_ROOT
    / "contentrepository/indexes/donovan_research_library_wwii_battle_bibliographies_manifest.json"
)

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
DOWNLOAD_DELAY_SEC = 0.5

SOURCE_PARENT_ORGANIZATION = "Maneuver Center of Excellence Libraries"
SOURCE_LIBRARY = "MCoE HQ Donovan Research Library"
COLLECTION_TITLE = "Battle Bibliographies"

SKIP_LINK_PREFIXES = (
    "Chief of Staff",
    "Annotated Bibliography",
    "MCoE Commanding General",
    "Infantry Commandant",
    "USAARMS Commandant",
    "CHINA:",
    "Military Advisor",
    "Transformational",
)

WWII_BATTLE_SLUGS = {
    "ARRACOURT",
    "BASTOGNE",
    "BRECOURT_MANOR",
    "CABANATUAN",
    "CARENTAN",
    "CISTERNA",
    "CORREGIDOR",
    "EBEN_EMAEL",
    "EL_ALAMEIN_Aug2023",
    "GAZALA",
    "GUADALCANAL",
    "HAMMELBURG_RAID",
    "HUERTGEN_FOREST",
    "KHALKHIN_GOL",
    "KURSK_PROKHOROVKA",
    "LEYTE_GULF",
    "LUDENDORFF_BRIDGE",
    "MARKET_GARDEN",
    "MERRILLS_MARAUDERS",
    "OPERATION_END_RUN_SEIZURE_MYITKYINA_AIRFIELD",
    "OPERATION_GUNNERSIDE_NORWAY",
    "PEGASUS_BRIDGE",
    "PELELIU",
    "POINTE_DU_HOC",
    "REMAGEN",
    "RIVA_RIDGE",
    "SADZOT",
    "SAINT_VITH",
    "SAIPAN",
    "SIDI_BOU_ZID",
    "SINGLING",
    "ST_LO",
    "STALINGRAD",
    "TARAWA",
    "AIRBORNE_STUDENT_PAPERS_WWII",
}

NON_WWII_SLUGS = {
    "73_EASTING",
    "ABU_AGEILA",
    "AD_DIWANIYAH_AMBUSH",
    "AGINCOURT",
    "ALLATOONA_PASS",
    "AMIENS",
    "ANACONDA",
    "AN_LOC",
    "ARAB_ISRAELI_WARS_YOM_KIPPUR",
    "ARRAS",
    "AUSTERLITZ",
    "BALACLAVA",
    "BELLEAU_WOOD",
    "BOSNIA_AND_HERZEGOVINA",
    "BRAVO_TWO_ZERO",
    "BREITENFELD_1631",
    "BRICES_CROSSROADS",
    "BUNKER_HILL",
    "CAMBRAI",
    "CANNAE",
    "CHANCELLORSVILLE",
    "CHICKAMAUGA",
    "CHOSIN",
    "COLENSO",
    "COP_KEATING",
    "COWPENS",
    "DAK_TO",
    "DEBECKA_PASS",
    "DESERT_STORM_1991",
    "DIEN_BIEN_PHU",
    "ENTEBEE_RAID",
    "FALKLAND_ISLANDS",
    "FALLUJAH",
    "FIRST_THUNDER_RUN",
    "FORT_DUQUESNE",
    "FORT_McCALLISTER",
    "FRANKLIN",
    "FRONT_ROYAL",
    "GALLIPOLI",
    "GANG_TOI",
    "GAUGAMELA",
    "GLORIETA_PASS",
    "GOOSE_GREEN",
    "GROZNY_AND_CHECHNYA",
    "HADITHA_DAM",
    "HASTINGS",
    "HEARTBREAK_RIDGE_KOREA",
    "HILL_205",
    "HORSESHOE_BEND",
    "HUE",
    "IA_DRANG",
    "INCHON_LANDING",
    "IRAQ_INVASION_2003",
    "ISANDHLWANA",
    "JUST_CAUSE",
    "KENNESAW_MOUNTAIN",
    "KHE_SANH",
    "KINGS_MOUNTAIN",
    "LANG_VEI",
    "LITTLE_BIG_HORN",
    "LITTLE_ROUND_TOP",
    "LONG_TAN",
    "LZ_ALBANY",
    "LZ_XRAY",
    "MACTAN",
    "MARATHON",
    "MARNE_SECOND_BATTLE",
    "MEDINA_RIDGE",
    "MOGADISHU_SOMALIA",
    "NAGORNO_KARABAKH_CONFLICT_2020",
    "NEW_ORLEANS",
    "NICKAJACK_REGION_CIVIL_WAR",
    "OPERATION_RED_WINGS",
    "ORISKANY",
    "PEA_RIDGE",
    "PERRYVILLE",
    "PICKETTS_MILL_NEW_HOPE_CHURCH",
    "PORT_REPUBLIC_CROSS_KEYS",
    "POWDER_RIVER",
    "PRINCETON",
    "RICHMOND",
    "ROCROI",
    "RORKES_DRIFT",
    "ROSEBUD",
    "SAN_JUAN_HILL",
    "SEVEN_DAYS_BATTLES",
    "SINJAR_IRAQ",
    "SIX_DAY_WAR",
    "SOISSONS",
    "SOMME",
    "SON_TAY_RAID",
    "STONES_RIVER_MURFREESBORO",
    "TAL_AFAR_IRAQ",
    "TANNENBERG",
    "TASK_FORCE_NORMANDY_DESERT_STORM",
    "TASK_FORCE_SMITH",
    "TET_OFFENSIVE",
    "THAMES",
    "THERMOPYLAE",
    "TICONDEROGA",
    "TOKTONG_PASS",
    "TRENTON",
    "TWIN_TUNNELS",
    "VALLEY_OF_TEARS",
    "VERDUN",
    "WANAT",
    "WATERLOO",
    "ZAMA_MARGARON",
    "10TH_MOUNTAIN_DIVISION",
    "AIRBORNE_HISTORY",
    "ARMOR_HISTORY",
    "BERLIN_AIRLIFT",
    "CYBER_WARFARE_with_summaries",
    "DESERT_ONE_OPERATION_EAGLE_CLAW",
    "EXTREMISM",
    "HAMBURGER_HILL",
    "MANEUVER_SELF_STUDY_READING_LIST",
    "MARKSMANSHIP",
    "SOUTHERN_UNIONISTS_DURING_UNITED_STATES_CIVIL_WAR",
    "TORA_BORA",
}

CSV_FIELDS = [
    "source_parent_organization",
    "source_library",
    "collection_title",
    "source_landing_page_url_wayback",
    "source_landing_page_url_original",
    "wayback_capture_timestamp",
    "battle_name",
    "battle_slug",
    "bibliography_pdf_url_wayback",
    "bibliography_pdf_url_original",
    "bibliography_pdf_filename",
    "bibliography_revision_date",
    "wwii_classification",
    "resource_section",
    "entry_number_in_battle",
    "entry_type",
    "call_number",
    "author",
    "title",
    "publisher",
    "publication_place",
    "publication_year",
    "resource_url",
    "access_note",
    "page_number_in_pdf",
    "raw_entry_text",
]

SECTION_HEADERS = {
    "BOOKS",
    "AUDIOBOOKS",
    "DOCUMENTS",
    "MAPS",
    "PERIODICALS",
    "JOURNAL ARTICLES",
    "VERTICAL FILE",
    "DVDs",
    "VIDEOS",
    "INTERNET SITES",
    "WEB SITES",
    "GOVERNMENT DOCUMENTS",
    "PAMPHLETS",
    "MAGAZINE ARTICLES",
    "E-BOOKS",
}

SECTION_START_RE = re.compile(
    r"^(BOOKS|AUDIOBOOKS|DOCUMENTS|MAPS|PERIODICALS|JOURNAL ARTICLES|VERTICAL FILE|"
    r"DVDs|VIDEOS|INTERNET SITES|WEB SITES|GOVERNMENT DOCUMENTS|PAMPHLETS|"
    r"MAGAZINE ARTICLES|E-BOOKS(?:\s*&\s*E-AUDIO BOOKS)?(?:\s+Available.*)?|"
    r"Available from Overdrive|Available from EBSCOHost)\b",
    re.I,
)

CALL_NUMBER_RE = re.compile(
    r"^[A-Z]{1,3}(?:\.\d+)?(?:\s+[A-Z]?\d{1,4}[\w./#()\-]*)+"
    r"(?:\s+\d{4}[a-z]?(?:\s+[A-Z]{1,3})?)?\s*$"
)
CALL_NUMBER_LOOSE_RE = re.compile(
    r"^[A-Z]{1,3}[\d.].{0,70}$"
)
AUTHOR_START_RE = re.compile(
    r"^[A-Z][A-Za-z'\-]+,\s+[A-Z]"
)
URL_RE = re.compile(r"https?://\S+")
PUBLISHER_RE = re.compile(
    r"([A-Z][A-Za-z .'-]+(?:,\s*[A-Z]{2})?):\s*([^,.]+(?:,\s*[^,.]+)?),\s*(\d{4})\b"
)
HEADER_SKIP_RE = re.compile(
    r"^(MCoE|https://www\.benning|MARCH\s*\d{4}|\d{1,3}$|BOOKS and|"
    r"Will require a library account|Contact the|For additional printed|"
    r"Ask circulation|Contact Virtual|May Require Library|"
    r"\(May Require Library|\(Will require|not linked from the Advanced)",
    re.I,
)


class BattleLinkParser(HTMLParser):
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
        if text and self._href:
            self.links.append((text, self._href))
        self._in_anchor = False

    def handle_data(self, data: str) -> None:
        if self._in_anchor:
            self._text_parts.append(data)


def clean_text(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace("\u00a0", " ")).strip()


def unwrap_wayback_url(url: str) -> str:
    cleaned = clean_text(url).strip("<>").strip()
    match = re.search(
        rf"https?://web\.archive\.org/web/{WAYBACK_TIMESTAMP}(?:id_|if_|im_)?/(https?://.+)",
        cleaned,
        re.I,
    )
    if match:
        return urllib.parse.unquote(match.group(1))
    match = re.search(rf"/web/{WAYBACK_TIMESTAMP}/(https?://.+)", cleaned, re.I)
    if match:
        return urllib.parse.unquote(match.group(1))
    return urllib.parse.unquote(cleaned)


def wayback_pdf_url(original_url: str) -> str:
    return f"https://web.archive.org/web/{WAYBACK_TIMESTAMP}id_/{original_url}"


def download_landing_html() -> str:
    HTML_CACHE.parent.mkdir(parents=True, exist_ok=True)
    if HTML_CACHE.exists() and HTML_CACHE.stat().st_size > 1000:
        return HTML_CACHE.read_text(encoding="utf-8", errors="replace")
    request = urllib.request.Request(WAYBACK_LANDING_URL, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=120) as response:
        html = response.read().decode("utf-8", errors="replace")
    HTML_CACHE.write_text(html, encoding="utf-8")
    return html


def parse_battle_links(html: str) -> list[dict[str, str]]:
    parser = BattleLinkParser()
    parser.feed(html)
    battles: list[dict[str, str]] = []
    seen_slugs: set[str] = set()
    for battle_name, href in parser.links:
        if "library/bibliographies/" not in href.lower():
            continue
        if any(battle_name.startswith(prefix) for prefix in SKIP_LINK_PREFIXES):
            continue
        slug_match = re.search(r"bibliographies/([^/?#]+)\.pdf", href, re.I)
        if not slug_match:
            continue
        slug = slug_match.group(1)
        if slug in seen_slugs:
            continue
        seen_slugs.add(slug)
        original_pdf = (
            f"https://mcoecbamcoepwprd01.blob.core.usgovcloudapi.net/"
            f"library/bibliographies/{slug}.pdf"
        )
        battles.append(
            {
                "battle_name": clean_text(battle_name),
                "battle_slug": slug,
                "bibliography_pdf_url_original": original_pdf,
                "bibliography_pdf_url_wayback": wayback_pdf_url(original_pdf),
                "bibliography_pdf_filename": f"{slug}.pdf",
            }
        )
    return battles


PDF_SLUG_FALLBACKS: dict[str, list[str]] = {
    "EL_ALAMEIN_Aug2023": ["EL_ALAMEIN"],
}


def download_pdf(slug: str, original_url: str) -> Path:
    PDF_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_path = PDF_CACHE_DIR / f"{slug}.pdf"
    if cache_path.exists() and cache_path.stat().st_size > 1000:
        return cache_path

    slug_candidates = [slug, *PDF_SLUG_FALLBACKS.get(slug, [])]
    last_error: Exception | None = None
    for candidate in slug_candidates:
        candidate_url = (
            f"https://mcoecbamcoepwprd01.blob.core.usgovcloudapi.net/"
            f"library/bibliographies/{candidate}.pdf"
        )
        request = urllib.request.Request(
            wayback_pdf_url(candidate_url),
            headers={"User-Agent": USER_AGENT},
        )
        for attempt in range(4):
            try:
                with urllib.request.urlopen(request, timeout=120) as response:
                    data = response.read()
                if data[:4] == b"%PDF":
                    cache_path.write_bytes(data)
                    return cache_path
            except Exception as exc:
                last_error = exc
                if attempt == 3:
                    break
                time.sleep(2 ** attempt)
    if last_error:
        raise last_error
    raise RuntimeError(f"Failed to download PDF for {slug}")


def pdf_suggests_wwii(text: str) -> bool:
    lowered = text.lower()
    wwii_hits = len(
        re.findall(
            r"world war ii|second world war|wwii|1939-1945|1941-1945|european theater|"
            r"pacific theater|ardennes|normandy|guadalcanal|stalingrad|operation market",
            lowered,
        )
    )
    non_wwii_hits = len(
        re.findall(
            r"vietnam|iraq|afghanistan|civil war|korean war|world war i|great war|"
            r"desert storm|somalia|falklands",
            lowered,
        )
    )
    return wwii_hits >= 2 and wwii_hits > non_wwii_hits


def normalize_battle_title(text: str) -> str:
    text = clean_text(text).upper()
    for old, new in {
        "Ô": "O",
        "É": "E",
        "Ë": "E",
        "’": "'",
        "‘": "'",
        "`": "'",
    }.items():
        text = text.replace(old, new)
    text = re.sub(r"\s*\([^)]*\)", "", text)
    text = re.sub(r"[^A-Z0-9 .'-]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def battle_title_matches(line: str, battle_name: str, battle_slug: str = "") -> bool:
    normalized_line = normalize_battle_title(line)
    normalized_battle = normalize_battle_title(battle_name)
    if not normalized_line:
        return False
    if normalized_battle:
        if normalized_line == normalized_battle:
            return True
        if normalized_line.startswith(normalized_battle):
            return True
        battle_tokens = normalized_battle.split()
        line_tokens = normalized_line.split()
        if len(battle_tokens) >= 2 and line_tokens[: len(battle_tokens)] == battle_tokens:
            return True
    if battle_slug:
        slug_tokens = [
            token
            for token in re.split(r"[_\W]+", battle_slug.upper())
            if token and token not in {"WWII", "THE", "AND", "OF"}
        ]
        if len(slug_tokens) >= 2 and all(token in normalized_line for token in slug_tokens[:3]):
            return True
    return False


def is_wwii_candidate(slug: str) -> bool:
    if slug in WWII_BATTLE_SLUGS:
        return True
    if slug in NON_WWII_SLUGS:
        return False
    return "wwii" in slug.lower()


def classify_battle(slug: str, battle_name: str, pdf_text: str) -> tuple[bool, str]:
    if slug in WWII_BATTLE_SLUGS:
        return True, "curated_wwii_battle_slug"
    if slug in NON_WWII_SLUGS:
        return False, "curated_non_wwii_slug"
    if "wwii" in slug.lower():
        return True, "wwii_in_slug"
    if pdf_suggests_wwii(pdf_text):
        return True, "pdf_content_wwii_markers"
    return False, "not_wwii"


def normalize_section(line: str) -> str:
    match = SECTION_START_RE.match(line.strip())
    if not match:
        return ""
    section = match.group(1).upper()
    if section.startswith("E-BOOKS"):
        return "E-BOOKS"
    if section.startswith("AVAILABLE FROM OVERDRIVE"):
        return "E-BOOKS (OVERDRIVE)"
    if section.startswith("AVAILABLE FROM EBSCOHOST"):
        return "JOURNAL ARTICLES (EBSCOHOST)"
    return section


def is_call_number(line: str) -> bool:
    stripped = line.strip()
    if not stripped or len(stripped) > 90:
        return False
    if CALL_NUMBER_RE.match(stripped):
        return True
    if CALL_NUMBER_LOOSE_RE.match(stripped) and not AUTHOR_START_RE.match(stripped):
        if re.search(r"\d{4}[a-z]?\s*$", stripped):
            return True
        if "Item nos." in stripped or re.search(r"\.\s*[A-Z]\d", stripped):
            return True
    return False


def is_section_intro(line: str) -> bool:
    stripped = line.strip()
    if not stripped:
        return True
    if SECTION_START_RE.match(stripped):
        return True
    if HEADER_SKIP_RE.match(stripped):
        return True
    if stripped.endswith(")") and "Contact the Reference Desk" in stripped:
        return True
    return False


def infer_entry_type(section: str, text: str) -> str:
    section_lower = section.lower()
    if "journal" in section_lower:
        return "journal_article"
    if "audiobook" in section_lower or "e-audio" in section_lower:
        return "audiobook"
    if "e-book" in section_lower or "overdrive" in section_lower:
        return "ebook"
    if section_lower == "documents":
        return "document"
    if section_lower == "maps":
        return "map"
    if section_lower == "periodicals":
        return "periodical"
    if "vertical file" in section_lower:
        return "vertical_file_note"
    if URL_RE.search(text) and "dtic.mil" in text:
        return "dtic_document"
    if URL_RE.search(text):
        return "linked_resource"
    if re.search(r"CPT|MAJ|LTC|COL", text):
        return "student_paper"
    return "book"


def parse_access_note(text: str) -> str:
    notes: list[str] = []
    for pattern in (
        r"Archives-in library use only",
        r"In Donovan Archives",
        r"Download available at",
        r"subscription databases",
        r"Require(?:s)? Library account",
    ):
        if re.search(pattern, text, re.I):
            notes.append(pattern.replace(r"(?:s)?", "").replace("\\", ""))
    return "; ".join(dict.fromkeys(notes))


def parse_author_title(text: str) -> tuple[str, str]:
    text = clean_text(text)
    match = re.match(
        r"^([A-Z][A-Za-z'\-]+(?:\s+[A-Z]\.?)?(?:\s+et al\.)?),?\s+(.+)$",
        text,
    )
    if match:
        author = match.group(1).strip()
        remainder = match.group(2).strip()
        title = remainder
        pub = PUBLISHER_RE.search(remainder)
        if pub:
            title = remainder[: pub.start()].strip(" .")
        else:
            sentence = re.split(r"\.\s+(?=[A-Z][a-z]+:)", remainder, maxsplit=1)
            if sentence:
                title = sentence[0].strip(" .")
        return author, title
    quoted = re.search(r'"([^"]+)"', text)
    if quoted:
        return "", quoted.group(1)
    return "", text[:200]


def parse_publication(text: str) -> tuple[str, str, str]:
    match = PUBLISHER_RE.search(text)
    if not match:
        year_match = re.search(r"\b(19\d{2}|20\d{2})\b", text)
        return "", "", year_match.group(1) if year_match else ""
    return match.group(1).strip(), match.group(2).strip(), match.group(3).strip()


def should_start_entry(line: str, current_lines: list[str], current_call: str) -> bool:
    stripped = line.strip()
    if not stripped or is_section_intro(stripped):
        return False
    if is_call_number(stripped):
        return True
    if URL_RE.fullmatch(stripped):
        return True
    if AUTHOR_START_RE.match(stripped) and current_lines:
        joined = clean_text(" ".join(current_lines))
        if joined.endswith((".", ")", "]", "0", "1", "2", "3", "4", "5", "6", "7", "8", "9")):
            return True
    if AUTHOR_START_RE.match(stripped) and not current_lines and not current_call:
        return True
    return False


def flush_entry(
    *,
    battle: dict[str, str],
    section: str,
    entry_number: int,
    page_number: int,
    call_number: str,
    lines: list[str],
    wwii_reason: str,
    revision_date: str,
) -> dict[str, str] | None:
    text = clean_text(" ".join(lines))
    if not text or len(text) < 8:
        return None
    if "vertical file at the" in text.lower():
        return None
    author, title = parse_author_title(text)
    pub_place, publisher, pub_year = parse_publication(text)
    url_match = URL_RE.search(text)
    return {
        "source_parent_organization": SOURCE_PARENT_ORGANIZATION,
        "source_library": SOURCE_LIBRARY,
        "collection_title": COLLECTION_TITLE,
        "source_landing_page_url_wayback": WAYBACK_LANDING_URL,
        "source_landing_page_url_original": ORIGINAL_LANDING_URL,
        "wayback_capture_timestamp": WAYBACK_TIMESTAMP,
        "battle_name": battle["battle_name"],
        "battle_slug": battle["battle_slug"],
        "bibliography_pdf_url_wayback": battle["bibliography_pdf_url_wayback"],
        "bibliography_pdf_url_original": battle["bibliography_pdf_url_original"],
        "bibliography_pdf_filename": battle["bibliography_pdf_filename"],
        "bibliography_revision_date": revision_date,
        "wwii_classification": wwii_reason,
        "resource_section": section,
        "entry_number_in_battle": str(entry_number),
        "entry_type": infer_entry_type(section, text),
        "call_number": call_number,
        "author": author,
        "title": title,
        "publisher": publisher,
        "publication_place": pub_place,
        "publication_year": pub_year,
        "resource_url": url_match.group(0).rstrip(").,;") if url_match else "",
        "access_note": parse_access_note(text),
        "page_number_in_pdf": str(page_number),
        "raw_entry_text": text,
    }


def extract_revision_date(text: str) -> str:
    match = re.search(r"\b((?:JANUARY|FEBRUARY|MARCH|APRIL|MAY|JUNE|JULY|AUGUST|"
                      r"SEPTEMBER|OCTOBER|NOVEMBER|DECEMBER)\s+\d{4})\b", text, re.I)
    return match.group(1).title() if match else ""


def extract_entries_from_pdf(
    battle: dict[str, str], pdf_path: Path, wwii_reason: str
) -> list[dict[str, str]]:
    doc = fitz.open(pdf_path)
    line_page: list[tuple[int, str]] = []
    for page_index, page in enumerate(doc):
        for line in page.get_text().splitlines():
            cleaned = clean_line(line)
            if cleaned:
                line_page.append((page_index + 1, cleaned))

    full_text = "\n".join(line for _, line in line_page)
    revision_date = extract_revision_date(full_text)
    entries: list[dict[str, str]] = []
    section = "GENERAL"
    entry_number = 0
    current_lines: list[str] = []
    current_call = ""
    current_page = 1
    battle_title_seen = False

    def emit() -> None:
        nonlocal entry_number, current_lines, current_call
        entry_number += 1
        row = flush_entry(
            battle=battle,
            section=section,
            entry_number=entry_number,
            page_number=current_page,
            call_number=current_call,
            lines=current_lines,
            wwii_reason=wwii_reason,
            revision_date=revision_date,
        )
        if row:
            entries.append(row)
        current_lines = []
        current_call = ""

    for page_number, line in line_page:
        current_page = page_number
        if battle_title_matches(line, battle["battle_name"], battle["battle_slug"]):
            battle_title_seen = True
            continue
        if not battle_title_seen:
            if normalize_section(line) or is_call_number(line) or AUTHOR_START_RE.match(line):
                battle_title_seen = True
            else:
                continue

        new_section = normalize_section(line)
        if new_section:
            if current_lines or current_call:
                emit()
            section = new_section
            continue
        if is_section_intro(line):
            continue

        if is_call_number(line):
            if current_lines or current_call:
                emit()
            current_call = line.strip()
            continue

        if should_start_entry(line, current_lines, current_call):
            if current_lines or current_call:
                emit()

        current_lines.append(line)

    if current_lines or current_call:
        emit()
    return entries


def clean_line(line: str) -> str:
    return clean_text(line)


def write_csv(entries: list[dict[str, str]]) -> None:
    OUTPUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    with OUTPUT_CSV.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS)
        writer.writeheader()
        writer.writerows(entries)


def main() -> None:
    html = download_landing_html()
    battles = parse_battle_links(html)
    all_entries: list[dict[str, str]] = []
    manifest_battles: list[dict[str, object]] = []
    non_wwii_manifest = [
        {
            "battle_name": battle["battle_name"],
            "battle_slug": battle["battle_slug"],
            "wwii": False,
            "classification": "curated_non_wwii_slug",
            "entry_count": 0,
        }
        for battle in battles
        if not is_wwii_candidate(battle["battle_slug"])
    ]
    wwii_battles = [battle for battle in battles if is_wwii_candidate(battle["battle_slug"])]

    for index, battle in enumerate(wwii_battles):
        slug = battle["battle_slug"]
        cache_path = PDF_CACHE_DIR / f"{slug}.pdf"
        try:
            pdf_path = download_pdf(slug, battle["bibliography_pdf_url_original"])
        except Exception as exc:
            if cache_path.exists() and cache_path.stat().st_size > 1000:
                pdf_path = cache_path
            else:
                manifest_battles.append(
                    {
                        "battle_name": battle["battle_name"],
                        "battle_slug": slug,
                        "wwii": True,
                        "classification": f"download_failed: {exc}",
                        "entry_count": 0,
                    }
                )
                continue

        pdf_text = "\n".join(page.get_text() for page in fitz.open(pdf_path))
        is_wwii, reason = classify_battle(slug, battle["battle_name"], pdf_text)
        if not is_wwii:
            manifest_battles.append(
                {
                    "battle_name": battle["battle_name"],
                    "battle_slug": slug,
                    "wwii": False,
                    "classification": reason,
                    "entry_count": 0,
                }
            )
            continue

        entries = extract_entries_from_pdf(battle, pdf_path, reason)
        all_entries.extend(entries)
        by_section: dict[str, int] = {}
        by_type: dict[str, int] = {}
        for entry in entries:
            by_section[entry["resource_section"]] = (
                by_section.get(entry["resource_section"], 0) + 1
            )
            by_type[entry["entry_type"]] = by_type.get(entry["entry_type"], 0) + 1
        manifest_battles.append(
            {
                "battle_name": battle["battle_name"],
                "battle_slug": slug,
                "wwii": True,
                "classification": reason,
                "entry_count": len(entries),
                "entries_by_section": by_section,
                "entries_by_type": by_type,
            }
        )
        print(f"WWII {battle['battle_name']}: {len(entries)} entries")
        if index < len(wwii_battles) - 1 and not cache_path.exists():
            time.sleep(DOWNLOAD_DELAY_SEC)

    manifest_battles.extend(non_wwii_manifest)

    write_csv(all_entries)
    wwii_battles = [b for b in manifest_battles if b.get("wwii")]
    manifest = {
        "source_parent_organization": SOURCE_PARENT_ORGANIZATION,
        "source_library": SOURCE_LIBRARY,
        "collection_title": COLLECTION_TITLE,
        "source_landing_page_url_wayback": WAYBACK_LANDING_URL,
        "source_landing_page_url_original": ORIGINAL_LANDING_URL,
        "wayback_capture_timestamp": WAYBACK_TIMESTAMP,
        "html_cache": str(HTML_CACHE.relative_to(PROJECT_ROOT)),
        "pdf_cache_dir": str(PDF_CACHE_DIR.relative_to(PROJECT_ROOT)),
        "output_csv": str(OUTPUT_CSV.relative_to(PROJECT_ROOT)),
        "total_battles_on_page": len(battles),
        "wwii_battles": len(wwii_battles),
        "total_entries": len(all_entries),
        "battles": manifest_battles,
    }
    MANIFEST_JSON.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"\nWrote {len(all_entries)} entries from {len(wwii_battles)} WWII battles")


if __name__ == "__main__":
    main()
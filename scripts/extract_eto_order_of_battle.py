#!/usr/bin/env python3
"""Extract ETO Order of Battle division data from PDF into typed CSV files."""

from __future__ import annotations

import argparse
import csv
import re
import sys
from dataclasses import dataclass
from pathlib import Path

import fitz

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PDF_PATH = (
    PROJECT_ROOT
    / "contentrepository/European Thater of Operations - Order of Battle/ETO_Order_of_Battle.pdf"
)
OUTPUT_DIR = PDF_PATH.parent

SCOPE_NOTE = (
    "ETO campaign scope only. Sicilian/Italian and North African campaign "
    "assignments are excluded from this volume but may be added later."
)

CANONICAL_DIVISIONS = [
    "1st Infantry Division",
    "2d Infantry Division",
    "3d Infantry Division",
    "4th Infantry Division",
    "5th Infantry Division",
    "9th Infantry Division",
    "13th Airborne Division",
    "17th Airborne Division",
    "26th Infantry Division",
    "28th Infantry Division",
    "29th Infantry Division",
    "30th Infantry Division",
    "35th Infantry Division",
    "36th Infantry Division",
    "42d Infantry Division",
    "44th Infantry Division",
    "45th Infantry Division",
    "63d Infantry Division",
    "65th Infantry Division",
    "66th Infantry Division",
    "69th Infantry Division",
    "70th Infantry Division",
    "71st Infantry Division",
    "75th Infantry Division",
    "76th Infantry Division",
    "78th Infantry Division",
    "79th Infantry Division",
    "80th Infantry Division",
    "82d Airborne Division",
    "83d Infantry Division",
    "84th Infantry Division",
    "86th Infantry Division",
    "87th Infantry Division",
    "89th Infantry Division",
    "90th Infantry Division",
    "94th Infantry Division",
    "95th Infantry Division",
    "97th Infantry Division",
    "99th Infantry Division",
    "100th Infantry Division",
    "101st Airborne Division",
    "102d Infantry Division",
    "103d Infantry Division",
    "104th Infantry Division",
    "106th Infantry Division",
    "1st Armored Division",
    "2d Armored Division",
    "3d Armored Division",
    "4th Armored Division",
    "5th Armored Division",
    "6th Armored Division",
    "7th Armored Division",
    "8th Armored Division",
    "9th Armored Division",
    "10th Armored Division",
    "11th Armored Division",
    "12th Armored Division",
    "13th Armored Division",
    "14th Armored Division",
    "16th Armored Division",
    "20th Armored Division",
]

DIVISION_LOOKUP = {}
for name in CANONICAL_DIVISIONS:
    key = re.sub(r"[^a-z0-9]+", "", name.lower())
    DIVISION_LOOKUP[key] = name

MONTHS = (
    "jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec|"
    "january|february|march|april|june|july|august|september|"
    "october|november|december|"
    "lee|hay|tan|bee|liar|my|ug|pr|kar|jen|dig|m|iiar"
)
DATE_RE = re.compile(
    rf"^\s*(\d{{1,2}}\s+(?:{MONTHS})\.?\s+\d{{2,4}})\s*$",
    re.I,
)
DATE_INLINE_RE = re.compile(
    rf"^(\d{{1,2}}\s+(?:{MONTHS})\.?\s+\d{{2,4}})\s+(.+)$",
    re.I,
)
DATE_RANGE_RE = re.compile(
    rf"(\d{{1,2}}\s+(?:{MONTHS})\.?\s+\d{{2,4}})\s*\.?\s*[-–—~]\s*"
    rf"(\d{{1,2}}\s+(?:{MONTHS})\.?\s+\d{{2,4}})",
    re.I,
)
TRAILING_DATE_PAIR_RE = re.compile(
    rf"(\d{{1,2}}\s+(?:{MONTHS})\.?\s+\d{{2,4}})\s+"
    rf"(\d{{1,2}}\s+(?:{MONTHS})\.?\s+\d{{2,4}})\s*$",
    re.I,
)
DATE_TO_LINE_RE = re.compile(
    rf"^[-–—~_\s]+(\d{{1,2}}\s+(?:{MONTHS})\.?\s+\d{{2,4}})",
    re.I,
)
MONTH_YEAR_ONLY_RE = re.compile(
    rf"^(?:{MONTHS})\.?\s+\d{{2,4}}$",
    re.I,
)
RANK_RE = re.compile(
    r"^(Gen|Lt Gen|Maj Gen|Brig Gen|Col|Lt Col|Lt\. Col|It Col|Lb Col|"
    r"Maj|1-Iaj|Haj|iu'aj|kaj|Capt|Lt|1st Lt|2d Lt|WOJG)\b",
    re.I,
)
POSITION_ROLES = {
    "Commanding General": "Division commander.",
    "Assistant Division Commander": "Deputy division commander.",
    "Artillery Commander": "Commander, division artillery.",
    "Chief of Staff": "Principal staff officer to the commanding general.",
    "ACofS G-1 (Personnel)": (
        "G1 (Personnel): manpower management, personnel services, casualty operations."
    ),
    "ACofS G-2 (Intelligence)": (
        "G2 (Intelligence): gathering, analyzing, and disseminating intelligence."
    ),
    "ACofS G-3 (Operations)": "G3 (Operations): current operations, training, and exercises.",
    "ACofS G-4 (Logistics)": (
        "G4 (Logistics): supply, maintenance, transportation, and logistical support."
    ),
    "ACofS G-5 (Plans)": (
        "G5 (Plans): long-range plans, policies, and civil-military operations."
    ),
    "ACofS G-6 (Communications)": "G6 (Communications): communications and information systems.",
    "ACofS G-7 (Training)": "G7 (Training): simulations; sometimes combined with G3 as G3/7.",
    "ACofS G-8 (Resource Management)": "G8 (Resource Management): budget and financial management.",
    "ACofS G-9 (Civil Affairs)": "G9 (Civil Affairs): civil affairs section or equivalent.",
    "Adjutant General": "Personnel and administrative services officer.",
}

POSITION_ALIASES = {
    "coaicig gen": "Commanding General",
    "comdg gen": "Commanding General",
    "asst div comdr": "Assistant Division Commander",
    ".nsst div comdr": "Assistant Division Commander",
    "arty comdr": "Artillery Commander",
    "gofs": "Chief of Staff",
    "cofs": "Chief of Staff",
    "i.cof s g-l": "ACofS G-1 (Personnel)",
    "acofs g-l": "ACofS G-1 (Personnel)",
    "acofs g~2": "ACofS G-2 (Intelligence)",
    "acofs g-2": "ACofS G-2 (Intelligence)",
    "acof s g-2": "ACofS G-2 (Intelligence)",
    "acofs g-3": "ACofS G-3 (Operations)",
    "acofs g~3": "ACofS G-3 (Operations)",
    "acofs g-4": "ACofS G-4 (Logistics)",
    "acofs g~4": "ACofS G-4 (Logistics)",
    "acofs g-5": "ACofS G-5 (Plans)",
    "acofs c-4": "ACofS G-4 (Logistics)",
    "acofs g~5": "ACofS G-5 (Plans)",
    "adj gen": "Adjutant General",
    "co 16th inf": "CO 16th Infantry",
    "co 18th inf": "CO 18th Infantry",
    "co 26th inf": "CO 26th Infantry",
    "co 7th inf": "CO 7th Infantry",
    "co 15th inf": "CO 15th Infantry",
    "co 30th inf": "CO 30th Infantry",
    "gg 30th inf": "CO 30th Infantry",
}

ATTACHMENT_CATEGORY_DISPLAY = {
    "antiaircraftartillery": "Antiaircraft Artillery",
    "armored": "Armored",
    "amored": "Armored",
    "amiored": "Armored",
    "cavalry": "Cavalry",
    "chemical": "Chemical",
    "engineer": "Engineer",
    "fieldartillery": "Field Artillery",
    "infantry": "Infantry",
    "medical": "Medical",
    "ordnance": "Ordnance",
    "quartermaster": "Quartermaster",
    "signal": "Signal",
    "tankdestroyer": "Tank Destroyer",
    "tankkestreyer": "Tank Destroyer",
    "navy": "Naval",
    "naval": "Naval",
    "miscellaneous": "Miscellaneous",
}

ATTACHMENT_CATEGORIES = {
    "antiaircraft artillery",
    "armored",
    "amored",
    "amiored",
    "cavalry",
    "chemical",
    "engineer",
    "field artillery",
    "field art i l l e r y",
    "infantry",
    "medical",
    "ordnance",
    "quartermaster",
    "signal",
    "tank destroyer",
    "tank kestreyer",
    "tank lestroyer",
    "navy",
    "naval",
    "miscellaneous",
}

SECTION_PATTERNS = {
    "command_staff": re.compile(r"command.*staff", re.I),
    "statistics": re.compile(r"^[\s•\*\-^]*stati[\s\-]*stics?\d*", re.I),
    "organic_units": re.compile(
        r"organic\s+u[sn\^]i|ohgahic\s+u|o[h0]g[a4]h[i1l]?c\s+u"
        r"|qb&?ajmi['c]*\s+units"
        r"|orga\w*[\s\-]*c\s+\S*x?j[i1l]"
        r"|organic\s+u\^?i"
        r"|organic\s+touts?"
        r"|orgatic\s+units"
        r"|orgamtd\s*,?\s*units"
        r"|organic\s+i?m[iu][ts]{2}"
        r"|organic\s*\.?\s*pi?ts"
        r"|orga[nai][in]c.*\bu[1n\^]?\w*t",
        re.I,
    ),
    "attachments": re.compile(r"attach", re.I),
    "detachments": re.compile(r"\bdetachments?\b|\(attached\s+to\)", re.I),
    "higher_units": re.compile(r"assignment.*attachment", re.I),
    "command_posts": re.compile(r"command\s+posts?", re.I),
}

SKIP_LINE_RE = re.compile(
    r"^-?\s*\d+\s*-$|property of|^\s*$|^\*+\s*$|composition|chronology|"
    r"campaigns|individual awards|casualties|attached to|to higher units|"
    r"date$|town$|region$|country$|dates$|corps$|army$|army group",
    re.I,
)
SOURCE_PAGE_LINE_RE = re.compile(
    r"^[-_—\s]*(\d{1,4})\s*[-_—]?\s*$",
)


def extract_source_page(page_text: str) -> str:
    """Return manuscript page number printed in the original document margin."""
    lines = [clean_text(line) for line in page_text.splitlines() if clean_text(line)]
    for line in reversed(lines[-8:]):
        match = SOURCE_PAGE_LINE_RE.match(line)
        if match:
            return match.group(1)
    matches = re.findall(r"[-_—«»]\s*(\d{1,4})\s*[-_—]", page_text)
    return matches[-1] if matches else ""


def build_page_lines(page_text: str) -> list[tuple[int, str]]:
    """Return (1-based raw page line number, cleaned text) for non-empty lines."""
    page_lines: list[tuple[int, str]] = []
    for line_num, raw_line in enumerate(page_text.splitlines(), start=1):
        line = clean_text(raw_line)
        if line:
            page_lines.append((line_num, line))
    return page_lines


def with_page_fields(
    row: dict, source_page: str, pdf_page: int, source_line: int = 0
) -> dict:
    enriched = dict(row)
    enriched["source_page"] = source_page
    enriched["pdf_page"] = str(pdf_page) if pdf_page else ""
    enriched["source_line"] = str(source_line) if source_line else ""
    return enriched


def clean_text(text: str) -> str:
    text = text.replace("\u00a0", " ")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def normalize_date_ocr(line: str) -> str:
    line = clean_text(line)
    for month in (
        "January",
        "February",
        "March",
        "April",
        "May",
        "June",
        "July",
        "August",
        "September",
        "October",
        "November",
        "December",
        "Jan",
        "Feb",
        "Mar",
        "Apr",
        "May",
        "Jun",
        "Jul",
        "Aug",
        "Sep",
        "Oct",
        "Nov",
        "Dec",
    ):
        spaced = r"\s*".join(month)
        line = re.sub(spaced, month, line, flags=re.I)
    line = re.sub(r"a«ir", "Mar", line, flags=re.I)
    line = re.sub(r"\biar\b", "Mar", line, flags=re.I)
    line = re.sub(r"liar", "Mar", line, flags=re.I)
    line = re.sub(r"\?\.iar", "Mar", line, flags=re.I)
    line = re.sub(r"hay", "May", line, flags=re.I)
    line = re.sub(r"lee", "Dec", line, flags=re.I)
    line = re.sub(r"jnug", "Aug", line, flags=re.I)
    line = re.sub(r"\^ug", "Aug", line, flags=re.I)
    line = re.sub(r"dig", "Aug", line, flags=re.I)
    line = re.sub(r"\bRar\b", "Mar", line, flags=re.I)
    line = re.sub(r"\bKar\b", "Mar", line, flags=re.I)
    line = re.sub(r"\bS3\s+(?:Liar|Mar)\b", "23 Mar", line, flags=re.I)
    line = re.sub(r"\b81\s+(?:Kar|Mar)\b", "21 Mar", line, flags=re.I)
    line = re.sub(r"\bFar\b", "Mar", line, flags=re.I)
    line = re.sub(r"\bAar\b", "Mar", line, flags=re.I)
    line = re.sub(r"\btar\b", "Mar", line, flags=re.I)
    line = re.sub(r"(?<![Aa])ug\b", "Aug", line, flags=re.I)
    line = re.sub(r"(?<![Aa])pr\b", "Apr", line, flags=re.I)
    line = re.sub(r"(\d{1,2}\s+Jun)\s+M\s*$", r"\1 44", line, flags=re.I)
    line = re.sub(
        rf"(\d{{1,2}})\.((?:{MONTHS}))\b",
        r"\1 \2",
        line,
        flags=re.I,
    )
    line = re.sub(
        rf"(\d{{1,2}}\s+(?:{MONTHS}))[-–—~\s]*kk\b",
        r"\1 44",
        line,
        flags=re.I,
    )
    line = re.sub(r"\bkk\b", "44", line, flags=re.I)
    line = re.sub(r"\b51\s+Dec\b", "1 Dec", line, flags=re.I)
    line = re.sub(r"\.Jan\b", " Jan", line, flags=re.I)
    line = re.sub(r"\b2k\s+Apr\b", "24 Apr", line, flags=re.I)
    line = re.sub(r"\bJA\s+Mar\b", "14 Mar", line, flags=re.I)
    line = re.sub(r"\b1\^\s*Mar\b", "14 Mar", line, flags=re.I)
    line = re.sub(r">\s*Jan\b", "8 Jan", line, flags=re.I)
    line = re.sub(r"\^5\b", "45", line)
    line = re.sub(r"\bU5\b", "45", line)
    line = re.sub(r"\bk5\b", "45", line, flags=re.I)
    line = re.sub(r"k[$]", "45", line)
    line = re.sub(r"\bky\b", "45", line, flags=re.I)
    line = re.sub(r"i£", "45", line)
    line = re.sub(
        rf"((?:{MONTHS}))\.?\s*[\^]5\b",
        r"\1 45",
        line,
        flags=re.I,
    )
    line = re.sub(
        rf"((?:{MONTHS}))\.?\s*U5\b",
        r"\1 45",
        line,
        flags=re.I,
    )
    line = re.sub(
        rf"((?:{MONTHS}))\.?\s*k[$]",
        r"\1 45",
        line,
        flags=re.I,
    )
    line = re.sub(
        rf"((?:{MONTHS}))\.?\s*ky\b",
        r"\1 45",
        line,
        flags=re.I,
    )
    line = re.sub(
        rf"((?:{MONTHS}))\.?\s*i£\b",
        r"\1 45",
        line,
        flags=re.I,
    )
    line = re.sub(
        rf"((?:{MONTHS}))\.?\s*k5\b",
        r"\1 45",
        line,
        flags=re.I,
    )
    line = re.sub(r"(\d)\s+(\d{2,4})$", r"\1 \2", line)
    line = re.sub(r"(\d)\s+(\d)\s*$", r"\1\2", line)
    return line


def parse_attachment_date_line(line: str) -> str | None:
    """Parse a columnar attachment date line (from or to) with OCR noise."""
    normalized = normalize_date_ocr(line)
    normalized = re.sub(r"^[\s.,;:'\"*•\-–—~_]+", "", normalized)
    normalized = re.sub(r"[\s.,;:'\"*•\-–—~_]+$", "", normalized)
    for pattern in (DATE_RE, DATE_TO_LINE_RE):
        match = pattern.match(normalized)
        if match:
            return clean_text(match.group(1))
    month_year = re.match(rf"^((?:{MONTHS})\.?\s+\d{{2,4}})$", normalized, re.I)
    if month_year:
        return clean_text(month_year.group(1))
    embedded = re.search(
        rf"(\d{{1,2}}\s+(?:{MONTHS})\.?\s+\d{{2,4}})",
        normalized,
        re.I,
    )
    if embedded:
        return clean_text(embedded.group(1))
    day_month = re.search(rf"^(\d{{1,2}}\s+(?:{MONTHS}))\b", normalized, re.I)
    if day_month:
        return clean_text(day_month.group(1))
    return None


def extract_attachment_date_pair(text: str) -> tuple[str, str]:
    """Pull date_from/date_to from a noisy OCR date blob."""
    normalized = normalize_date_ocr(text)
    normalized = re.sub(r'["\']', "", normalized)
    normalized = re.sub(
        r"(\d)\s+(\d)\s+((?:{MONTHS}))".format(MONTHS=MONTHS),
        r"\1\2 \3",
        normalized,
        flags=re.I,
    )
    normalized = re.sub(r"-\s*0\s+(\d)", r"- \1", normalized)
    normalized = re.sub(r"[-–—~/\\]+", "-", normalized)
    full_dates = re.findall(
        rf"\d{{1,2}}\s+(?:{MONTHS})\.?\s+\d{{2,4}}",
        normalized,
        re.I,
    )
    full_dates = [clean_text(value) for value in full_dates]
    if len(full_dates) >= 2:
        return full_dates[0], full_dates[-1]
    if len(full_dates) == 1:
        start_partial = re.search(rf"(\d{{1,2}}\s+(?:{MONTHS}))", normalized, re.I)
        year_match = re.search(r"\d{2,4}", full_dates[0])
        if start_partial and year_match:
            start = clean_text(f"{start_partial.group(1)} {year_match.group(0)}")
            if start != full_dates[0]:
                return start, full_dates[0]
        return full_dates[0], ""
    return "", ""


def is_full_attachment_date(value: str) -> bool:
    return parse_full_attachment_date(value) is not None


def _infer_start_year(from_month: str, end_month: str, end_year: str) -> str:
    end_year_full = end_year if len(end_year) == 4 else f"19{end_year}"
    end_month_key = end_month[:3].lower()
    from_month_key = from_month[:3].lower()
    from_month_num = {
        "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
        "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
    }.get(from_month_key, 0)
    end_month_num = {
        "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
        "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
    }.get(end_month_key, 0)
    year = int(end_year_full)
    if from_month_num and end_month_num and from_month_num > end_month_num:
        year -= 1
    return str(year)[-2:]


def parse_inline_from_range(
    from_norm: str, to_norm: str
) -> tuple[str, str] | None:
    """Parse date range embedded in the from column (with optional year-only to)."""
    stripped = clean_text(from_norm)
    range_match = DATE_RANGE_RE.search(stripped)
    if range_match:
        return clean_text(range_match.group(1)), clean_text(range_match.group(2))
    partial_end = re.search(
        rf"(\d{{1,2}}\s+(?:{MONTHS})\.?\s+\d{{2,4}})\s*[-–—~]\s*"
        rf"(\d{{1,2}}\s+(?:{MONTHS}))\s*$",
        stripped,
        re.I,
    )
    if partial_end:
        start = clean_text(partial_end.group(1))
        end_bits = parse_full_attachment_date(start)
        if end_bits:
            _day, _month, year = end_bits
            end = clean_text(f"{partial_end.group(2)} {year}")
            return start, end
    if re.match(r"^45$", clean_text(to_norm)):
        return parse_inline_from_range(stripped, "")
    return None


def parse_start_month_end_full_columns(
    from_norm: str, to_norm: str
) -> tuple[str, str] | None:
    """Parse from=day month only with to=full end date (e.g. 26 Nov / - 24 Apr 45)."""
    start_match = re.match(
        rf"^(\d{{1,2}}\s+(?:{MONTHS}))\s*$",
        clean_text(from_norm),
        re.I,
    )
    end_text = clean_text(to_norm)
    end_text = re.sub(r"^[-–—~]\s*", "", end_text)
    end_full = parse_attachment_date_line(end_text)
    if not start_match or not end_full:
        return None
    end_bits = parse_full_attachment_date(end_full)
    if not end_bits:
        return None
    _ed, end_month, end_year = end_bits
    start_day, start_month = start_match.group(1).split(maxsplit=1)
    start_year = _infer_start_year(start_month, end_month, end_year)
    return (
        clean_text(f"{start_day} {start_month} {start_year}"),
        end_full,
    )


def parse_orphan_end_day_columns(
    from_norm: str, to_norm: str
) -> tuple[str, str] | None:
    """Parse from column ending in - DD with to column month+year only."""
    orphan = re.search(
        rf"(\d{{1,2}}\s+(?:{MONTHS})\.?\s+\d{{2,4}})\s*[-–—~]\s*(\d{{1,2}})\s*$",
        from_norm,
        re.I,
    )
    month_year = MONTH_YEAR_ONLY_RE.match(clean_text(to_norm))
    if orphan and month_year:
        return (
            clean_text(orphan.group(1)),
            clean_text(f"{orphan.group(2)} {month_year.group(0)}"),
        )
    return None


def parse_attachment_date_columns(from_raw: str, to_raw: str) -> tuple[str, str]:
    """Parse attachment date_from/date_to from middle and right PDF columns."""
    from_norm = normalize_date_ocr(from_raw)
    to_norm = normalize_date_ocr(to_raw)
    split_end = parse_orphan_end_day_columns(from_norm, to_norm)
    if split_end:
        return split_end
    inline_from = parse_inline_from_range(from_norm, to_norm)
    if inline_from:
        return inline_from
    month_start = parse_start_month_end_full_columns(from_norm, to_norm)
    if month_start:
        return month_start

    date_from = parse_attachment_date_line(from_norm) or ""
    date_to = parse_attachment_date_line(to_norm) or ""
    if (
        date_from
        and date_to
        and is_full_attachment_date(date_from)
        and is_full_attachment_date(date_to)
    ):
        return date_from, date_to
    if date_to and not is_full_attachment_date(date_to):
        date_to = ""

    combined = clean_text(f"{from_norm} {to_norm}")
    combined = re.sub(r"^[\s.,;:'\"*•\-–—~_/\\]+", "", combined)
    combined = re.sub(r"[\s.,;:'\"*•]+$", "", combined)
    combined = re.sub(r"[-–—~/\\]+", "-", combined)

    for pattern in (DATE_RANGE_RE, TRAILING_DATE_PAIR_RE):
        match = pattern.search(combined)
        if match:
            return clean_text(match.group(1)), clean_text(match.group(2))

    same_month = re.search(
        rf"(\d{{1,2}}\s+(?:{MONTHS})\.?\s+\d{{2,4}})\s*-\s*"
        rf"((?:{MONTHS})\.?\s+\d{{2,4}})\s*$",
        combined,
        re.I,
    )
    if same_month:
        start = clean_text(same_month.group(1))
        end_partial = clean_text(same_month.group(2))
        day_match = re.match(r"(\d{1,2})\s", start)
        end = (
            clean_text(f"{day_match.group(1)} {end_partial}")
            if day_match
            else end_partial
        )
        return start, end

    split_year = re.search(
        rf"(\d{{1,2}}\s+(?:{MONTHS}))\.?\s+(\d{{2,4}})\s*-\s*"
        rf"(\d{{1,2}}\s+)?((?:{MONTHS})\.?\s+\d{{2,4}})",
        combined,
        re.I,
    )
    if split_year:
        start = clean_text(f"{split_year.group(1)} {split_year.group(2)}")
        end = clean_text(f"{split_year.group(3) or ''}{split_year.group(4)}")
        return start, end

    if not date_from or not date_to:
        inline = extract_attachment_inline_row(combined, 0)
        if inline and inline[1]:
            if not date_from:
                date_from = inline[1]
            if not date_to:
                date_to = inline[2]

    if date_from and not date_to:
        month_year = re.search(rf"((?:{MONTHS})\.?\s+\d{{2,4}})$", to_norm, re.I)
        if month_year:
            partial = clean_text(month_year.group(1))
            day_match = re.match(r"(\d{1,2})\s", date_from)
            date_to = (
                clean_text(f"{day_match.group(1)} {partial}")
                if day_match
                else partial
            )

    if not date_from:
        date_from = parse_attachment_date_line(combined) or ""

    if date_from and not date_to and to_norm.strip():
        date_to = parse_attachment_date_line(to_norm) or date_to

    if not (date_from and date_to):
        pair = extract_attachment_date_pair(combined)
        if not date_from and pair[0]:
            date_from = pair[0]
        if not date_to and pair[1]:
            date_to = pair[1]

    if not date_from and date_to:
        to_bits = parse_full_attachment_date(date_to)
        from_partial = re.search(
            rf"(\d{{1,2}}\s+(?:{MONTHS}))\b",
            from_norm,
            re.I,
        )
        if to_bits and from_partial:
            _day, month, year = to_bits
            from_day = int(from_partial.group(1).split()[0])
            date_from = clean_text(f"{from_day} {month} {year}")

    if date_from and not date_to:
        to_partial = parse_attachment_date_line(to_norm)
        if to_partial:
            date_to = to_partial

    if date_from and not date_to:
        from_bits = parse_full_attachment_date(date_from)
        to_day_month = re.search(
            rf"^(\d{{1,2}})\s+((?:{MONTHS}))\b",
            to_norm,
            re.I,
        )
        if from_bits and to_day_month:
            _day, month, year = from_bits
            date_to = clean_text(
                f"{to_day_month.group(1)} {to_day_month.group(2)} {year}"
            )

    return date_from, date_to


def parse_full_attachment_date(value: str) -> tuple[int, str, str] | None:
    """Return (day, month_token, year) when value is a full attachment date."""
    match = re.match(
        rf"^(\d{{1,2}})\s+((?:{MONTHS}))\.?\s+(\d{{2,4}})$",
        clean_text(value),
        re.I,
    )
    if not match:
        return None
    return int(match.group(1)), match.group(2), match.group(3)


def _collect_attachment_spans(fitz_page) -> list[tuple[float, float, str]]:
    spans: list[tuple[float, float, str]] = []
    for block in fitz_page.get_text("dict").get("blocks", []):
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                text = span.get("text", "")
                if text.strip():
                    spans.append((span["bbox"][1], span["bbox"][0], text))
    spans.sort()
    return spans


def _empty_attachment_band(y: float) -> dict[str, object]:
    return {
        "y": y,
        "unit": [],
        "from": [],
        "to": [],
        "dates": [],
        "attached_to": [],
    }


def _cluster_attachment_row_bands(
    spans: list[tuple[float, float, str]],
    y_tolerance: float,
    *,
    column_key=None,
) -> list[dict[str, object]]:
    if not spans:
        return []
    key_fn = column_key or _attachment_column_key
    bands: list[dict[str, object]] = []
    current = _empty_attachment_band(spans[0][0])
    for y, x, text in spans:
        if y - current["y"] > y_tolerance:  # type: ignore[operator]
            bands.append(current)
            current = _empty_attachment_band(y)
        column = key_fn(x)
        if column:
            current[column].append((x, text))  # type: ignore[index]
    bands.append(current)
    return bands


def _find_detachment_section_y(spans: list[tuple[float, float, str]]) -> float | None:
    for y, _x, text in spans:
        if _detachment_section_marker(text):
            return y
    return None


def _attached_to_section_marker(text: str) -> bool:
    collapsed = re.sub(r"[^A-Za-z\s]", "", clean_text(text)).strip().lower()
    return collapsed in {"attached to", "attached tp", "attached tof"}


def _group_spans_into_lines(
    spans: list[tuple[float, float, str]],
    *,
    y_tolerance: float = 2.0,
) -> list[tuple[float, str]]:
    if not spans:
        return []
    lines: list[tuple[float, list[tuple[float, str]]]] = []
    for y, x, text in spans:
        if lines and y - lines[-1][0] <= y_tolerance:
            lines[-1][1].append((x, text))
        else:
            lines.append((y, [(x, text)]))
    return [
        (line_y, clean_text("".join(part for _, part in sorted(parts))))
        for line_y, parts in lines
    ]


def _find_attached_to_section_y(spans: list[tuple[float, float, str]]) -> float | None:
    for y, text in _group_spans_into_lines(spans):
        if _attached_to_section_marker(text):
            return y
    return None


def is_detachment_header_line(line: str) -> bool:
    if _detachment_section_marker(line):
        return True
    if re.match(r"^\(attached\s+to\)\s*$", clean_text(line), re.I):
        return True
    return False


def normalize_combat_command_parent(unit_name: str) -> str:
    """Strip OCR junk from combat-command header text used as parent_unit_name."""
    text = normalize_cc_names(unit_name)
    match = re.match(r"^(CC[ABR]\s*\([^)]+\))", text, flags=re.I)
    if match:
        return clean_text(match.group(1))
    match = re.match(r"^(CC[ABR])\b", text, flags=re.I)
    if match:
        return match.group(1).upper()
    return text


def is_combat_command_unit(unit_name: str) -> bool:
    text = clean_text(unit_name)
    if not COMBAT_COMMAND_UNIT_RE.match(text):
        return False
    if re.search(r"\bComdr\b", text, flags=re.I):
        return False
    if DATE_RE.search(text) or DATE_INLINE_RE.search(text):
        return False
    return True


ATTACHMENT_GROUP_MARKER_RE = re.compile(r"\b(?:Group|Gp)\b", re.I)
ATTACHMENT_GROUP_HQ_PREFIX_RE = re.compile(
    r"^(?:Hq|Ha|He|Eq|Ilq|Tr\s+E)\b",
    re.I,
)
ATTACHMENT_SUBORDINATE_UNIT_RE = re.compile(
    r"\b(?:"
    r"Field Artillery\s+Battalion|FA\s+Bn|Fa\s+(?:En|Bn)|"
    r"Cavalry\s+Reconnaissance\s+Squadron|Cav(?:alry)?\s+(?:Ren\s*)?(?:Squadron|Sq)|"
    r"Tank\s+Battalion|Tk\s+Bn|"
    r"(?:Mortar|Engineer|Chemical)\s+Battalion|"
    r"Battery|Btry|Squadron|Troop|Company|Co\b"
    r")\b",
    re.I,
)
ATTACHMENT_GROUP_FAMILY_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "field_artillery",
        re.compile(r"Field Artillery|FA\b|F\.?\s*A\.?\s+Group", re.I),
    ),
    ("cavalry", re.compile(r"Cavalry|Cav\b", re.I)),
    ("armored", re.compile(r"Armored|Armd|Amid", re.I)),
    ("antiaircraft", re.compile(r"Antiaircraft|AAA", re.I)),
    ("engineer", re.compile(r"Engineer|Eingr|Eng(?:ineer)?", re.I)),
)
ATTACHMENT_GROUP_CHILD_PATTERNS: dict[str, re.Pattern[str]] = {
    "field_artillery": re.compile(
        r"Field Artillery\s+Battalion|FA\s+Bn|Fa\s+(?:En|Bn)|"
        r"\(\d+\s*(?:How|Howitzer|Gun|How)",
        re.I,
    ),
    "cavalry": re.compile(
        r"Cavalry\s+Reconnaissance\s+Squadron|"
        r"Cav(?:alry)?\s+(?:Ren\s*-?\s*)?(?:Squadron|Sq)|"
        r"\d+\w*\s+Cav(?:alry)?\s+Reconnaissance\s+Squadron",
        re.I,
    ),
    "armored": re.compile(r"Tank\s+Battalion|Tk\s+Bn", re.I),
    "antiaircraft": re.compile(r"Antiaircraft\s+Artillery\s+Battalion|AAA\s+Bn", re.I),
    "engineer": re.compile(
        r"Engineer(?:\s+Combat)?\s+Battalion|Engineer\s+Company|Eng(?:ineer)?\s+Co",
        re.I,
    ),
}


@dataclass
class AttachmentParentState:
    combat_command: str | None = None
    group: str | None = None


def is_attachment_group_header(unit_name: str) -> bool:
    """True for group header rows (e.g. 188th Field Artillery Group / 4th Cav Gp)."""
    text = clean_text(unit_name)
    if not ATTACHMENT_GROUP_MARKER_RE.search(text):
        return False
    if _is_attachment_group_hq_row(unit_name):
        return False
    if ATTACHMENT_GROUP_HQ_PREFIX_RE.match(text):
        return False
    if re.search(r"\b(?:Battery|Btry)\b", text, flags=re.I):
        return False
    return True


def normalize_attachment_group_parent(unit_name: str) -> str:
    text = clean_text(unit_name)
    base = re.sub(r"\s*\(.*$", "", text).strip()
    base = re.sub(r"\bFA\s+Gp\b", "Field Artillery Group", base, flags=re.I)
    base = re.sub(r"\bCav\s+Gp\b", "Cavalry Group", base, flags=re.I)
    base = re.sub(r"\bF\.?\s*A\.?\s+Group\b", "Field Artillery Group", base, flags=re.I)
    base = re.sub(r"\bAmid\b", "Armored", base, flags=re.I)
    base = re.sub(r"\bArmd\b", "Armored", base, flags=re.I)
    base = re.sub(r"\bGp\.?\s*$", "Group", base, flags=re.I)
    base = re.sub(r"[,.\s]+$", "", base)
    return clean_text(base)


def _attachment_group_family(group_parent: str) -> str | None:
    text = clean_text(group_parent)
    for family, pattern in ATTACHMENT_GROUP_FAMILY_PATTERNS:
        if pattern.search(text):
            return family
    return None


def _is_attachment_group_child(unit_name: str, group_parent: str) -> bool:
    if is_attachment_group_header(unit_name):
        return False
    if _is_attachment_group_hq_row(unit_name):
        return False
    text = clean_text(unit_name)
    if re.search(r"\bObsn\b", text, flags=re.I):
        return False
    family = _attachment_group_family(group_parent)
    if family:
        child_pattern = ATTACHMENT_GROUP_CHILD_PATTERNS.get(family)
        if child_pattern and child_pattern.search(text):
            return True
    if ATTACHMENT_SUBORDINATE_UNIT_RE.search(text):
        return not ATTACHMENT_GROUP_MARKER_RE.search(text)
    return False


def _is_attachment_group_hq_row(unit_name: str) -> bool:
    text = clean_text(unit_name)
    return bool(
        re.search(r"\bHq\s*&\s*Hq\b", text, flags=re.I)
        and ATTACHMENT_GROUP_MARKER_RE.search(text)
    )


def _is_standalone_attachment_unit(unit_name: str) -> bool:
    """True when a top-column row is a direct division attachment, not a CC sub-unit."""
    text = clean_text(unit_name)
    if re.match(r"^(?:Co|Cos|Companies?)\b", text, flags=re.I):
        return False
    if is_combat_command_unit(text):
        return False
    if re.search(r"\b(?:Tank|Tk)\s*(?:Battalion|Bn)\b", text, flags=re.I):
        return True
    if re.search(r"\bArmored\s+Group\b", text, flags=re.I):
        return True
    if re.match(r"^Task\s+Force\b", text, flags=re.I):
        return True
    return False


def resolve_attachment_parent(
    unit_name: str,
    state: AttachmentParentState,
    *,
    unit_x_min: float | None = None,
) -> tuple[AttachmentParentState, str]:
    """Return updated parent state and parent_unit_name for the row."""
    if is_combat_command_unit(unit_name):
        return (
            AttachmentParentState(
                combat_command=normalize_combat_command_parent(unit_name),
                group=None,
            ),
            "",
        )
    if is_attachment_group_header(unit_name):
        # Parenthetical group rows (e.g. 4th Cav Gp (24th Sq)) are self-contained.
        group_parent = (
            None
            if "(" in clean_text(unit_name)
            else normalize_attachment_group_parent(unit_name)
        )
        return (
            AttachmentParentState(combat_command=None, group=group_parent),
            "",
        )
    if _is_attachment_group_hq_row(unit_name):
        return AttachmentParentState(combat_command=state.combat_command, group=None), ""

    if state.group and _is_attachment_group_child(unit_name, state.group):
        return state, state.group

    if state.combat_command:
        if unit_x_min is not None and unit_x_min <= ATTACHMENT_TOP_LEVEL_X_MAX:
            if _is_standalone_attachment_unit(unit_name):
                return AttachmentParentState(group=state.group), ""
        return state, state.combat_command

    return state, ""


def apply_attachment_parents_to_rows(rows: list[dict]) -> list[dict]:
    """Assign parent_unit_name from Group/Gp and combat-command hierarchy."""
    updated: list[dict] = []
    state = AttachmentParentState()
    current_scope: tuple[str, str, int] | None = None

    for row in rows:
        scope = (
            row.get("division", ""),
            row.get("category", ""),
            int(row.get("pdf_page") or 0),
        )
        if current_scope != scope:
            state = AttachmentParentState()
            current_scope = scope
        state, parent_unit_name = resolve_attachment_parent(row.get("unit_name", ""), state)
        new_row = dict(row)
        new_row["parent_unit_name"] = parent_unit_name
        updated.append(new_row)
    return updated


def _band_unit_x_min(band: dict[str, object]) -> float | None:
    parts = band.get("unit")
    if not parts:
        return None
    meaningful = [
        (x, text)
        for x, text in parts  # type: ignore[misc]
        if re.search(r"[A-Za-z0-9]", text)
    ]
    if not meaningful:
        return min(x for x, _ in parts)  # type: ignore[misc]
    return min(x for x, _ in meaningful)


def collapse_spaced_ocr_tokens(text: str) -> str:
    """Collapse OCR like 'F i e l d' into 'Field'."""

    def repl(match: re.Match[str]) -> str:
        return match.group(0).replace(" ", "")

    return re.sub(r"(?:\b[A-Za-z]\s+){2,}[A-Za-z]\b", repl, text)


def normalize_organic_unit_name(text: str) -> str:
    text = re.sub(r"^['\"\s]+", "", text)
    text = normalize_cc_names(text)
    text = re.sub(r"#\s*i\s*e\s*l\s*d", "Field", text, flags=re.I)
    text = re.sub(r"\^i\.?\s*e\s*l\s*d", "Field", text, flags=re.I)
    text = re.sub(r"\(Mecz\)", "(Mech)", text, flags=re.I)
    text = re.sub(r"\(Mepz\)", "(Mech)", text, flags=re.I)
    text = re.sub(r"78th\$econnaissance", "78th Reconnaissance", text, flags=re.I)
    text = re.sub(r"23M\s*\?MX\$Xm.*", "78th Division Artillery", text, flags=re.I)
    text = re.sub(r"Battaliont<(\d+)", r"Battalion (\1", text, flags=re.I)
    text = re.sub(r"Battalion\^(\d+)", r"Battalion (\1", text, flags=re.I)
    text = re.sub(r"\b903rd\b", "903d", text, flags=re.I)
    text = re.sub(r"Special g\W+\w*", "Special Troops", text, flags=re.I)
    text = re.sub(r"\bbattalion\b", "Battalion", text)
    text = re.sub(r"\?9th\b", "29th", text, flags=re.I)
    text = re.sub(r"\bdroops\b", "Troops", text, flags=re.I)
    text = re.sub(r"\bIilitary\b", "Military", text, flags=re.I)
    text = re.sub(r"°rdnance", "Ordnance", text, flags=re.I)
    text = re.sub(r'Artillery"\^attalion', "Artillery Battalion", text, flags=re.I)
    text = re.sub(r"\$\s*i\s*l\s*t\s*h\s+Medical", "111th Medical", text, flags=re.I)
    text = re.sub(r"1-55", "155", text, flags=re.I)
    text = re.sub(r'""+', "", text)
    text = re.sub(
        r"Ordnai\^eLllghtriMaintenahce Gampanyr",
        "Ordnance Light Maintenance Company",
        text,
        flags=re.I,
    )
    text = re.sub(r"Battalion\"\(", "Battalion (", text, flags=re.I)
    text = re.sub(r"\b(\d{2,3})&\s+Infantry", r"\1d Infantry", text, flags=re.I)
    text = re.sub(r"[\^E]ield", "Field", text, flags=re.I)
    text = re.sub(r"\^ight", "Light", text, flags=re.I)
    text = re.sub(r"\bI32d\b", "132d", text, flags=re.I)
    text = re.sub(r"\b43d\s+Reconnaissance\s+Troop\b", "42d Reconnaissance Troop", text, flags=re.I)
    text = re.sub(r"\b42nd\s+Division\s+Artillery\b", "42d Division Artillery", text, flags=re.I)
    text = re.sub(r"Comp'any", "Company", text, flags=re.I)
    text = re.sub(r"\binfantry\b", "Infantry", text)
    text = re.sub(r"114£h", "114th", text, flags=re.I)
    text = re.sub(r"\b44tli\b", "44th", text, flags=re.I)
    text = re.sub(
        r"\b44th\s+Reconnaissance\s+Combat\s+Battalion\b",
        "44th Reconnaissance Troop (Mech)",
        text,
        flags=re.I,
    )
    text = re.sub(r"JJ?'ield", "Field", text, flags=re.I)
    text = re.sub(r"\*'ield", "Field", text, flags=re.I)
    text = re.sub(r"\^aartermaster", "Quartermaster", text, flags=re.I)
    text = re.sub(r"\.350th", "330th", text, flags=re.I)
    text = re.sub(r"5\.08th", "324th", text, flags=re.I)
    text = re.sub(r"\.(\d+(?:st|nd|rd|th|d)\b)", r"\1", text, flags=re.I)
    text = re.sub(r"Trooi\)?", "Troop", text, flags=re.I)
    text = re.sub(r"J\?iei&", "Field", text, flags=re.I)
    text = re.sub(r"Comnany", "Company", text, flags=re.I)
    text = re.sub(r"Quartermaster-\s+", "Quartermaster ", text, flags=re.I)
    text = re.sub(r"Signal'\s+", "Signal ", text, flags=re.I)
    text = re.sub(r"\b563d\s+Signal\b", "63d Signal", text, flags=re.I)
    text = re.sub(r"\b565th\s+Signal\b", "65th Signal", text, flags=re.I)
    text = re.sub(r"\b570th\s+Signal\b", "70th Signal", text, flags=re.I)
    text = re.sub(r"\b575th\s+Signal\b", "75th Signal", text, flags=re.I)
    text = re.sub(r"\b3:04th\b", "304th", text, flags=re.I)
    text = re.sub(r"\(3\.05\s+Howitzer\)", "(105 Howitzer)", text, flags=re.I)
    text = re.sub(r"ildintenance", "Maintenance", text, flags=re.I)
    text = re.sub(r"\b301st\s+Medical\b", "376th Medical", text, flags=re.I)
    text = re.sub(r"Troop-", "Troop", text, flags=re.I)
    text = re.sub(r"([a-z])\.(\s+[A-Za-z])", r"\1\2", text)
    text = re.sub(r"\bEield\b", "Field", text, flags=re.I)
    text = re.sub(r"Special,\s*droops", "Special Troops", text, flags=re.I)
    text = re.sub(r"Troop;", "Troop", text, flags=re.I)
    text = re.sub(r"Combat\s+\^\s*", "Combat ", text, flags=re.I)
    text = re.sub(r"Special,\s*Troops", "Special Troops", text, flags=re.I)
    text = re.sub(r"Special;Troops", "Special Troops", text, flags=re.I)
    text = re.sub(r"f\.3&\$k\s+Infantry", "313th Infantry", text, flags=re.I)
    text = re.sub(r"3ii-th\s+Infantry", "314th Infantry", text, flags=re.I)
    text = re.sub(r"\.(\d{2,3}d)\s+Engineer", r"\1 Engineer", text, flags=re.I)
    text = re.sub(r"\.(\d{2,3}d)\s+Medical", r"\1 Medical", text, flags=re.I)
    text = re.sub(r'Engineer"Combat', "Engineer Combat", text, flags=re.I)
    text = re.sub(r'Medical"\s+Battalion', "Medical Battalion", text, flags=re.I)
    text = re.sub(r"79th-\s+Division", "79th Division", text, flags=re.I)
    text = re.sub(r"Field-Artillery", "Field Artillery", text, flags=re.I)
    text = re.sub(r"Ba\^tali6n", "Battalion", text, flags=re.I)
    text = re.sub(r"\{iQ5", "(105", text, flags=re.I)
    text = re.sub(r"904th\.", "904th ", text, flags=re.I)
    text = re.sub(r"79th Division\s*•\s*Artillery", "79th Division Artillery", text, flags=re.I)
    text = re.sub(r"310th''Field", "310th Field", text, flags=re.I)
    text = re.sub(r"Artillery\.»\s*Battalion", "Artillery Battalion", text, flags=re.I)
    text = re.sub(r"Battalion';", "Battalion", text, flags=re.I)
    text = re.sub(r"Battalion':", "Battalion", text, flags=re.I)
    text = re.sub(r"312th;\s*Field Art'illary", "312th Field Artillery", text, flags=re.I)
    text = re.sub(r"['\",./\s]+Special;?\s*Troops.*", "Special Troops", text, flags=re.I)
    text = re.sub(r"\.»", "", text)
    text = re.sub(r"Artillery\.\(", "Artillery (", text, flags=re.I)
    text = re.sub(r"\s*\.;\s*•\s*$", "", text)
    text = re.sub(r"Field\s*:\s*Artillery", "Field Artillery", text, flags=re.I)
    text = re.sub(r"\b3l8th\b", "318th", text, flags=re.I)
    text = re.sub(r"31\^th:?\.", "314th", text, flags=re.I)
    text = re.sub(r"305t&", "305th", text, flags=re.I)
    text = re.sub(r"\.\.\s*Battalion", " Battalion", text, flags=re.I)
    text = re.sub(r"\bBoth\b", "80th", text, flags=re.I)
    text = re.sub(r"8C&h\b", "80th", text, flags=re.I)
    text = re.sub(r"\bIrifehtry\b", "Infantry", text, flags=re.I)
    text = re.sub(r"&4th\b", "84th", text, flags=re.I)
    text = re.sub(r"33\s*5th", "335th", text, flags=re.I)
    text = re.sub(r"\b3klst\b", "331st", text, flags=re.I)
    text = re.sub(r"3\^2d\b", "332d", text, flags=re.I)
    text = re.sub(r"3\^3&", "333rd", text, flags=re.I)
    text = re.sub(r",;+", "", text)
    text = re.sub(r"(\d{2,3}(?:st|nd|rd|th|d))\s+\^", r"\1", text, flags=re.I)
    text = re.sub(r"\^intenance", "Maintenance", text, flags=re.I)
    text = re.sub(r"\bkQktih\b", "333rd", text, flags=re.I)
    text = re.sub(r"311thoMedical", "311th Medical", text, flags=re.I)
    text = re.sub(r"309th•Medical", "309th Medical", text, flags=re.I)
    text = re.sub(r"Division\s+artillery\b", "Division Artillery", text, flags=re.I)
    text = re.sub(r"\b35\^th\b", "354th", text, flags=re.I)
    text = re.sub(r"\b31\^th\b", "314th", text, flags=re.I)
    text = re.sub(r"\b3\^0th\b", "340th", text, flags=re.I)
    text = re.sub(r"\b3\^1st\b", "341st", text, flags=re.I)
    text = re.sub(r"\b9lHh\b", "913th", text, flags=re.I)
    text = re.sub(r"\b3lHh\b", "314th", text, flags=re.I)
    text = re.sub(r"\b89th Bivision\b", "89th Division", text, flags=re.I)
    text = re.sub(r"\b71\^th\b", "789th", text, flags=re.I)
    text = re.sub(r"\^05th Quartermaster", "89th Quartermaster", text, flags=re.I)
    text = re.sub(r"^:\s+", "", text)
    text = re.sub(
        r"\b508th Field Artillery Battalion \(105",
        "324th Field Artillery Battalion (105",
        text,
        flags=re.I,
    )
    text = re.sub(r"3Q\.?8th\b", "308th", text, flags=re.I)
    text = re.sub(r"Engineer\s+-\.Combat-", "Engineer Combat", text, flags=re.I)
    text = re.sub(r"Medical'Battalion", "Medical Battalion", text, flags=re.I)
    text = re.sub(r"Artillery,Battalion", "Artillery Battalion", text, flags=re.I)
    text = re.sub(r"\(105;\s*Howitzer\)", "(105 Howitzer)", text, flags=re.I)
    text = re.sub(r":\s*3Q8th", "308th", text, flags=re.I)
    text = re.sub(r"\b508th Medical\b", "383rd Medical", text, flags=re.I)
    text = re.sub(r"\bird Division\.Artillery\b", "83d Division Artillery", text, flags=re.I)
    text = re.sub(r"\b522nd\b", "322nd", text, flags=re.I)
    text = re.sub(r"\b523rd\b", "323rd", text, flags=re.I)
    text = re.sub(r";524th\s+\.Field Artillery Battalion \(155", "908th Field Artillery Battalion (155", text, flags=re.I)
    text = re.sub(r"\b785d\b", "783rd", text, flags=re.I)
    text = re.sub(r"Special Trjiops", "Special Troops", text, flags=re.I)
    text = re.sub(r"Fie\s+id", "Field", text, flags=re.I)
    text = re.sub(r"Field\.''Artillery", "Field Artillery", text, flags=re.I)
    text = re.sub(r"83rd\"", "83d", text, flags=re.I)
    text = re.sub(r'Military\s+"Police', "Military Police", text, flags=re.I)
    text = re.sub(r"\b26lst\b", "261st", text, flags=re.I)
    text = re.sub(r"\bFlatbon\b", "Platoon", text, flags=re.I)
    text = re.sub(r"Troo-os", "Troops", text, flags=re.I)
    text = re.sub(r"5'ield", "Field", text, flags=re.I)
    text = re.sub(r"'-t'ield", "Field", text, flags=re.I)
    text = re.sub(r"(\d+(?:st|nd|rd|th))\s+'\s*", r"\1 ", text, flags=re.I)
    text = re.sub(r",\s*Battalion", " Battalion", text, flags=re.I)
    text = re.sub(r"['\"]+\s*$", "", text)
    text = re.sub(r"\b1Q5\b", "105", text, flags=re.I)
    text = re.sub(r"Arblllery", "Artillery", text, flags=re.I)
    text = re.sub(
        r"\b2\s*9\s*t\s*h\s*\.?\s*D\s*i\s*v\s*i\s*s\s*i\s*o\s*n\s*'?\s*A\s*r\s*t\s*i\s*l\s*l\s*e\s*r\s*y",
        "29th Division Artillery",
        text,
        flags=re.I,
    )
    text = collapse_spaced_ocr_tokens(text)
    text = re.sub(r"Field\s+Artilleryfattalion", "Field Artillery Battalion", text, flags=re.I)
    text = re.sub(r"Artilleryfattalion", "Artillery Battalion", text, flags=re.I)
    text = re.sub(r"Combat\s+attalion\b", "Combat Battalion", text, flags=re.I)
    text = re.sub(r"\bHeconnaissance\b", "Reconnaissance", text, flags=re.I)
    text = re.sub(r"\bffroops\b", "Troops", text, flags=re.I)
    text = re.sub(r"\bl05\b", "105", text, flags=re.I)
    text = re.sub(r"\bl55\b", "155", text, flags=re.I)
    text = re.sub(r"\b3\s*lst\b", "31st", text, flags=re.I)
    text = re.sub(r"B\.afrtaTion", "Battalion", text, flags=re.I)
    text = re.sub(r"\.Battalion", " Battalion", text, flags=re.I)
    text = re.sub(r"Battalion\.\(", "Battalion (", text, flags=re.I)
    text = re.sub(r"artillery\s*battalion", "Artillery Battalion", text, flags=re.I)
    text = re.sub(r"FieldArtillery", "Field Artillery", text, flags=re.I)
    text = re.sub(r"\bffth\s*\.?\s*Division\s+Artillery", "9th Division Artillery", text, flags=re.I)
    text = re.sub(
        r"(\d+(?:st|nd|rd|th|d)?)['\"]?\s*Division\s*\.?\s*Artillery",
        r"\1 Division Artillery",
        text,
        flags=re.I,
    )
    text = re.sub(r"Com\"bat", "Combat", text, flags=re.I)
    text = re.sub(r"Troo-ps", "Troops", text, flags=re.I)
    text = re.sub(r"\blight\b", "Light", text, flags=re.I)
    text = re.sub(r"(\d)(Field Artillery)", r"\1 \2", text, flags=re.I)
    text = re.sub(r"(\d{2,3}(?:st|nd|rd|th|d))Field\b", r"\1 Field", text, flags=re.I)
    text = re.sub(r"5th\.:Reconnaissance", "5th Reconnaissance", text, flags=re.I)
    text = re.sub(r"Troop'?", "Troop", text, flags=re.I)
    text = re.sub(r"He\s+adquarters", "Headquarters", text, flags=re.I)
    text = re.sub(r"\bC\s+ompany\b", "Company", text, flags=re.I)
    text = re.sub(r"ieldArtillery", "Field Artillery", text, flags=re.I)
    text = re.sub(r"(\d)(d|st|nd|rd|th)\.([A-Za-z])", r"\1\2 \3", text, flags=re.I)
    text = re.sub(r"(\d)(d|st|nd|rd|th)\.\s*", r"\1\2 ", text, flags=re.I)
    text = re.sub(r"(\d)(st|nd|rd|th)\s+\.\s*", r"\1\2 ", text, flags=re.I)
    text = re.sub(r"\s+-\s+", " ", text)
    text = re.sub(r"\.{2,}", ".", text)
    text = re.sub(r"\s+", " ", text).strip(" ,.")
    text = re.sub(r"^[\s.,:\-«•'\"]+", "", text)
    return text


def normalize_cc_names(text: str) -> str:
    text = clean_text(text)
    text = re.sub(r"\bCC\s*A\b", "CCA", text, flags=re.I)
    text = re.sub(r"\bCC\s*B\b", "CCB", text, flags=re.I)
    text = re.sub(r"\bCC\s*R\b", "CCR", text, flags=re.I)
    text = re.sub(r"\bCC\s*-\s*\(", "CCR (", text, flags=re.I)
    text = re.sub(r"\bAmd\b", "Armd", text, flags=re.I)
    text = re.sub(r"\bAnmd\b", "Armd", text, flags=re.I)
    text = re.sub(r"\bArmid\b", "Armd", text, flags=re.I)
    text = re.sub(r"\bBiv\b", "Div", text, flags=re.I)
    return text


def normalize_position(raw: str) -> str:
    key = clean_text(raw).lower().rstrip(".")
    key = re.sub(r"\(.*contd.*\)", "", key, flags=re.I).strip()
    if key in POSITION_ALIASES:
        return POSITION_ALIASES[key]
    if re.match(r"^co \d+", key):
        return f"CO {clean_text(raw)[3:].title()}"
    if re.match(r"^acofs g[-~ ]?\d", key):
        num = re.search(r"(\d)", key)
        role = {
            "1": "G-1 (Personnel)",
            "2": "G-2 (Intelligence)",
            "3": "G-3 (Operations)",
            "4": "G-4 (Logistics)",
            "5": "G-5 (Plans)",
            "6": "G-6 (Communications)",
            "7": "G-7 (Training)",
            "8": "G-8 (Resource Management)",
            "9": "G-9 (Civil Affairs)",
        }
        if num:
            return f"ACofS {role.get(num.group(1), f'G-{num.group(1)}')}"
    return clean_text(raw)


def normalize_division_header_ocr(line: str) -> str:
    """Normalize common OCR glitches in division title lines before matching."""
    text = clean_text(line)
    text = re.sub(r"3,2\s*th\b", "12th", text, flags=re.I)
    text = re.sub(r"\b2\.d\b", "2d", text, flags=re.I)
    text = re.sub(r"\bI02d\b", "102d", text, flags=re.I)
    text = re.sub(r"5th,'", "5th", text, flags=re.I)
    text = re.sub(r"Infankry|Infentry|Inf\s*antry[_\s]*", "Infantry ", text, flags=re.I)
    text = re.sub(r"Tpfantry", "Infantry", text, flags=re.I)
    text = re.sub(r"(\d{1,3}(?:st|nd|rd|th)?):", r"\1", text, flags=re.I)
    text = re.sub(r"\b3Qth\b", "30th", text, flags=re.I)
    text = re.sub(r"\b30ih\b", "30th", text, flags=re.I)
    text = re.sub(r"\b3\.6th\b", "36th", text, flags=re.I)
    text = re.sub(r"Jni'?antry", "Infantry", text, flags=re.I)
    text = re.sub(r"j>\s*iv\s*i?\s*s?\s*ion", "Division", text, flags=re.I)
    text = re.sub(r"Diy-islon", "Division", text, flags=re.I)
    text = re.sub(r"(\d{1,3}(?:st|nd|rd|th)?)\"", r"\1", text, flags=re.I)
    text = re.sub(r'"\s*Division\b', " Division", text, flags=re.I)
    text = re.sub(r"\b7Sth\b", "79th", text, flags=re.I)
    text = re.sub(r"\b79tn\b", "79th", text, flags=re.I)
    text = collapse_spaced_ocr_tokens(text)
    text = re.sub(r"Bilrision", "Division", text, flags=re.I)
    text = re.sub(
        r"aruiored|i,rmored|\^rmored|xjrmored|xirmored",
        "armored",
        text,
        flags=re.I,
    )
    text = re.sub(r"\bAmored\b", "Armored", text, flags=re.I)
    text = re.sub(r"infantry\s+Divis\s+ion", "Infantry Division", text, flags=re.I)
    text = re.sub(r"Divi\s+si\s+sn", "Division", text, flags=re.I)
    text = re.sub(r"Pi\s*vision", "Division", text, flags=re.I)
    text = re.sub(r"Pavilion\b", "Division", text, flags=re.I)
    text = re.sub(r"K3T7,CIftiENT8", "ATTACHMENTS", text, flags=re.I)
    text = re.sub(r"\.3d\b", "3d", text, flags=re.I)
    text = re.sub(r"(\d)u(?=[Aa]rmored)", r"\1d ", text)
    text = re.sub(r"(\d)\s*-\s*(?=[Aa]rmored)", r"\1d ", text)
    text = re.sub(r"[•·]", "", text)
    text = re.sub(r"Armored\.", "Armored", text, flags=re.I)
    text = re.sub(r"17tii", "17th", text, flags=re.I)
    text = re.sub(r"^[,.\s]+", "", text)
    text = re.sub(r"\s+-\s*$", "", text)
    text = re.sub(r"JE\.?\s*nfantry", "Infantry", text, flags=re.I)
    text = re.sub(r"\bJth\.Infantry\b", "5th Infantry", text, flags=re.I)
    text = re.sub(r"13th\s+iU\.?\s*rbQ\.?\s*rne", "13th Airborne", text, flags=re.I)
    text = re.sub(r"13th\s+\^?irbox[\'i]*i?v+", "13th Airborne", text, flags=re.I)
    text = re.sub(r"trbbrne\s*>?&?ivi&?i'?o+n", "Airborne Division", text, flags=re.I)
    text = re.sub(r"\b73th\b", "78th", text, flags=re.I)
    text = re.sub(r"\b7Sth\b", "78th", text, flags=re.I)
    text = re.sub(r"[:>?]+8th\b", "78th", text, flags=re.I)
    text = re.sub(r"\bfrt-h\b", "28th", text, flags=re.I)
    text = re.sub(r"Piv[ij]s[l1]ori", "Division", text, flags=re.I)
    text = re.sub(r"Bivi\s*sion", "Division", text, flags=re.I)
    text = re.sub(r"&6th~?", "86th", text, flags=re.I)
    text = re.sub(r"(\d{1,3}(?:st|nd|rd|th|d))~", r"\1", text, flags=re.I)
    text = re.sub(r"\br\^\s*division\b", "Division", text, flags=re.I)
    text = re.sub(r"Ihf\s+aiitry", "Infantry", text, flags=re.I)
    text = re.sub(r"I&vision", "Division", text, flags=re.I)
    text = re.sub(r"jrifant", "Infantry", text, flags=re.I)
    text = re.sub(r"[•?]+", "", text)
    text = re.sub(r"\.(?=\s*[a-zA-Z])", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def match_division(line: str) -> str | None:
    line = normalize_division_header_ocr(line)
    if len(line) > 70 or "table" in line.lower():
        return None
    if re.fullmatch(
        r"\d{1,3}(?:st|nd|rd|th|d)?\s*[-\s]*(?:infantry|armored|airborne)\s*",
        line,
        re.I,
    ):
        line = f"{line} Division"
    m = re.search(
        r"(\d{1,3})(?:st|nd|rd|th|d)?\s*[-\s]*(infantry|armored|airborne)\s*division",
        line,
        re.I,
    )
    if not m:
        return None
    number = int(m.group(1))
    kind = m.group(2).lower()
    for name in CANONICAL_DIVISIONS:
        if kind not in name.lower():
            continue
        if re.match(rf"^{number}\D", name):
            return name
    return None


DEFAULT_PDF_PAGE_DIVISION_OVERRIDES = (
    PROJECT_ROOT / "config" / "eto_oob_pdf_page_division_overrides.yaml"
)


def load_pdf_page_division_overrides(
    path: Path | None = None,
) -> dict[str, str]:
    import yaml  # noqa: PLC0415

    config_path = path or DEFAULT_PDF_PAGE_DIVISION_OVERRIDES
    if not config_path.exists():
        return {}
    data = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    return {str(key): value for key, value in (data.get("overrides") or {}).items()}


def load_division_page_ranges(
    path: Path | None = None,
) -> dict[str, tuple[int, int]]:
    import yaml  # noqa: PLC0415

    config_path = path or DEFAULT_PDF_PAGE_DIVISION_OVERRIDES
    if not config_path.exists():
        return {}
    data = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    ranges: dict[str, tuple[int, int]] = {}
    for division, spec in (data.get("division_page_ranges") or {}).items():
        if not isinstance(spec, dict):
            continue
        first = int(spec["first_pdf_page"])
        last = int(spec["last_pdf_page"])
        ranges[str(division)] = (first, last)
    return ranges


def expand_division_page_ranges(
    ranges: dict[str, tuple[int, int]],
) -> dict[str, str]:
    expanded: dict[str, str] = {}
    for division, (first, last) in ranges.items():
        for pdf_page in range(first, last + 1):
            expanded[str(pdf_page)] = division
    return expanded


def match_division_from_page_context(page_text: str) -> str | None:
    """Infer division from insignia/nickname pages when numeric headers OCR fails."""
    sample = page_text[:1200]
    if re.search(r"Lightning\s+Division", sample, re.I) and re.search(
        r"78th\s+Division", sample, re.I
    ):
        return "78th Infantry Division"
    if re.search(r"Keystone", sample, re.I):
        return "28th Infantry Division"
    if re.search(r"Black\s+Cat", sample, re.I) and re.search(
        r"AIRBORNE", sample, re.I
    ):
        return "13th Airborne Division"
    if re.search(r"Cross of Lorraine", sample, re.I) and re.search(
        r"79th", sample, re.I
    ):
        return "79th Infantry Division"
    if re.search(r"Thunderbolt\s+Division", sample, re.I):
        return "83d Infantry Division"
    if re.search(r"Blackhawk\s+Division", sample, re.I):
        return "86th Infantry Division"
    return None


def walk_pdf_page_divisions(
    doc: fitz.Document,
    *,
    overrides: dict[str, str] | None = None,
    start_page: int = 21,
    end_page: int = 570,
) -> dict[str, str]:
    """Map each PDF page to the active division using header OCR and carry-forward."""
    page_overrides = overrides if overrides is not None else load_pdf_page_division_overrides()
    range_pages = expand_division_page_ranges(load_division_page_ranges())
    current_division = ""
    page_divisions: dict[str, str] = {}

    for page_index in range(start_page - 1, min(end_page, doc.page_count)):
        fitz_page = doc[page_index]
        page_text = fitz_page.get_text("text")
        if "ORGANIC COMPOSITION" in page_text.upper() and "DIVISIONS" in page_text.upper():
            break

        pdf_page = str(page_index + 1)
        page_lines = build_page_lines(page_text)
        lines = [text for _, text in page_lines]

        if pdf_page in range_pages:
            page_division = range_pages[pdf_page]
            current_division = page_division
            page_divisions[pdf_page] = page_division
            continue

        page_division = current_division
        for header_line in lines[:8]:
            matched = match_division(header_line)
            if matched:
                page_division = matched
                current_division = matched
                break
        if not page_division:
            context_match = match_division_from_page_context(page_text)
            if context_match:
                page_division = context_match
                current_division = context_match
        if pdf_page in page_overrides:
            page_division = page_overrides[pdf_page]
            current_division = page_division

        if page_division:
            page_divisions[pdf_page] = page_division

    return page_divisions


def is_garbled_composition_line(line: str) -> bool:
    letters = re.sub(r"[^a-z0-9]", "", clean_text(line).lower())
    if not letters:
        return False
    norm = letters.replace("0", "o").replace("3", "s").replace("1", "i")
    if norm in {"composition", "compoit", "composit", "composi"}:
        return True
    return bool(re.fullmatch(r"compo[s5o0]?i?ti?", norm))


def detect_section(line: str) -> str | None:
    cleaned = clean_text(line)
    if re.search(
        r"c[o0]m.{0,6}st[\*a4]ff|st[\*a4]ff.{0,6}c[o0]m",
        cleaned,
        re.I,
    ):
        return "command_staff"
    if re.fullmatch(r"ST[\*A4]FF\.?", cleaned, re.I):
        return "command_staff"
    if re.search(r"to\s+higher\s+units", cleaned, re.I):
        return "higher_units"
    if (
        re.search(r"ass[i1l][cg][nm].{0,12}attach", cleaned, re.I)
        and "organic" not in cleaned.lower()
    ):
        return "higher_units"
    if (
        re.search(r"ass[i1l][cg][nm].{0,12}ati", cleaned, re.I)
        and "organic" not in cleaned.lower()
    ):
        return "higher_units"
    assign_key = re.sub(r"[^a-z]", "", cleaned.lower())
    if (
        "assign" in assign_key
        or "ssigm" in assign_key
        or "ssigmm" in assign_key
    ) and "attach" in assign_key:
        if "organic" not in cleaned.lower():
            return "higher_units"
    if re.search(r"assignment.*attachment", cleaned, re.I) and "organic" not in cleaned.lower():
        return "higher_units"
    if re.search(r"comm[ao]hd\s+posts?", cleaned, re.I):
        return "command_posts"
    if re.search(r"command\s*posts?", cleaned, re.I):
        return "command_posts"
    if re.search(r"posts?\b", cleaned, re.I) and re.search(
        r"comm|c\(w|cqmm|c\(w&i",
        cleaned,
        re.I,
    ):
        return "command_posts"
    if re.fullmatch(r"[\s'\",\.•\-^*]*[0O][\s'\",\.•\-^*]+N[\s'\",\.•\-^*]*", cleaned, re.I):
        return "organic_units"
    if re.fullmatch(
        r"[\s'\",\.•\-^*]*P\s+O\s+S\s+I\s+T\s+I\s+O\s+N[\s'\",\.•\-^*]*",
        cleaned,
        re.I,
    ):
        return "organic_units"
    if re.search(r"ohgahic\s+u", cleaned, re.I):
        return "organic_units"
    if re.search(r"qb&?ajmi['c]*\s+units", cleaned, re.I):
        return "organic_units"
    if re.search(r"orga\w*[\s\-]*c\s+\S*x?j[i1l]", cleaned, re.I):
        return "organic_units"
    if re.search(r"organic\s+u\^?i", cleaned, re.I):
        return "organic_units"
    if re.search(r"organic\s+touts?", cleaned, re.I):
        return "organic_units"
    if re.search(r"orgatic\s+units", cleaned, re.I):
        return "organic_units"
    if re.search(r"orgamtd\s*,?\s*units", cleaned, re.I):
        return "organic_units"
    if re.search(r"organic\s+i?m[iu][ts]{2}", cleaned, re.I):
        return "organic_units"
    if re.search(r"organic\s*\.?\s*pi?ts", cleaned, re.I):
        return "organic_units"
    if re.search(r"orga[nai][in]c", cleaned, re.I) and re.search(
        r"\bu[1n\^]?\w*t", cleaned, re.I
    ):
        return "organic_units"
    if re.search(r"detach\s+h[o0]ts?", cleaned, re.I):
        return "detachments"
    if re.search(r"attack|attac|ttach|ttac", cleaned, re.I) and "detach" not in cleaned.lower():
        if "assignment" not in cleaned.lower():
            return "attachments"
    if SECTION_PATTERNS["higher_units"].search(cleaned) and "organic" not in cleaned.lower():
        return "higher_units"
    for name, pattern in SECTION_PATTERNS.items():
        if name == "higher_units":
            continue
        if pattern.search(cleaned):
            return name
    return None


def page_is_command_staff(lines: list[str]) -> bool:
    position_count = sum(1 for line in lines if is_position_header(line))
    inline_count = sum(1 for line in lines if DATE_INLINE_RE.match(line))
    date_count = sum(1 for line in lines if DATE_RE.match(line))
    month_year_count = sum(
        1
        for line in lines
        if re.search(
            rf"\b(?:{MONTHS})\.?\s+\d{{2,4}}\b",
            line,
            re.I,
        )
    )
    dated_lines = inline_count + date_count + month_year_count
    if position_count >= 8 and month_year_count >= 2:
        return True
    return position_count >= 2 and dated_lines >= 2


def page_declares_section(page_lines: list[tuple[int, str]]) -> str | None:
    for _, line in page_lines:
        section = detect_section(line)
        if section:
            return section
    return None


COMMAND_STAFF_BLOCKED_SECTIONS = frozenset(
    {
        "attachments",
        "detachments",
        "higher_units",
        "command_posts",
        "statistics",
        "campaigns",
        "organic_units",
    }
)

ATTACHMENT_BLOCKED_SECTIONS = frozenset(
    {
        "detachments",
        "higher_units",
        "command_posts",
        "statistics",
        "campaigns",
        "organic_units",
        "command_staff",
    }
)


def should_parse_command_staff_page(
    page_lines: list[tuple[int, str]],
    *,
    page_section: str,
) -> bool:
    declared = page_declares_section(page_lines)
    if page_has_attachment_content(page_lines):
        return False
    if declared == "command_staff":
        return True
    if declared in COMMAND_STAFF_BLOCKED_SECTIONS:
        return False
    if page_section == "command_staff":
        return True
    if declared is None:
        lines = [text for _, text in page_lines]
        return page_is_command_staff(lines)
    return False


def is_position_header(line: str) -> bool:
    low = clean_text(line).lower().rstrip(".")
    if "command" in low and "staff" in low:
        return False
    if low in POSITION_ALIASES:
        return True
    return bool(
        re.match(
            r"^(co|gg|cofs|gofs|comdg|asst|arty|adj|acofs|i\.cof|chief of staff)",
            low,
        )
    )


def parse_month_year(date_text: str) -> tuple[int, int]:
    m = re.search(rf"(\d{{1,2}})\s+({MONTHS})\.?\s+(\d{{2,4}})", date_text, re.I)
    if not m:
        return (0, 0)
    month_map = {
        "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
        "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
        "lee": 12, "hay": 5, "tan": 1, "bee": 12, "liar": 3, "my": 5,
        "ug": 8, "pr": 4, "kar": 3, "jen": 1, "dig": 12, "m": 5, "iiar": 3,
    }
    month_key = m.group(2).lower()[:3]
    month = month_map.get(month_key, 0)
    year = int(m.group(3))
    if year < 100:
        year += 1900
    return (year, month)


def parse_command_staff_page(
    page_lines: list[tuple[int, str]],
    division: str,
    source_page: str = "",
    pdf_page: int = 0,
) -> list[dict]:
    rows: list[dict] = []
    positions: list[str] = []
    entries: list[tuple[str, str, str, bool, int]] = []
    current_position = ""
    pending_date = ""
    pending_name = ""

    for line_num, line in page_lines:
        if detect_section(line) == "command_staff":
            continue
        if is_position_header(line):
            current_position = normalize_position(line)
            if not entries:
                positions.append(current_position)
            continue
        inline = DATE_INLINE_RE.match(line)
        if inline:
            rank, name, acting = parse_rank_name(inline.group(2))
            entries.append((clean_text(inline.group(1)), rank, name, acting, line_num))
            if current_position and current_position not in positions:
                positions.append(current_position)
            continue
        if DATE_RE.match(line):
            pending_date = DATE_RE.match(line).group(1)
            continue
        if pending_date and current_position:
            rank, name, acting = parse_rank_name(line)
            if rank and name:
                entries.append((clean_text(pending_date), rank, name, acting, line_num))
                if current_position not in positions:
                    positions.append(current_position)
                pending_date = ""
                pending_name = ""
            elif rank and not name:
                pending_name = clean_text(f"{rank} {line}")
            elif not rank and pending_name:
                combined = clean_text(f"{pending_name} {line}")
                rank, name, acting = parse_rank_name(combined)
                entries.append(
                    (clean_text(pending_date), rank, name or combined, acting, line_num)
                )
                if current_position not in positions:
                    positions.append(current_position)
                pending_date = ""
                pending_name = ""
            elif not rank and re.match(r"^(Col|Lt|Maj|Brig|Capt)", line, re.I):
                pending_name = line
            elif pending_name:
                combined = clean_text(f"{pending_name} {line}")
                rank, name, acting = parse_rank_name(combined)
                entries.append(
                    (clean_text(pending_date), rank, name or combined, acting, line_num)
                )
                if current_position not in positions:
                    positions.append(current_position)
                pending_date = ""
                pending_name = ""

    if not entries:
        return rows

    if len(positions) > 1 and len(entries) > len(positions):
        groups: list[list[tuple[str, str, str, bool, int]]] = []
        current_group: list[tuple[str, str, str, bool, int]] = []
        last_ym = (0, 0)
        for entry in entries:
            ym = parse_month_year(entry[0])
            if current_group and ym < last_ym:
                groups.append(current_group)
                current_group = [entry]
            else:
                current_group.append(entry)
            last_ym = ym
        if current_group:
            groups.append(current_group)
        for position, group in zip(positions, groups):
            for date, rank, name, acting, line_num in group:
                rows.append(
                    _command_staff_row(
                        division,
                        position,
                        date,
                        rank,
                        name,
                        acting,
                        source_page,
                        pdf_page,
                        line_num,
                    )
                )
        return rows

    position = positions[0] if positions else current_position
    for date, rank, name, acting, line_num in entries:
        rows.append(
            _command_staff_row(
                division,
                position or current_position,
                date,
                rank,
                name,
                acting,
                source_page,
                pdf_page,
                line_num,
            )
        )
    return rows


def _command_staff_row(
    division: str,
    position: str,
    date: str,
    rank: str,
    name: str,
    acting: bool,
    source_page: str = "",
    pdf_page: int = 0,
    source_line: int = 0,
) -> dict:
    return with_page_fields(
        {
            "division": division,
            "position": position,
            "position_role": POSITION_ROLES.get(position, ""),
            "effective_date": date,
            "rank": rank,
            "name": name,
            "acting": str(acting).lower(),
            "promotion_or_replacement": "true",
            "scope_note": SCOPE_NOTE,
        },
        source_page,
        pdf_page,
        source_line,
    )


def parse_rank_name(line: str) -> tuple[str, str, bool]:
    line = clean_text(line)
    acting = bool(re.search(r"\((?:actg|Actg|xtctg|ictg|\^ctg)\)", line, re.I))
    line = re.sub(r"\([^)]*\)", "", line).strip()
    m = RANK_RE.match(line)
    if not m:
        return "", line, acting
    rank = m.group(1)
    name = clean_text(line[m.end() :])
    return rank, name, acting


def parse_higher_units_page(
    page_lines: list[tuple[int, str]],
    division: str,
    source_page: str = "",
    pdf_page: int = 0,
) -> list[dict]:
    dates: list[str] = []
    corps: list[str] = []
    armies: list[str] = []
    army_groups: list[str] = []
    date_lines: list[int] = []
    mode = ""

    for line_num, line in page_lines:
        low = line.lower()
        if detect_section(line) == "higher_units":
            continue
        if low == "corps":
            mode = "corps"
            continue
        if low == "army":
            mode = "army"
            continue
        if "army group" in low:
            mode = "army_group"
            continue
        if DATE_RE.match(line) or re.match(r"^\d{1,2}\s+\w{3,9}\s+\d{2,4}$", line):
            dates.append(clean_text(line))
            date_lines.append(line_num)
            continue
        if line in {"-", "—"}:
            if mode == "corps":
                corps.append("")
            elif mode == "army":
                armies.append("")
            elif mode == "army_group":
                army_groups.append("")
            continue
        if mode == "corps":
            corps.append(clean_text(line))
        elif mode == "army":
            armies.append(clean_text(line.title()))
        elif mode == "army_group":
            army_groups.append(clean_text(line))

    rows: list[dict] = []
    for idx, date in enumerate(dates):
        rows.append(
            with_page_fields(
                {
                    "division": division,
                    "date": date,
                    "corps": corps[idx] if idx < len(corps) else "",
                    "army": armies[idx] if idx < len(armies) else "",
                    "army_group_other": army_groups[idx] if idx < len(army_groups) else "",
                    "scope_note": SCOPE_NOTE,
                },
                source_page,
                pdf_page,
                date_lines[idx] if idx < len(date_lines) else 0,
            )
        )
    return rows


HIGHER_UNIT_DATE_X_MAX = 140
HIGHER_UNIT_CORPS_X_MAX = 210
HIGHER_UNIT_ARMY_X_MAX = 300


def _higher_unit_column_key(x: float) -> str | None:
    if x < HIGHER_UNIT_DATE_X_MAX:
        return "date"
    if x < HIGHER_UNIT_CORPS_X_MAX:
        return "corps"
    if x < HIGHER_UNIT_ARMY_X_MAX:
        return "army"
    if x >= HIGHER_UNIT_ARMY_X_MAX:
        return "army_group"
    return None


def _empty_higher_unit_band(y: float) -> dict[str, object]:
    return {"y": y, "date": [], "corps": [], "army": [], "army_group": []}


def _cluster_higher_unit_bands(
    spans: list[tuple[float, float, str]],
    y_tolerance: float = 8,
) -> list[dict[str, object]]:
    if not spans:
        return []
    bands: list[dict[str, object]] = []
    current = _empty_higher_unit_band(spans[0][0])
    for y, x, text in spans:
        if y - current["y"] > y_tolerance:  # type: ignore[operator]
            bands.append(current)
            current = _empty_higher_unit_band(y)
        column = _higher_unit_column_key(x)
        if column:
            current[column].append((x, text))  # type: ignore[index]
    bands.append(current)
    return bands


def _find_higher_units_table_y(spans: list[tuple[float, float, str]]) -> float | None:
    for y, _x, text in spans:
        if re.search(r"\bDATE\b", text, re.I) and re.search(
            r"\bCORPS\b|\bCOBPS\b", text, re.I
        ):
            return y
    return None


def normalize_assignment_date_ocr(text: str) -> str:
    """Normalize OCR noise in assignment/command-post date cells."""
    value = clean_text(text)
    value = re.sub(r"\bkk\b", "44", value, flags=re.I)
    value = re.sub(r"\bli-5\b|\bii-5\b", "45", value, flags=re.I)
    value = re.sub(r"\^5", "45", value)
    value = re.sub(r"\bif5'?\b", "45", value, flags=re.I)
    value = re.sub(r"^Z\s+", "2 ", value, flags=re.I)
    value = re.sub(r"2i\|\.\s*", "21 ", value)
    value = re.sub(r"6'\s+", "6 ", value)
    value = re.sub(r"Ik\s+", "14 ", value, flags=re.I)
    value = re.sub(r"19\^1-14-", "", value)
    return clean_text(value)


def complete_partial_command_post_date(date_str: str) -> str:
    text = normalize_assignment_date_ocr(date_str)
    text = re.sub(r"^['\"•\s]+", "", text)
    if re.search(r"\b\d{4}\b", text):
        return text
    match = re.match(r"(\d{1,2}\s+\w{3,9})", text, flags=re.I)
    if not match:
        return text
    base = match.group(1)
    month_token = base.split()[1][:3].lower()
    year = "1945" if month_token in {"jan", "feb", "mar", "apr", "may", "jun"} else "1944"
    return f"{base} {year}"


def parse_higher_units_page_spatial(
    fitz_page,
    division: str,
    source_page: str,
    pdf_page: int,
) -> list[dict]:
    spans = _collect_attachment_spans(fitz_page)
    if not spans:
        return []
    table_y = _find_higher_units_table_y(spans)
    y_min = (table_y or 0) + 20
    bands = _cluster_higher_unit_bands(spans)
    rows: list[dict] = []
    for band in bands:
        if band["y"] <= y_min:  # type: ignore[operator]
            continue
        date_raw = _join_attachment_column(band["date"])  # type: ignore[arg-type]
        corps_raw = _join_attachment_column(band["corps"])  # type: ignore[arg-type]
        army_raw = _join_attachment_column(band["army"])  # type: ignore[arg-type]
        ag_raw = _join_attachment_column(band["army_group"])  # type: ignore[arg-type]
        date = normalize_assignment_date_ocr(date_raw)
        if not date or not re.search(r"\d{1,2}\s+\w{3,9}\s+\d{2,4}", date):
            continue
        corps = clean_text(corps_raw).strip("- ")
        army = clean_text(army_raw).strip("' ")
        army_group = clean_text(ag_raw).strip("' ")
        rows.append(
            with_page_fields(
                {
                    "division": division,
                    "date": date,
                    "corps": corps,
                    "army": army,
                    "army_group_other": army_group,
                    "scope_note": SCOPE_NOTE,
                },
                source_page,
                pdf_page,
                int(band["y"]),
            )
        )
    return rows


COMMAND_POST_DATE_X_MAX = 130
COMMAND_POST_TOWN_X_MAX = 310
COMMAND_POST_REGION_X_MAX = 410


def _command_post_column_key(x: float) -> str | None:
    if x < COMMAND_POST_DATE_X_MAX:
        return "date"
    if x < COMMAND_POST_TOWN_X_MAX:
        return "town"
    if x < COMMAND_POST_REGION_X_MAX:
        return "region"
    if x >= COMMAND_POST_REGION_X_MAX:
        return "country"
    return None


def _empty_command_post_band(y: float) -> dict[str, object]:
    return {"y": y, "date": [], "town": [], "region": [], "country": []}


def _cluster_command_post_bands(
    spans: list[tuple[float, float, str]],
    y_tolerance: float = 8,
) -> list[dict[str, object]]:
    if not spans:
        return []
    bands: list[dict[str, object]] = []
    current = _empty_command_post_band(spans[0][0])
    for y, x, text in spans:
        if y - current["y"] > y_tolerance:  # type: ignore[operator]
            bands.append(current)
            current = _empty_command_post_band(y)
        column = _command_post_column_key(x)
        if column:
            current[column].append((x, text))  # type: ignore[index]
    bands.append(current)
    return bands


def _find_command_posts_table_y(spans: list[tuple[float, float, str]]) -> float | None:
    for y, _x, text in spans:
        if re.search(r"\bDATE\b", text, re.I) and re.search(
            r"\bTOWN\b|\bT\s*O\s*W\b", text, re.I
        ):
            return y
    return None


def parse_command_posts_page_spatial(
    fitz_page,
    division: str,
    source_page: str,
    pdf_page: int,
) -> list[dict]:
    spans = _collect_attachment_spans(fitz_page)
    if not spans:
        return []
    table_y = _find_command_posts_table_y(spans)
    y_min = (table_y or 0) + 20
    bands = _cluster_command_post_bands(spans)
    rows: list[dict] = []
    for band in bands:
        if band["y"] <= y_min:  # type: ignore[operator]
            continue
        date_raw = _join_attachment_column(band["date"])  # type: ignore[arg-type]
        town_raw = _join_attachment_column(band["town"])  # type: ignore[arg-type]
        region_raw = _join_attachment_column(band["region"])  # type: ignore[arg-type]
        country_raw = _join_attachment_column(band["country"])  # type: ignore[arg-type]
        date = complete_partial_command_post_date(date_raw)
        town = clean_text(town_raw)
        if not date or not re.search(r"\d{1,2}\s+\w{3,9}", date):
            continue
        if not town or is_junk_value(town):
            continue
        rows.append(
            with_page_fields(
                {
                    "division": division,
                    "date": date,
                    "town": town,
                    "region": clean_text(region_raw),
                    "country": clean_text(country_raw),
                    "scope_note": SCOPE_NOTE,
                },
                source_page,
                pdf_page,
                int(band["y"]),
            )
        )
    return rows


def attachment_category_key(text: str) -> str:
    key = re.sub(r"[^a-z0-9]+", "", text.lower())
    key = key.replace("lestroyer", "destroyer")
    return key


def normalize_attachment_category(line: str) -> str | None:
    line = clean_text(line)
    if match_division(line):
        return None
    low = re.sub(r"\(contd\)", "", line, flags=re.I).strip().lower().rstrip(".")
    if not low:
        return None
    low_key = attachment_category_key(low)
    # OCR column headers like "1 A m o r e d" -> "1amored"
    low_key_variants = [low_key, re.sub(r"^\d+", "", low_key)]
    for cat in sorted(ATTACHMENT_CATEGORIES, key=len, reverse=True):
        cat_key = attachment_category_key(cat)
        for key in low_key_variants:
            if key == cat_key or key.startswith(cat_key):
                return ATTACHMENT_CATEGORY_DISPLAY.get(
                    cat_key, clean_text(cat.title())
                )
    return None


def is_attachment_contd_header(line: str) -> bool:
    """True for OCR section headers like 'ATTACHMENTS (Contd)', not unit rows."""
    if re.search(r"field\s+art", line, re.I) and re.search(
        r"(?:\(contd|\(cantd|\(gontd|contd[\W]*\)?\s*$)", line, re.I
    ):
        return True
    if not re.search(r"(?:\(contd|\(cantd|\(gontd|contd[\W]*$)", line, re.I):
        return False
    text = clean_text(line)
    if re.search(
        r"\d+(?:st|d|rd|th)?\s+(?:CT|Infantry|Battalion|Bn|Regiment|Regt|"
        r"Field Artillery|Tank|Armored|Division)",
        text,
        re.I,
    ):
        return False
    return True


DIVISION_HEADER_UNIT_KW = re.compile(
    r"\b(?:"
    r"Battalion|Bn|Company|Co\b|Regiment|Regt|Platoon|Plat|Battery|Btry|"
    r"Squadron|Sq|Troop|Combat Team|CT|Tank Destroyer|Chemical|Mortar|"
    r"Engineer|Reconnaissance|Cavalry|Field Artillery|FA\b|Group|Gp|"
    r"Headquarters|Hq|Detachment|Team\b"
    r")\b",
    re.I,
)


def is_division_page_header_unit_name(unit_name: str, division: str = "") -> bool:
    """True when unit_name is a page division title (OCR), not an attached unit."""
    name = clean_text(unit_name)
    if not name or not division:
        return False
    if DIVISION_HEADER_UNIT_KW.search(name):
        return False
    if re.search(r"\bCorps\b", name, re.I):
        return False
    if re.search(r"\(\s*\d", name):
        return False
    match = re.match(
        r"^(\d+(?:st|nd|rd|th)?)\s+(Infantry|Armored|Airborne|Cavalry)\b",
        division,
        re.I,
    )
    if not match:
        return False
    prefix = f"{match.group(1)} {match.group(2)}"
    if not re.match(rf"^{re.escape(prefix)}\b", name, re.I):
        return False
    if len(name) > 55:
        return False
    tail = name[len(prefix) :].strip(" .,;:'\"-*•")
    if not tail:
        return True
    if re.match(
        r"^(?:Division|Divis|Di\s*vision|Pi\s*vision|Blvisi|D\s*i\s*v|Arty|Artillery)\b",
        tail,
        re.I,
    ):
        return True
    return bool(re.match(r"^[\W_\d%]+(?:on|vision|divis)\b", tail, re.I))


def is_attachment_junk_line(line: str) -> bool:
    if SKIP_LINE_RE.search(line):
        return True
    if re.match(r"^[\d\s\.oOzZqQ]+$", line):
        return True
    if MONTH_YEAR_ONLY_RE.match(line):
        return True
    if re.match(r"^\d{1,2}$", line):
        return True
    if len(line) <= 2:
        return True
    if re.match(r"^[\.\,\-\*•';:^_]+$", line):
        return True
    if re.match(r"^DSIACBMIKIS\s*$", line, re.I):
        return True
    if re.match(r"^_\s*\d+\s*_$", line):
        return True
    if re.match(r"^[_\s\d]+$", line) and len(line) < 15:
        return True
    if is_detachment_header_line(line):
        return True
    if re.search(r"attach", line, re.I) and "contd" in line.lower():
        return True
    if re.search(r"attach", line, re.I) and len(line) < 40:
        return True
    if re.match(r"^(?:[ALit]?TTACH|ATTAC)", line, re.I) and not re.search(
        r"\b(?:Battalion|Bn|Company|Cos?|Squadron|Regiment|Battery|Btry|"
        r"Platoon|CT|Infantry|Artillery|Engineer|Cavalry|Armored|Chemical|"
        r"Mortar|Tank|Group|Gp)\b",
        line,
        re.I,
    ):
        return True
    if is_attachment_contd_header(line):
        return True
    if re.search(r"^(?:To\s+Higher|Indicates\s+relieved|\(-\)\s*Indicates)", line, re.I):
        return True
    if re.search(r"ASSIG[A-Za-z0-9'£\s]*ATTAC[A-Za-z0-9'£\s]*", line, re.I) and not re.search(
        r"\b(?:Battalion|Bn|Company|Infantry|Regiment)\b", line, re.I
    ):
        return True
    if re.search(r"\bAsgd\b", line, re.I) and not re.search(
        r"\b(?:Battalion|Bn|Company|Infantry|Regiment)\b", line, re.I
    ):
        return True
    if re.search(r"\b(?:I{1,3}|IV|VI{1,2}|XXI)\s*Seventh\b", line, re.I) and not re.search(
        r"\b(?:Battalion|Bn|Company|Infantry|Regiment)\b", line, re.I
    ):
        return True
    if re.match(r"^t\s*\\?$", line):
        return True
    if re.search(r"division", line, re.I) and len(line) < 70:
        return True
    if re.search(r"infantry.*division|division.*infantry", line, re.I):
        return True
    collapsed = re.sub(r"\s+", "", line.lower())
    if "infantrydivision" in collapsed or collapsed.endswith("division"):
        return True
    return False


def is_attachment_unit_line(line: str) -> bool:
    if is_attachment_junk_line(line):
        return False
    if DATE_RE.match(line) or DATE_TO_LINE_RE.match(line):
        return False
    if re.search(rf"^\d{{1,2}}\s+(?:{MONTHS})\.?\s+\d{{2,4}}\s*$", line, re.I):
        return False
    if DATE_RANGE_RE.search(line) or DATE_INLINE_RE.match(line):
        return True
    probe = re.sub(r"\bEn\b", "Bn", line, flags=re.I)
    if re.search(
        r"\b(Bn|Co|Regt|Inf|Div|CCR|CCA|CCB|Gp|Sq|Plat|CT|Ren|Cav|Engr|FA|Mort|"
        r"TD|TB|Arty|Cml|Tk|Hq|Btry|Op)\b",
        probe,
        re.I,
    ):
        return True
    return bool(re.match(r"^\d", line) and re.search(r"[A-Za-z]{2}", line))


def dedupe_consecutive_units(
    units: list[tuple[int, str]],
) -> list[tuple[int, str]]:
    if not units:
        return []
    deduped = [units[0]]
    for line_num, unit in units[1:]:
        if normalize_cc_names(unit) != normalize_cc_names(deduped[-1][1]):
            deduped.append((line_num, unit))
    return deduped


def split_stacked_date_columns(
    dates: list[tuple[int, str]],
) -> tuple[list[tuple[int, str]], list[tuple[int, str]]]:
    """Split OCR column where date_from rows precede date_to rows."""
    if len(dates) < 2:
        return dates, []
    first_value = dates[0][1]
    split_at = 0
    for idx, (_, value) in enumerate(dates):
        if value != first_value:
            split_at = idx
            break
    else:
        return dates, []
    if split_at <= 0 or split_at >= len(dates):
        return dates, []
    return dates[:split_at], dates[split_at:]


def pair_attachment_dates(
    units: list[tuple[int, str]],
    from_dates: list[tuple[int, str]],
    to_dates: list[tuple[int, str]],
) -> list[tuple[str, str]]:
    n = len(units)
    if not n:
        return []

    if len(from_dates) == n and len(to_dates) == n:
        return [(from_dates[i][1], to_dates[i][1]) for i in range(n)]

    if not to_dates and len(from_dates) == 2 * n:
        return [
            (from_dates[i][1], from_dates[n + i][1]) for i in range(n)
        ]

    if not to_dates and n < len(from_dates) <= 2 * n:
        return [
            (
                from_dates[i][1],
                from_dates[n + i][1] if n + i < len(from_dates) else "",
            )
            for i in range(n)
        ]

    if not to_dates and len(from_dates) >= 2:
        stacked_from, stacked_to = split_stacked_date_columns(from_dates)
        if (
            stacked_to
            and len(stacked_from) == len(stacked_to)
            and len(stacked_from) <= n
        ):
            use_n = len(stacked_from)
            return [
                (stacked_from[i][1], stacked_to[i][1]) for i in range(use_n)
            ]

    if len(from_dates) == n and not to_dates:
        return [(from_dates[i][1], "") for i in range(n)]

    if len(from_dates) == 1 and to_dates and n > 1:
        date_from = from_dates[0][1]
        date_to = to_dates[0][1]
        return [(date_from, date_to)] * n

    if not from_dates and len(to_dates) == 1 and n > 1:
        date_to = to_dates[0][1]
        return [("", date_to)] * n

    paired: list[tuple[str, str]] = [("", "")] * n
    for i in range(min(n, len(from_dates))):
        paired[i] = (from_dates[i][1], paired[i][1])
    for i in range(min(n, len(to_dates))):
        paired[i] = (paired[i][0], to_dates[i][1])
    return paired


def extract_attachment_inline_row(
    line: str, line_num: int
) -> tuple[str, str, str] | None:
    line = normalize_date_ocr(line)
    range_match = DATE_RANGE_RE.search(line)
    if range_match:
        unit_part = normalize_cc_names(
            DATE_RANGE_RE.sub("", line).strip(" .-;,")
        )
        if unit_part and not is_attachment_junk_line(unit_part):
            return (
                unit_part,
                clean_text(range_match.group(1)),
                clean_text(range_match.group(2)),
            )
        return None
    inline = DATE_INLINE_RE.match(line)
    if inline:
        unit_part = normalize_cc_names(inline.group(2))
        if unit_part:
            return (unit_part, clean_text(inline.group(1)), "")
        return None
    trailing_range = list(DATE_RANGE_RE.finditer(line))
    if trailing_range:
        match = trailing_range[-1]
        unit_part = normalize_cc_names(line[: match.start()].strip(" .-;,"))
        if unit_part and not is_attachment_junk_line(unit_part):
            return (
                unit_part,
                clean_text(match.group(1)),
                clean_text(match.group(2)),
            )
    trailing_pair = TRAILING_DATE_PAIR_RE.search(line)
    if trailing_pair:
        unit_part = normalize_cc_names(
            line[: trailing_pair.start()].strip(" .-;,")
        )
        if unit_part and not is_attachment_junk_line(unit_part):
            return (
                unit_part,
                clean_text(trailing_pair.group(1)),
                clean_text(trailing_pair.group(2)),
            )
    trailing_date = re.search(
        rf"(\d{{1,2}}\s+(?:{MONTHS})\.?\s+\d{{2,4}})\s*$",
        line,
        re.I,
    )
    if trailing_date and is_attachment_unit_line(
        line[: trailing_date.start()].strip()
    ):
        unit_part = normalize_cc_names(
            line[: trailing_date.start()].strip(" .-;,")
        )
        if unit_part:
            return (unit_part, clean_text(trailing_date.group(1)), "")
    return None


def parse_attachment_segment(
    category: str,
    lines: list[tuple[int, str]],
    division: str,
    source_page: str,
    pdf_page: int,
) -> list[dict]:
    rows: list[dict] = []
    units: list[tuple[int, str]] = []
    from_dates: list[tuple[int, str]] = []
    to_dates: list[tuple[int, str]] = []
    parent_state = AttachmentParentState()

    for line_num, line in lines:
        inline_row = extract_attachment_inline_row(line, line_num)
        if inline_row:
            unit_name, date_from, date_to = inline_row
            parent_state, parent_unit_name = resolve_attachment_parent(
                unit_name, parent_state
            )
            rows.append(
                with_page_fields(
                    {
                        "division": division,
                        "category": category,
                        "unit_name": unit_name,
                        "parent_unit_name": parent_unit_name,
                        "date_from": date_from,
                        "date_to": date_to,
                        "scope_note": SCOPE_NOTE,
                    },
                    source_page,
                    pdf_page,
                    line_num,
                )
            )
            continue

        line = normalize_date_ocr(line)

        if DATE_RANGE_RE.search(line):
            continue

        to_match = DATE_TO_LINE_RE.match(line)
        if to_match:
            to_dates.append((line_num, clean_text(to_match.group(1))))
            continue

        date_text = parse_attachment_date_line(line)
        if date_text:
            from_dates.append((line_num, date_text))
            continue

        if is_attachment_unit_line(line):
            units.append((line_num, normalize_cc_names(line)))

    units = dedupe_consecutive_units(units)
    paired_dates = pair_attachment_dates(units, from_dates, to_dates)
    for idx, (line_num, unit) in enumerate(units):
        if idx >= len(paired_dates):
            break
        date_from, date_to = paired_dates[idx]
        parent_state, parent_unit_name = resolve_attachment_parent(unit, parent_state)
        rows.append(
            with_page_fields(
                {
                    "division": division,
                    "category": category,
                    "unit_name": unit,
                    "parent_unit_name": parent_unit_name,
                    "date_from": date_from,
                    "date_to": date_to,
                    "scope_note": SCOPE_NOTE,
                },
                source_page,
                pdf_page,
                line_num,
            )
        )
    return rows


def build_attachment_segment_rows(
    category: str,
    units: list[tuple[int, str]],
    from_dates: list[tuple[int, str]],
    to_dates: list[tuple[int, str]],
    division: str,
    source_page: str,
    pdf_page: int,
) -> list[dict]:
    units = dedupe_consecutive_units(units)
    paired_dates = pair_attachment_dates(units, from_dates, to_dates)
    rows: list[dict] = []
    parent_state = AttachmentParentState()
    for idx, (line_num, unit) in enumerate(units):
        if idx >= len(paired_dates):
            break
        date_from, date_to = paired_dates[idx]
        parent_state, parent_unit_name = resolve_attachment_parent(unit, parent_state)
        rows.append(
            with_page_fields(
                {
                    "division": division,
                    "category": category,
                    "unit_name": unit,
                    "parent_unit_name": parent_unit_name,
                    "date_from": date_from,
                    "date_to": date_to,
                    "scope_note": SCOPE_NOTE,
                },
                source_page,
                pdf_page,
                line_num,
            )
        )
    return rows


ATTACHMENT_UNIT_X_MAX = 280
ATTACHMENT_FROM_X_MIN = 280
ATTACHMENT_FROM_X_MAX = 399
ATTACHMENT_TO_X_MIN = 400
ATTACHMENT_ROW_Y_TOLERANCE = 10
ATTACHMENT_TOP_LEVEL_X_MAX = 96
COMBAT_COMMAND_UNIT_RE = re.compile(r"^CC[ABR]\b", re.I)
ATTACHED_TO_UNIT_X_MAX = 220
ATTACHED_TO_ORG_X_MIN = 220
ATTACHED_TO_DATE_X_MIN = 335
ATTACHED_TO_LAYOUT_RE = re.compile(
    r"\(\s*\.?\s*attached|attached\s*\(?\s*To\s*\)?",
    re.I,
)
DETACHMENT_SECTION_RE = re.compile(
    r"^[\.\s]*DETACHMENTS?[';]?\s*$|^DETACH\s+SOTS?\s*$",
    re.I,
)


def _detachment_section_marker(text: str) -> bool:
    """True for OCR section headers marking start of DETACHMENTS block."""
    collapsed = re.sub(r"[^A-Za-z\s]", "", clean_text(text)).strip()
    if re.match(r"^DETACHMENTS?$", collapsed, re.I):
        return True
    return bool(re.match(r"^DETACH\s+SOTS?$", collapsed, re.I))


def _attachment_column_key(x: float) -> str | None:
    if x < ATTACHMENT_UNIT_X_MAX:
        return "unit"
    if ATTACHMENT_FROM_X_MIN <= x <= ATTACHMENT_FROM_X_MAX:
        return "from"
    if x >= ATTACHMENT_TO_X_MIN:
        return "to"
    return None


def _attachment_column_key_attached_to(x: float) -> str | None:
    if x < ATTACHED_TO_UNIT_X_MAX:
        return "unit"
    if ATTACHED_TO_ORG_X_MIN <= x < ATTACHED_TO_DATE_X_MIN:
        return "attached_to"
    if x >= ATTACHED_TO_DATE_X_MIN:
        return "dates"
    return None


def page_has_attached_to_layout(fitz_page) -> bool:
    text = fitz_page.get_text()
    return bool(ATTACHED_TO_LAYOUT_RE.search(text))


def _join_attachment_column(parts: list[tuple[float, str]]) -> str:
    return clean_text("".join(text for _, text in sorted(parts)))


def parse_attachments_page_spatial(
    fitz_page,
    division: str,
    start_category: str,
    source_page: str,
    pdf_page: int,
    *,
    y_max: float | None = None,
) -> tuple[list[dict], str] | None:
    """Parse attachment rows using PDF column positions (unit / from / to)."""
    spans = _collect_attachment_spans(fitz_page)
    if not spans:
        return None
    bands = _cluster_attachment_row_bands(spans, ATTACHMENT_ROW_Y_TOLERANCE)
    if not bands:
        return None

    rows: list[dict] = []
    category = start_category
    pending_dates: dict[str, str] | None = None
    parent_state = AttachmentParentState()

    def emit_row(
        *,
        unit_name: str,
        date_from: str,
        date_to: str,
        source_line: int,
        unit_x_min: float | None = None,
    ) -> None:
        nonlocal category, parent_state
        if not unit_name or is_attachment_junk_line(unit_name):
            return
        new_category = normalize_attachment_category(unit_name)
        if new_category:
            category = new_category
            parent_state = AttachmentParentState()
            return
        if not category:
            return
        parent_state, parent_unit_name = resolve_attachment_parent(
            unit_name,
            parent_state,
            unit_x_min=unit_x_min,
        )
        rows.append(
            with_page_fields(
                {
                    "division": division,
                    "category": category,
                    "unit_name": unit_name,
                    "parent_unit_name": parent_unit_name,
                    "date_from": date_from,
                    "date_to": date_to,
                    "scope_note": SCOPE_NOTE,
                },
                source_page,
                pdf_page,
                source_line,
            )
        )

    for band in bands:
        if y_max is not None and band["y"] >= y_max:  # type: ignore[operator]
            continue
        unit_raw = _join_attachment_column(band["unit"])  # type: ignore[arg-type]
        from_raw = _join_attachment_column(band["from"])  # type: ignore[arg-type]
        to_raw = _join_attachment_column(band["to"])  # type: ignore[arg-type]
        source_line = int(band["y"])
        unit_x_min = _band_unit_x_min(band)

        if not unit_raw and (from_raw or to_raw):
            date_from, date_to = parse_attachment_date_columns(from_raw, to_raw)
            if date_from or date_to:
                pending_dates = {
                    "date_from": date_from,
                    "date_to": date_to,
                }
            continue

        if not unit_raw:
            continue

        unit_name = normalize_cc_names(unit_raw)
        date_from = ""
        date_to = ""
        if pending_dates:
            date_from = pending_dates.get("date_from", "")
            date_to = pending_dates.get("date_to", "")
            pending_dates = None
        band_from, band_to = parse_attachment_date_columns(from_raw, to_raw)
        if band_from:
            date_from = band_from
        if band_to:
            date_to = band_to
        for text in (
            clean_text(" ".join(part for part in (unit_raw, from_raw, to_raw) if part)),
            unit_raw,
        ):
            if date_from and date_to:
                break
            inline = extract_attachment_inline_row(text, source_line)
            if not inline:
                continue
            inline_unit, inline_from, inline_to = inline
            if inline_unit:
                unit_name = inline_unit
            if inline_from and not date_from:
                date_from = inline_from
            if inline_to and not date_to:
                date_to = inline_to

        emit_row(
            unit_name=unit_name,
            date_from=date_from,
            date_to=date_to,
            source_line=source_line,
            unit_x_min=unit_x_min,
        )

    if not rows:
        return None
    return rows, category


def parse_detachments_page_spatial(
    fitz_page,
    division: str,
    source_page: str,
    pdf_page: int,
    *,
    y_min: float,
) -> list[dict]:
    """Parse DETACHMENTS (Attached To) rows: unit | higher organization | dates."""
    spans = _collect_attachment_spans(fitz_page)
    if not spans:
        return []
    bands = _cluster_attachment_row_bands(
        spans,
        ATTACHMENT_ROW_Y_TOLERANCE,
        column_key=_attachment_column_key_attached_to,
    )
    rows: list[dict] = []
    pending_dates: dict[str, str] | None = None

    for band in bands:
        if band["y"] <= y_min:  # type: ignore[operator]
            continue
        unit_raw = _join_attachment_column(band["unit"])  # type: ignore[arg-type]
        attached_raw = _join_attachment_column(band["attached_to"])  # type: ignore[arg-type]
        dates_raw = _join_attachment_column(band["dates"])  # type: ignore[arg-type]
        source_line = int(band["y"])

        if not unit_raw and dates_raw:
            date_from, date_to = extract_attachment_date_pair(dates_raw)
            if date_from or date_to:
                pending_dates = {"date_from": date_from, "date_to": date_to}
            continue

        combined = clean_text(" ".join(part for part in (unit_raw, attached_raw) if part))
        if is_detachment_header_line(combined) or is_attachment_junk_line(combined):
            continue
        if normalize_attachment_category(combined):
            continue

        if not unit_raw and not attached_raw:
            continue

        unit_name = normalize_cc_names(unit_raw) if unit_raw else ""
        attached_to = normalize_cc_names(attached_raw) if attached_raw else ""
        date_from = ""
        date_to = ""
        if pending_dates:
            date_from = pending_dates.get("date_from", "")
            date_to = pending_dates.get("date_to", "")
            pending_dates = None
        if dates_raw:
            band_from, band_to = extract_attachment_date_pair(dates_raw)
            if band_from:
                date_from = band_from
            if band_to:
                date_to = band_to

        if not unit_name and attached_to:
            unit_name = attached_to
            attached_to = ""

        if not unit_name:
            continue

        rows.append(
            with_page_fields(
                {
                    "division": division,
                    "unit_name": unit_name,
                    "attached_to_organization": attached_to,
                    "date_from": date_from,
                    "date_to": date_to,
                    "scope_note": SCOPE_NOTE,
                },
                source_page,
                pdf_page,
                source_line,
            )
        )

    return rows


def parse_attachments_page_attached_to_spatial(
    fitz_page,
    division: str,
    start_category: str,
    source_page: str,
    pdf_page: int,
    *,
    y_min: float | None = None,
) -> tuple[list[dict], str] | None:
    """Parse (Attached To) layout: unit | higher HQ | date range columns."""
    spans = _collect_attachment_spans(fitz_page)
    if not spans:
        return None
    bands = _cluster_attachment_row_bands(
        spans,
        ATTACHMENT_ROW_Y_TOLERANCE,
        column_key=_attachment_column_key_attached_to,
    )
    if not bands:
        return None

    rows: list[dict] = []
    category = start_category
    pending_dates: dict[str, str] | None = None

    def emit_row(
        *,
        unit_name: str,
        date_from: str,
        date_to: str,
        source_line: int,
    ) -> None:
        nonlocal category
        if not unit_name or is_attachment_junk_line(unit_name):
            return
        new_category = normalize_attachment_category(unit_name)
        if new_category:
            category = new_category
            return
        if not category:
            return
        rows.append(
            with_page_fields(
                {
                    "division": division,
                    "category": category,
                    "unit_name": unit_name,
                    "parent_unit_name": "",
                    "date_from": date_from,
                    "date_to": date_to,
                    "scope_note": SCOPE_NOTE,
                },
                source_page,
                pdf_page,
                source_line,
            )
        )

    for band in bands:
        if y_min is not None and band["y"] <= y_min:  # type: ignore[operator]
            continue
        unit_raw = _join_attachment_column(band["unit"])  # type: ignore[arg-type]
        attached_raw = _join_attachment_column(band["attached_to"])  # type: ignore[arg-type]
        dates_raw = _join_attachment_column(band["dates"])  # type: ignore[arg-type]
        source_line = int(band["y"])

        combined_header = clean_text(" ".join(part for part in (unit_raw, attached_raw) if part))
        if is_detachment_header_line(combined_header) or _attached_to_section_marker(combined_header):
            continue

        if not unit_raw and dates_raw:
            date_from, date_to = extract_attachment_date_pair(dates_raw)
            if date_from or date_to:
                pending_dates = {"date_from": date_from, "date_to": date_to}
            continue

        if not unit_raw:
            continue

        unit_name = normalize_cc_names(unit_raw)
        date_from = ""
        date_to = ""
        if pending_dates:
            date_from = pending_dates.get("date_from", "")
            date_to = pending_dates.get("date_to", "")
            pending_dates = None
        for blob in (dates_raw, attached_raw, unit_raw):
            if not blob:
                continue
            band_from, band_to = extract_attachment_date_pair(blob)
            if band_from and not date_from:
                date_from = band_from
            if band_to and not date_to:
                date_to = band_to
        for text in (dates_raw, attached_raw, unit_raw):
            if date_from and date_to:
                break
            inline = extract_attachment_inline_row(text, source_line)
            if not inline:
                continue
            inline_unit, inline_from, inline_to = inline
            if inline_unit:
                unit_name = inline_unit
            if inline_from and not date_from:
                date_from = inline_from
            if inline_to and not date_to:
                date_to = inline_to

        emit_row(
            unit_name=unit_name,
            date_from=date_from,
            date_to=date_to,
            source_line=source_line,
        )

    if not rows:
        return None
    return rows, category


def parse_attachments_page(
    page_lines: list[tuple[int, str]],
    division: str,
    start_category: str,
    source_page: str,
    pdf_page: int,
    *,
    fitz_page=None,
) -> tuple[list[dict], list[dict], str]:
    detachment_rows: list[dict] = []
    if fitz_page is not None:
        spans = _collect_attachment_spans(fitz_page)
        y_detach = _find_detachment_section_y(spans)
        if y_detach is not None:
            detachment_rows = parse_detachments_page_spatial(
                fitz_page,
                division,
                source_page,
                pdf_page,
                y_min=y_detach,
            )
            spatial = parse_attachments_page_spatial(
                fitz_page,
                division,
                start_category,
                source_page,
                pdf_page,
                y_max=y_detach,
            )
            if spatial is not None:
                rows, category = spatial
                return rows, detachment_rows, category
            return [], detachment_rows, start_category
        y_attached_to = _find_attached_to_section_y(spans)
        if y_attached_to is not None:
            detachment_rows = parse_detachments_page_spatial(
                fitz_page,
                division,
                source_page,
                pdf_page,
                y_min=y_attached_to,
            )
            spatial = parse_attachments_page_spatial(
                fitz_page,
                division,
                start_category,
                source_page,
                pdf_page,
                y_max=y_attached_to,
            )
            if spatial is not None:
                rows, category = spatial
                return rows, detachment_rows, category
            if detachment_rows:
                return [], detachment_rows, start_category
        spatial = parse_attachments_page_spatial(
            fitz_page,
            division,
            start_category,
            source_page,
            pdf_page,
        )
        if spatial is not None:
            rows, category = spatial
            return rows, detachment_rows, category
    rows: list[dict] = []
    category = start_category
    segment_units: list[tuple[int, str]] = []
    pending_from: list[tuple[int, str]] = []
    page_to_dates: list[tuple[int, str]] = []
    parent_state = AttachmentParentState()

    segment_to_dates: list[tuple[int, str]] = []

    def flush_segment() -> None:
        nonlocal segment_units, pending_from, segment_to_dates, parent_state
        if category and segment_units:
            rows.extend(
                build_attachment_segment_rows(
                    category,
                    segment_units,
                    pending_from,
                    segment_to_dates,
                    division,
                    source_page,
                    pdf_page,
                )
            )
        segment_units = []
        pending_from = []
        segment_to_dates = []
        parent_state = AttachmentParentState()

    for line_num, line in page_lines:
        if match_division(line) and len(line) < 80:
            continue
        if detect_section(line) == "attachments":
            continue
        if detect_section(line) == "detachments" or is_detachment_header_line(line):
            break
        new_category = normalize_attachment_category(line)
        if new_category:
            flush_segment()
            category = new_category
            parent_state = AttachmentParentState()
            continue

        inline_row = extract_attachment_inline_row(line, line_num)
        if inline_row:
            unit_name, date_from, date_to = inline_row
            parent_state, parent_unit_name = resolve_attachment_parent(
                unit_name, parent_state
            )
            rows.append(
                with_page_fields(
                    {
                        "division": division,
                        "category": category,
                        "unit_name": unit_name,
                        "parent_unit_name": parent_unit_name,
                        "date_from": date_from,
                        "date_to": date_to,
                        "scope_note": SCOPE_NOTE,
                    },
                    source_page,
                    pdf_page,
                    line_num,
                )
            )
            continue

        date_text = parse_attachment_date_line(line)
        if date_text:
            pending_from.append((line_num, date_text))
            continue
        normalized = normalize_date_ocr(line)
        if DATE_TO_LINE_RE.match(normalized):
            to_value = clean_text(DATE_TO_LINE_RE.match(normalized).group(1))
            page_to_dates.append((line_num, to_value))
            segment_to_dates.append((line_num, to_value))
            continue
        if is_attachment_junk_line(line) or DATE_RANGE_RE.search(normalized):
            continue
        if is_attachment_unit_line(line):
            segment_units.append((line_num, normalize_cc_names(line)))

    flush_segment()

    if page_to_dates:
        row_idx = 0
        for row in rows:
            if row.get("date_to"):
                continue
            if row_idx < len(page_to_dates):
                row["date_to"] = page_to_dates[row_idx][1]
                row_idx += 1

    return rows, detachment_rows, category


def page_has_attachment_content(page_lines: list[tuple[int, str]]) -> bool:
    for _, line in page_lines:
        if normalize_attachment_category(line):
            return True
        if detect_section(line) == "attachments":
            return True
    return False


def should_parse_attachments_page(
    page_lines: list[tuple[int, str]],
    *,
    page_section: str,
    current_section: str,
) -> bool:
    declared = page_declares_section(page_lines)
    if declared in ATTACHMENT_BLOCKED_SECTIONS:
        return False
    if declared == "attachments":
        return True
    if page_section == "attachments" and declared is None and page_has_attachment_content(
        page_lines
    ):
        return True
    if (
        declared is None
        and page_has_attachment_content(page_lines)
        and (current_section == "attachments" or page_section == "attachments")
    ):
        return True
    if (
        declared is None
        and page_has_attachment_content(page_lines)
        and (current_section == "organic_units" or page_section == "organic_units")
    ):
        return True
    return False


def parse_command_posts_page(
    page_lines: list[tuple[int, str]],
    division: str,
    source_page: str = "",
    pdf_page: int = 0,
) -> list[dict]:
    rows: list[dict] = []
    pending_date = ""
    for line_num, line in page_lines:
        if detect_section(line) == "command_posts":
            continue
        inline = DATE_INLINE_RE.match(line)
        if inline:
            rows.append(
                with_page_fields(
                    {
                        "division": division,
                        "date": clean_text(inline.group(1)),
                        "town": clean_text(inline.group(2)),
                        "region": "",
                        "country": "",
                        "scope_note": SCOPE_NOTE,
                    },
                    source_page,
                    pdf_page,
                    line_num,
                )
            )
            continue
        if DATE_RE.match(line):
            pending_date = DATE_RE.match(line).group(1)
            continue
        if pending_date and len(line) < 80 and not SKIP_LINE_RE.search(line):
            rows.append(
                with_page_fields(
                    {
                        "division": division,
                        "date": pending_date,
                        "town": line,
                        "region": "",
                        "country": "",
                        "scope_note": SCOPE_NOTE,
                    },
                    source_page,
                    pdf_page,
                    line_num,
                )
            )
            pending_date = ""
    return rows


def is_junk_value(value: str) -> bool:
    value = clean_text(value)
    if not value or value in {".", ",", "-", "—", "<", ">", "*"}:
        return True
    return len(value) <= 1


def is_chronology_junk_unit_name(name: str) -> bool:
    name = clean_text(name)
    if not name:
        return True
    if re.search(
        r"^(?:#|@|/|French\s+attachments|First\s+Elements|Arrived\s+STO|"
        r"Disbanded|Reorganized|Activated\b)",
        name,
        re.I,
    ):
        return True
    if re.search(
        r"^(?:Comdg\s*Gen|CofS|ACofS|Asst\s+Division|Arty\s*Comdr|Adj\s*Gen|"
        r"CO\s+\d|AcofS|JXofS|Adj\s*Qr|CO\s+Sloth|i\s*rty\s*Comdr|Comdg\s*Gea|"
        r"CQfS|inf\s+\d|GpfS|xXofS|CC\s*,1\s*Comdr|CG\s*R|Arty\s*\"?comdr|"
        r"ACo'?f\s*S|XofS|CofS~|/'\s*AC|CCR\s*Comdr)",
        name,
        re.I,
    ):
        return True
    if re.match(rf"^\d{{1,2}}\s+(?:{MONTHS})", name, re.I) and not re.search(
        r"\b(Bn|Battalion|Company|Regiment|Infantry|Division|CCR|CCA|CCB|"
        r"Gp|Group|Squadron|Platoon|CT|Cav|Cavalry|Engineer|Artillery|Cml|"
        r"Mort|Tank|Field|Armored|Battery|Btry|Reconnaissance|Antiaircraft|"
        r"Automatic|Weapons|Engineers?|Battallions?|Inf|FA|TD|TB|Arty|Ren|Hq|"
        r"Plat|Cos|Batteries|Tr|Propelled|Mobile)\b",
        name,
        re.I,
    ):
        return True
    if (
        re.match(rf"^\d{{1,2}}\s+(?:{MONTHS})\b", name, re.I)
        and len(name) < 25
        and not re.search(
            r"\b(Bn|Battalion|Company|Regiment|Infantry|Division|CCR|CCA|CCB|"
            r"Gp|Group|Squadron|CT|Cav|Cavalry|Engineer|Artillery|Tank|Armored|"
            r"Battery|Btry|Reconnaissance|Antiaircraft)\b",
            name,
            re.I,
        )
    ):
        return True
    if re.match(r"^[\d\s/:'\"•\*\.;,=-]+$", name):
        return True
    if re.search(r"\bffnfant\b", name, re.I):
        return True
    if re.match(r"^[a-z]*fant\s+rv_?$", name, re.I):
        return True
    return False


def filter_rows(rows: list[dict], required_field: str) -> list[dict]:
    filtered: list[dict] = []
    for row in rows:
        value = clean_text(row.get(required_field, ""))
        if is_junk_value(value):
            continue
        if required_field == "unit_name" and is_chronology_junk_unit_name(value):
            continue
        filtered.append(row)
    return filtered


def write_csv(path: Path, fieldnames: list[str], rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


class ETOOrderOfBattleExtractor:
    def __init__(self, pdf_path: Path) -> None:
        self.pdf_path = pdf_path
        self.command_staff: list[dict] = []
        self.statistics: list[dict] = []
        self.campaigns: list[dict] = []
        self.organic_units: list[dict] = []
        self.attachments: list[dict] = []
        self.detachments: list[dict] = []
        self.higher_units: list[dict] = []
        self.command_posts: list[dict] = []
        self.current_source_page = ""
        self.current_pdf_page = 0
        self.current_source_line = 0
        self._pending_organic_ordinal = ""

    def _row(self, data: dict, source_line: int | None = None) -> dict:
        return with_page_fields(
            data,
            self.current_source_page,
            self.current_pdf_page,
            source_line if source_line is not None else self.current_source_line,
        )

    def extract(self, start_page: int = 21, end_page: int = 570) -> None:
        doc = fitz.open(self.pdf_path)
        current_division = ""
        current_section = ""
        current_category = ""
        pending_position = ""
        pending_date = ""
        pending_metric = ""
        detachment_attached_to = ""
        higher_dates: list[str] = []
        higher_corps: list[str] = []
        higher_armies: list[str] = []
        higher_army_groups: list[str] = []
        cp_dates: list[str] = []
        cp_towns: list[str] = []
        cp_regions: list[str] = []
        cp_countries: list[str] = []
        attach_units: list[str] = []
        attach_from: list[str] = []
        attach_to: list[str] = []
        attach_unit_lines: list[int] = []
        higher_date_lines: list[int] = []
        cp_town_lines: list[int] = []
        stat_key = ""

        for page_index in range(start_page - 1, end_page):
            fitz_page = doc[page_index]
            page_text = fitz_page.get_text()
            self.current_pdf_page = page_index + 1
            self.current_source_page = extract_source_page(page_text)
            if "ORGANIC COMPOSITION" in page_text.upper() and "DIVISIONS" in page_text.upper():
                break
            page_lines = build_page_lines(page_text)
            lines = [text for _, text in page_lines]

            range_pages = expand_division_page_ranges(load_division_page_ranges())
            page_overrides = load_pdf_page_division_overrides()
            pdf_page_key = str(self.current_pdf_page)

            if pdf_page_key in range_pages:
                page_division = range_pages[pdf_page_key]
                current_division = page_division
            else:
                page_division = current_division
                for header_line in lines[:8]:
                    matched = match_division(header_line)
                    if matched:
                        page_division = matched
                        current_division = matched
                        break
                if not page_division:
                    context_match = match_division_from_page_context(page_text)
                    if context_match:
                        page_division = context_match
                        current_division = context_match
                if pdf_page_key in page_overrides:
                    page_division = page_overrides[pdf_page_key]
                    current_division = page_division

            page_section = current_section
            for header_line in lines[:35]:
                section = detect_section(header_line)
                if section:
                    page_section = section
                    current_section = section
                    break

            if page_division and should_parse_command_staff_page(
                page_lines, page_section=page_section
            ):
                self.command_staff.extend(
                    parse_command_staff_page(
                        page_lines,
                        page_division,
                        self.current_source_page,
                        self.current_pdf_page,
                    )
                )
                if page_section == "command_staff" or page_declares_section(
                    page_lines
                ) == "command_staff":
                    continue

            if page_division and page_section == "higher_units":
                spatial_higher = (
                    parse_higher_units_page_spatial(
                        fitz_page,
                        page_division,
                        self.current_source_page,
                        self.current_pdf_page,
                    )
                    if fitz_page is not None
                    else []
                )
                if spatial_higher:
                    self.higher_units.extend(spatial_higher)
                else:
                    self.higher_units.extend(
                        parse_higher_units_page(
                            page_lines,
                            page_division,
                            self.current_source_page,
                            self.current_pdf_page,
                        )
                    )
                continue

            if page_division and page_section == "command_posts":
                spatial_posts = (
                    parse_command_posts_page_spatial(
                        fitz_page,
                        page_division,
                        self.current_source_page,
                        self.current_pdf_page,
                    )
                    if fitz_page is not None
                    else []
                )
                if spatial_posts:
                    self.command_posts.extend(spatial_posts)
                else:
                    self.command_posts.extend(
                        parse_command_posts_page(
                            page_lines,
                            page_division,
                            self.current_source_page,
                            self.current_pdf_page,
                        )
                    )
                continue

            if page_division and should_parse_attachments_page(
                page_lines,
                page_section=page_section,
                current_section=current_section,
            ):
                if current_section == "organic_units" or page_section == "organic_units":
                    current_section = "attachments"
                    page_section = "attachments"
                page_rows, page_detachment_rows, current_category = parse_attachments_page(
                    page_lines,
                    page_division,
                    current_category,
                    self.current_source_page,
                    self.current_pdf_page,
                    fitz_page=fitz_page,
                )
                self.attachments.extend(page_rows)
                self.detachments.extend(page_detachment_rows)
                continue

            for line_num, line in page_lines:
                self.current_source_line = line_num
                if SKIP_LINE_RE.search(line):
                    continue

                division = match_division(line)
                if division and len(line) < 80:
                    current_division = division
                    continue

                section = detect_section(line)
                if section:
                    current_section = section
                    if section == "organic_units":
                        self._pending_organic_ordinal = ""
                        self._capture_pre_organic_infantry(
                            page_lines, line_num, current_division
                        )
                    if section == "attachments":
                        current_category = ""
                        attach_units, attach_from, attach_to = [], [], []
                        attach_unit_lines = []
                    if section == "detachments":
                        detachment_attached_to = ""
                    if section == "higher_units":
                        higher_dates, higher_corps = [], []
                        higher_armies, higher_army_groups = [], []
                        higher_date_lines = []
                    if section == "command_posts":
                        cp_dates, cp_towns, cp_regions, cp_countries = [], [], [], []
                        cp_town_lines = []
                    continue

                if not current_division or not current_section:
                    continue

                if current_section == "command_staff":
                    continue

                elif current_section == "statistics":
                    stat_key, pending_metric = self._parse_statistics_line(
                        line, current_division, stat_key, pending_metric
                    )

                elif current_section == "organic_units":
                    self._parse_organic_unit_line(line, current_division)

                elif current_section == "attachments":
                    continue

                elif current_section == "detachments":
                    detachment_attached_to = self._parse_detachment_line(
                        line,
                        current_division,
                        detachment_attached_to,
                    )

                elif current_section == "higher_units":
                    (
                        higher_dates,
                        higher_corps,
                        higher_armies,
                        higher_army_groups,
                        higher_date_lines,
                    ) = self._parse_higher_unit_line(
                        line,
                        current_division,
                        higher_dates,
                        higher_corps,
                        higher_armies,
                        higher_army_groups,
                        higher_date_lines,
                    )

                elif current_section == "command_posts":
                    (
                        cp_dates,
                        cp_towns,
                        cp_regions,
                        cp_countries,
                        cp_town_lines,
                    ) = self._parse_command_post_line(
                        line,
                        current_division,
                        cp_dates,
                        cp_towns,
                        cp_regions,
                        cp_countries,
                        cp_town_lines,
                    )

        self._flush_attachment_buffers(
            current_division,
            current_category,
            attach_units,
            attach_from,
            attach_to,
            attach_unit_lines,
        )
        self._flush_command_post_buffers(
            current_division,
            cp_dates,
            cp_towns,
            cp_regions,
            cp_countries,
            cp_town_lines,
        )
        self._flush_higher_units(
            current_division,
            higher_dates,
            higher_corps,
            higher_armies,
            higher_army_groups,
            higher_date_lines,
        )

    def _parse_command_staff_line(
        self,
        line: str,
        division: str,
        pending_position: str,
        pending_date: str,
    ) -> tuple[str, str]:
        low = line.lower()
        if "command" in low and "staff" in low:
            return pending_position, pending_date
        pos_key = low.rstrip(".")
        if pos_key in POSITION_ALIASES or re.match(
            r"^(co|gg|cofs|gofs|comdg|asst|arty|adj|acofs|i\.cof)", pos_key
        ):
            return normalize_position(line), ""

        inline = DATE_INLINE_RE.match(line)
        if inline and pending_position:
            date, rest = inline.group(1), inline.group(2)
            rank, name, acting = parse_rank_name(rest)
            self.command_staff.append(
                self._row(
                    {
                        "division": division,
                        "position": pending_position,
                        "effective_date": clean_text(date),
                        "rank": rank,
                        "name": name,
                        "acting": str(acting).lower(),
                        "scope_note": SCOPE_NOTE,
                    }
                )
            )
            return pending_position, ""
        if DATE_RE.match(line):
            return pending_position, DATE_RE.match(line).group(1)
        if pending_position and pending_date and not DATE_RE.match(line):
            rank, name, acting = parse_rank_name(line)
            if name:
                self.command_staff.append(
                    self._row(
                        {
                            "division": division,
                            "position": pending_position,
                            "effective_date": clean_text(pending_date),
                            "rank": rank,
                            "name": name,
                            "acting": str(acting).lower(),
                            "scope_note": SCOPE_NOTE,
                        }
                    )
                )
                return pending_position, ""
        return pending_position, pending_date

    def _parse_statistics_line(
        self, line: str, division: str, stat_key: str, pending_metric: str
    ) -> tuple[str, str]:
        low = line.lower()
        value_match = re.match(r"^[\d,'\.\s]+%?$", line)
        label_match = not value_match and not DATE_RE.match(line)
        if low in {"campaigns", "chronology", "individual awards"}:
            return low, ""
        if low.startswith("activated"):
            stat_key = "chronology"
            return stat_key, "Activated"
        if "arrived eto" in low:
            stat_key = "chronology"
            return stat_key, "Arrived ETO"
        if "arrived continent" in low:
            stat_key = "chronology"
            return stat_key, "Arrived Continent"
        if "entered combat" in low:
            stat_key = "chronology"
            return stat_key, "Entered Combat"
        if "days in combat" in low:
            stat_key = "chronology"
            return stat_key, "Days in Combat"
        if low.startswith("casualties"):
            return "casualties", ""
        if stat_key == "casualties" and label_match:
            return "casualties", clean_text(re.sub(r"[\.:]+$", "", line))
        if stat_key == "casualties" and value_match and pending_metric:
            self.statistics.append(
                self._row(
                    {
                        "division": division,
                        "category": "casualties",
                        "metric": pending_metric,
                        "value": clean_text(line),
                        "scope_note": SCOPE_NOTE,
                    }
                )
            )
            return "casualties", ""
        if stat_key == "individual awards" and label_match:
            return "awards", clean_text(re.sub(r"[\.:]+$", "", line))
        if stat_key == "awards" and value_match and pending_metric:
            self.statistics.append(
                self._row(
                    {
                        "division": division,
                        "category": "awards",
                        "metric": pending_metric,
                        "value": clean_text(line),
                        "scope_note": SCOPE_NOTE,
                    }
                )
            )
            return "awards", ""
        campaign_names = {
            "normandy",
            "northern france",
            "ardennes",
            "rhineland",
            "central europe",
            "southern france",
        }
        if low.rstrip(".") in campaign_names or "campaign" in low:
            self.campaigns.append(
                self._row(
                    {
                        "division": division,
                        "campaign": clean_text(line.rstrip(".")),
                        "scope_note": SCOPE_NOTE,
                    }
                )
            )
            return stat_key, stat_key

        if stat_key == "chronology" and label_match and not pending_metric:
            return stat_key, clean_text(line)
        if pending_metric and value_match:
            category = (
                "chronology"
                if stat_key == "chronology"
                else "casualties_or_awards"
            )
            self.statistics.append(
                self._row(
                    {
                        "division": division,
                        "category": category,
                        "metric": pending_metric,
                        "value": clean_text(line),
                        "scope_note": SCOPE_NOTE,
                    }
                )
            )
            return stat_key, ""

        known_metrics = {
            "killed",
            "wounded",
            "missing",
            "captured",
            "battle casualties",
            "non-battle casualties",
            "non-battle,casualties",
            "total casualties",
            "percent of t/o strength",
            "pws taken",
            "pw taken",
            "dsc",
            "dsm",
            "legion of merit",
            "silver star",
            "soldiers medal",
            "soldiers eledal",
            "bronze star",
            "air medal",
        }
        if label_match and low.rstrip(".:") in known_metrics:
            return stat_key, clean_text(re.sub(r"[\.:]+$", "", line))
        return stat_key, pending_metric

    def _capture_pre_organic_infantry(
        self,
        page_lines: list[tuple[int, str]],
        before_line: int,
        division: str,
    ) -> None:
        """Capture infantry regiments listed above a garbled ORGANIC UNITS header."""
        if not division:
            return
        for line_num, line in page_lines:
            if line_num >= before_line:
                break
            if line_num < before_line - 25:
                continue
            normalized = normalize_organic_unit_name(line)
            if re.search(
                r"\d{2,3}(?:st|nd|rd|th|d)\s+Infantry\b",
                normalized,
                re.I,
            ):
                self.current_source_line = line_num
                self._parse_organic_unit_line(line, division)

    def _parse_organic_unit_line(self, line: str, division: str) -> None:
        if detect_section(line) in {"attachments", "detachments", "higher_units", "command_posts"}:
            return
        if SECTION_PATTERNS["attachments"].search(line):
            return
        if re.search(r"attack|attac|ttach|detach|assignment|command posts", line, re.I):
            return
        cleaned = clean_text(line)
        if re.fullmatch(r"[\d,\.\s]+", cleaned):
            return
        if re.fullmatch(r"[\s;,.:'\"•\-^*]+", cleaned):
            return
        if re.sub(r"\s+", "", cleaned).lower() == "composition":
            return
        if re.search(r"organic\s+touts?", cleaned, re.I):
            return
        if re.search(r"orgatic\s+units", cleaned, re.I):
            return
        if re.search(r"orgamtd\s*,?\s*units", cleaned, re.I):
            return
        if re.search(r"organic\s+i?m[iu][ts]{2}", cleaned, re.I):
            return
        if re.search(r"organic\s*\.?\s*pi?ts", cleaned, re.I):
            return
        if re.search(
            r"Ordnance\b.*Military\s*\"?Police|Military\s+Police\s+Platoon",
            cleaned,
            re.I,
        ) and re.search(r"Ordnance\b", cleaned, re.I):
            ordnance = re.split(
                r'(?=Military\s*"?Police)', cleaned, maxsplit=1, flags=re.I
            )[0]
            mp = re.search(r"Military\s*\"?Police\s+Platoon", cleaned, re.I)
            self._parse_organic_unit_line(ordnance, division)
            if mp:
                self._parse_organic_unit_line(mp.group(0), division)
            return
        if is_garbled_composition_line(line):
            return
        if re.search(r"^COM Z\b", cleaned, re.I):
            return
        if re.search(
            r"arrived Iceland|First Elements.*arrived|^\*?\s*Hq\.\s+arrived|"
            r"Entire Division entered Combat|"
            r"^#.*D Day|^\*.*Previous Combat|"
            r"^\*.*Three Inf Regts|Entered Combat as Task Force|"
            r"^#.*(?:Bay|D Pay)\s*Southern|^\*.*previous Combat in|"
            r"Three Inf Regts|D Day Southern France|improvised.*Hq|"
            r"Task\s*-?\s*force\s+Herron|Comdr arrived|Asst Div\s*$",
            cleaned,
            re.I,
        ):
            if self.organic_units:
                self.organic_units[-1]["notes"] = clean_text(
                    f"{self.organic_units[-1].get('notes', '')} {cleaned}".strip()
                )
            return
        if re.fullmatch(r"t[-\s]*\d+.*", cleaned, re.I):
            return
        if re.fullmatch(r"[,.\s]*\d{1,3}\s*r\.?", cleaned, re.I):
            return
        if re.fullmatch(r"[~«_.\s\d]+", cleaned):
            return
        if re.fullmatch(r"[\s'\"OTQ\.]+", cleaned, re.I):
            return
        if re.fullmatch(r"[a-z]{1,3}", cleaned, re.I):
            return
        if re.fullmatch(r"[\s;•\-^*'\"v/]+", cleaned):
            return
        if re.fullmatch(r"~?\s*-?\s*\d+\s*-?", cleaned):
            return
        if re.fullmatch(r"\d+(?:st|nd|rd|th|d)\.?", cleaned, re.I):
            self._pending_organic_ordinal = cleaned.rstrip(".")
            return
        if self._pending_organic_ordinal and re.fullmatch(r"infantry\.?", cleaned, re.I):
            line = f"{self._pending_organic_ordinal} Infantry"
            self._pending_organic_ordinal = ""
        elif self._pending_organic_ordinal and re.search(
            r"field\s+artillery",
            collapse_spaced_ocr_tokens(cleaned),
            re.I,
        ):
            line = f"{self._pending_organic_ordinal} {collapse_spaced_ocr_tokens(cleaned)}"
            self._pending_organic_ordinal = ""
        elif self._pending_organic_ordinal and re.search(
            r"engineer\s+combat",
            collapse_spaced_ocr_tokens(cleaned),
            re.I,
        ):
            line = f"{self._pending_organic_ordinal} {collapse_spaced_ocr_tokens(cleaned)}"
            self._pending_organic_ordinal = ""
        dash_unit = re.match(
            r"^[•\*\-]+\s*(\d+(?:st|nd|rd|th)\b.+)", line, re.I
        )
        if dash_unit:
            line = dash_unit.group(1)
        if re.match(r"^-\s*Band\.?$", line, re.I):
            line = "Band"
        if re.search(r"^[«_]\s*\d+", cleaned):
            return
        if line.startswith("*") or line.startswith("#") or (
            line.startswith("-") and not re.match(r"^-\s*\d+(?:st|nd|rd|th)\b", line, re.I)
        ):
            note = clean_text(line.lstrip("*-# "))
            if re.search(r"\b\d{1,3}\s*r\.?\s*$", note, re.I) and len(note) < 20:
                return
            if re.fullmatch(r"\d{1,3}\s*[-»_.\s]+", note):
                return
            if re.fullmatch(r"[\d\$'\-\s\.,r]+", note) and len(note) < 15:
                return
            if self.organic_units:
                self.organic_units[-1]["notes"] = clean_text(
                    f"{self.organic_units[-1].get('notes', '')} {note}".strip()
                )
            return
        unit_name = normalize_organic_unit_name(line)
        if not unit_name or len(unit_name) < 3:
            return
        if re.fullmatch(r"[-.*•\s]+", unit_name):
            return
        self.organic_units.append(
            self._row(
                {
                    "division": division,
                    "unit_name": unit_name,
                    "notes": "",
                    "scope_note": SCOPE_NOTE,
                }
            )
        )

    def _flush_attachment_buffers(
        self,
        division: str,
        category: str,
        units: list[str],
        date_from: list[str],
        date_to: list[str],
        unit_lines: list[int],
    ) -> None:
        if not division or not units:
            return
        for idx, unit in enumerate(units):
            self.attachments.append(
                self._row(
                    {
                        "division": division,
                        "category": category,
                        "unit_name": normalize_cc_names(unit),
                        "date_from": date_from[idx] if idx < len(date_from) else "",
                        "date_to": date_to[idx] if idx < len(date_to) else "",
                        "scope_note": SCOPE_NOTE,
                    },
                    source_line=unit_lines[idx] if idx < len(unit_lines) else None,
                )
            )

    def _parse_attachment_line(
        self,
        line: str,
        division: str,
        category: str,
        units: list[str],
        date_from: list[str],
        date_to: list[str],
        unit_lines: list[int],
    ) -> tuple[str, list[str], list[str], list[str], list[int]]:
        low = line.lower().rstrip(".")
        if low.replace(" ", "") in {
            c.replace(" ", "") for c in ATTACHMENT_CATEGORIES
        } or low in ATTACHMENT_CATEGORIES:
            self._flush_attachment_buffers(
                division, category, units, date_from, date_to, unit_lines
            )
            return clean_text(line.title()), [], [], [], []

        range_match = DATE_RANGE_RE.search(line)
        if range_match:
            unit_part = normalize_cc_names(DATE_RANGE_RE.sub("", line).strip(" .-"))
            if unit_part:
                self.attachments.append(
                    self._row(
                        {
                            "division": division,
                            "category": category,
                            "unit_name": unit_part,
                            "date_from": clean_text(range_match.group(1)),
                            "date_to": clean_text(range_match.group(2)),
                            "scope_note": SCOPE_NOTE,
                        }
                    )
                )
            return category, units, date_from, date_to, unit_lines

        if DATE_RE.match(line):
            date_from.append(clean_text(DATE_RE.match(line).group(1)))
            if len(date_from) > len(date_to):
                date_to.append("")
            return category, units, date_from, date_to, unit_lines

        if DATE_INLINE_RE.match(line):
            inline = DATE_INLINE_RE.match(line)
            unit_part = normalize_cc_names(inline.group(2))
            units.append(unit_part)
            unit_lines.append(self.current_source_line)
            date_from.append(clean_text(inline.group(1)))
            date_to.append("")
            return category, units, date_from, date_to, unit_lines

        if not DATE_RE.match(line) and not RANK_RE.match(line):
            units.append(normalize_cc_names(line))
            unit_lines.append(self.current_source_line)
        return category, units, date_from, date_to, unit_lines

    def _parse_detachment_line(
        self, line: str, division: str, attached_to: str
    ) -> str:
        if line.lower().startswith("(attached to)"):
            return ""
        if DATE_RANGE_RE.search(line):
            unit_part, attached_part = line, ""
            if " - " in line and DATE_RANGE_RE.search(line):
                before = DATE_RANGE_RE.split(line)[0]
                unit_part = before.strip()
            attached_to_clean = clean_text(attached_to)
            range_match = DATE_RANGE_RE.search(line)
            self.detachments.append(
                self._row(
                    {
                        "division": division,
                        "unit_name": normalize_cc_names(unit_part),
                        "attached_to_organization": attached_to_clean,
                        "date_from": clean_text(range_match.group(1)) if range_match else "",
                        "date_to": clean_text(range_match.group(2)) if range_match else "",
                        "scope_note": SCOPE_NOTE,
                    }
                )
            )
            return attached_to
        if DATE_RE.match(line):
            return attached_to
        if not DATE_RE.match(line) and len(line) < 60:
            if any(token in line.lower() for token in ("corps", "div", "gp", "force")):
                return normalize_cc_names(line)
            if attached_to:
                self.detachments.append(
                    self._row(
                        {
                            "division": division,
                            "unit_name": normalize_cc_names(line),
                            "attached_to_organization": attached_to,
                            "date_from": "",
                            "date_to": "",
                            "scope_note": SCOPE_NOTE,
                        }
                    )
                )
            else:
                return normalize_cc_names(line)
        return attached_to

    def _flush_higher_units(
        self,
        division: str,
        dates: list[str],
        corps: list[str],
        armies: list[str],
        army_groups: list[str],
        date_lines: list[int],
    ) -> None:
        count = len(dates)
        for idx in range(count):
            self.higher_units.append(
                self._row(
                    {
                        "division": division,
                        "date": dates[idx],
                        "corps": corps[idx] if idx < len(corps) else "",
                        "army": armies[idx] if idx < len(armies) else "",
                        "army_group_other": army_groups[idx] if idx < len(army_groups) else "",
                        "scope_note": SCOPE_NOTE,
                    },
                    source_line=date_lines[idx] if idx < len(date_lines) else None,
                )
            )

    def _parse_higher_unit_line(
        self,
        line: str,
        division: str,
        dates: list[str],
        corps: list[str],
        armies: list[str],
        army_groups: list[str],
        date_lines: list[int],
    ) -> tuple[list[str], list[str], list[str], list[str], list[int]]:
        low = line.lower()
        if low in {"corps", "army", "army group", "army group and other", "asgd", "atchd"}:
            return dates, corps, armies, army_groups, date_lines
        if DATE_RE.match(line) or re.match(r"^\d{1,2}\s+\w{3,9}\s+\d{2,4}$", line):
            dates.append(clean_text(line))
            date_lines.append(self.current_source_line)
            return dates, corps, armies, army_groups, date_lines
        if line in {"-", "—"}:
            corps.append("")
            return dates, corps, armies, army_groups, date_lines
        if line.upper() in {
            "VII", "V", "VI", "III", "VIII", "XII", "XIX", "IX", "IV", "II", "I", "X",
            "XVIII ABN", "XVIII", "XVI", "XX", "XIII", "XIV", "XV",
        }:
            corps.append(clean_text(line))
            return dates, corps, armies, army_groups, date_lines
        if line.title() in {"First", "Third", "Ninth", "Fifteenth", "Eighth", "Twelfth"}:
            armies.append(clean_text(line.title()))
            return dates, corps, armies, army_groups, date_lines
        if line.upper() in {"ETOUSA", "12TH", "21ST"} or "21ST" in line.upper():
            army_groups.append(clean_text(line))
            if len(dates) and len(corps) >= len(dates) and len(armies) >= len(dates):
                self._flush_higher_units(
                    division, dates, corps, armies, army_groups, date_lines
                )
                return [], [], [], [], []
            return dates, corps, armies, army_groups, date_lines
        if len(dates) and len(corps) >= len(dates) and len(armies) >= len(dates):
            self._flush_higher_units(
                division, dates, corps, armies, army_groups, date_lines
            )
            return [], [], [], [], []
        return dates, corps, armies, army_groups, date_lines

    def _flush_command_post_buffers(
        self,
        division: str,
        dates: list[str],
        towns: list[str],
        regions: list[str],
        countries: list[str],
        town_lines: list[int],
    ) -> None:
        count = min(len(dates), len(towns))
        for idx in range(count):
            self.command_posts.append(
                self._row(
                    {
                        "division": division,
                        "date": dates[idx],
                        "town": towns[idx],
                        "region": regions[idx] if idx < len(regions) else "",
                        "country": countries[idx] if idx < len(countries) else "",
                        "scope_note": SCOPE_NOTE,
                    },
                    source_line=town_lines[idx] if idx < len(town_lines) else None,
                )
            )

    def _parse_command_post_line(
        self,
        line: str,
        division: str,
        dates: list[str],
        towns: list[str],
        regions: list[str],
        countries: list[str],
        town_lines: list[int],
    ) -> tuple[list[str], list[str], list[str], list[str], list[int]]:
        if DATE_INLINE_RE.match(line):
            inline = DATE_INLINE_RE.match(line)
            dates.append(clean_text(inline.group(1)))
            towns.append(clean_text(inline.group(2)))
            town_lines.append(self.current_source_line)
            return dates, towns, regions, countries, town_lines
        if re.match(r"^\d{4}\s", line):
            return dates, towns, regions, countries, town_lines
        if DATE_RE.match(line) or re.match(
            rf"^\d{{1,2}}\s+(?:{MONTHS})", line, re.I
        ):
            dates.append(clean_text(line))
            return dates, towns, regions, countries, town_lines
        countries_known = {
            "france",
            "england",
            "scotland",
            "belgium",
            "germany",
            "czech",
            "luxembourg",
            "netherlands",
            "austria",
        }
        if line.lower() in countries_known or line.lower().startswith("czech"):
            countries.append(clean_text(line.title()))
            if len(dates) > len(towns):
                self._flush_command_post_buffers(
                    division, dates, towns, regions, countries, town_lines
                )
                dates, towns, regions, countries = [], [], [], []
                town_lines = []
            return dates, towns, regions, countries, town_lines
        if len(line) < 40 and not RANK_RE.match(line):
            if any(ch.isdigit() for ch in line[:4]):
                return dates, towns, regions, countries, town_lines
            if countries and len(regions) < len(countries):
                regions.append(line)
            elif towns and len(regions) <= len(towns):
                if len(regions) < len(towns):
                    regions.append(line)
                else:
                    towns.append(line)
                    town_lines.append(self.current_source_line)
            else:
                towns.append(line)
                town_lines.append(self.current_source_line)
        return dates, towns, regions, countries, town_lines

    def write_outputs(self, output_dir: Path) -> dict[str, int]:
        outputs = {
            "eto_oob_command_and_staff.csv": (
                [
                    "division",
                    "source_page",
                    "pdf_page",
                    "source_line",
                    "position",
                    "position_role",
                    "effective_date",
                    "rank",
                    "name",
                    "acting",
                    "promotion_or_replacement",
                    "scope_note",
                ],
                self.command_staff,
            ),
            "eto_oob_statistics.csv": (
                [
                    "division",
                    "source_page",
                    "pdf_page",
                    "source_line",
                    "category",
                    "metric",
                    "value",
                    "scope_note",
                ],
                self.statistics,
            ),
            "eto_oob_campaigns.csv": (
                ["division", "source_page", "pdf_page", "source_line", "campaign", "scope_note"],
                self.campaigns,
            ),
            "eto_oob_organic_units.csv": (
                [
                    "division",
                    "source_page",
                    "pdf_page",
                    "source_line",
                    "unit_name",
                    "notes",
                    "scope_note",
                ],
                self.organic_units,
            ),
            "eto_oob_attachments.csv": (
                [
                    "division",
                    "source_page",
                    "pdf_page",
                    "source_line",
                    "category",
                    "parent_unit_name",
                    "unit_name",
                    "date_from",
                    "date_to",
                    "scope_note",
                ],
                self.attachments,
            ),
            "eto_oob_detachments.csv": (
                [
                    "division",
                    "source_page",
                    "pdf_page",
                    "source_line",
                    "unit_name",
                    "attached_to_organization",
                    "date_from",
                    "date_to",
                    "scope_note",
                ],
                self.detachments,
            ),
            "eto_oob_higher_unit_assignments.csv": (
                [
                    "division",
                    "source_page",
                    "pdf_page",
                    "source_line",
                    "date",
                    "army",
                    "corps",
                    "army_group_other",
                    "scope_note",
                ],
                self.higher_units,
            ),
            "eto_oob_command_posts.csv": (
                [
                    "division",
                    "source_page",
                    "pdf_page",
                    "source_line",
                    "date",
                    "town",
                    "region",
                    "country",
                    "scope_note",
                ],
                self.command_posts,
            ),
        }
        required_field = {
            "eto_oob_command_and_staff.csv": "name",
            "eto_oob_organic_units.csv": "unit_name",
            "eto_oob_attachments.csv": "unit_name",
            "eto_oob_detachments.csv": "unit_name",
            "eto_oob_higher_unit_assignments.csv": "date",
            "eto_oob_command_posts.csv": "town",
            "eto_oob_statistics.csv": "metric",
            "eto_oob_campaigns.csv": "campaign",
        }
        counts: dict[str, int] = {}
        for filename, (fields, rows) in outputs.items():
            filtered = filter_rows(rows, required_field.get(filename, "division"))
            write_csv(output_dir / filename, fields, filtered)
            counts[filename] = len(filtered)
        return counts


def apply_csv_search_replacements(output_dir: Path) -> None:
    """Apply config/csv_search_replacements.yaml to extracted ETO OOB CSVs."""
    scripts_dir = Path(__file__).resolve().parent
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))
    from csv_search_replacements import (  # noqa: PLC0415
        apply_to_csv_file,
        load_rule_sets,
        rules_for_columns,
    )

    rules_by_column = rules_for_columns(load_rule_sets())
    if not rules_by_column:
        return

    total_replacements = 0
    touched_files = 0
    for path in sorted(output_dir.glob("eto_oob_*.csv")):
        stats = apply_to_csv_file(path, rules_by_column)
        if not stats.get("_rows_changed"):
            continue
        touched_files += 1
        rows_changed = stats.pop("_rows_changed", 0)
        file_replacements = sum(stats.values())
        total_replacements += file_replacements
        print(
            f"  {path.name}: {rows_changed} row(s), "
            f"{file_replacements} replacement(s) from search/replace rules"
        )
        for column, count in sorted(stats.items()):
            print(f"    {column}: {count}")

    if touched_files:
        print(
            f"Applied {total_replacements} search/replace rule(s) across "
            f"{touched_files} CSV file(s)."
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="Extract ETO Order of Battle CSVs.")
    parser.add_argument("--pdf", type=Path, default=PDF_PATH)
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_DIR)
    parser.add_argument("--start-page", type=int, default=21)
    parser.add_argument("--end-page", type=int, default=570)
    parser.add_argument(
        "--skip-search-replace",
        action="store_true",
        help="Skip post-processing from config/csv_search_replacements.yaml",
    )
    args = parser.parse_args()

    extractor = ETOOrderOfBattleExtractor(args.pdf)
    extractor.extract(start_page=args.start_page, end_page=args.end_page)
    counts = extractor.write_outputs(args.output_dir)
    print(f"Wrote CSVs to {args.output_dir}")
    for name, count in sorted(counts.items()):
        print(f"  {name}: {count} rows")
    if not args.skip_search_replace:
        print("Applying CSV search/replace rules:")
        apply_csv_search_replacements(args.output_dir)


if __name__ == "__main__":
    main()
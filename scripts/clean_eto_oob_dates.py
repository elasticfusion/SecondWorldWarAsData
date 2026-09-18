#!/usr/bin/env python3
"""Repair misaligned and partial date_from/date_to fields in ETO OOB attachment CSVs."""

from __future__ import annotations

import argparse
import calendar
import csv
import re
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ROOT = (
    PROJECT_ROOT
    / "contentrepository"
    / "European Thater of Operations - Order of Battle"
)

MONTHS = (
    r"Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec|"
    r"January|February|March|April|June|July|August|September|"
    r"October|November|December|"
    r"Lee|Hay|Tan|Bee|Liar|Han|Dee|Deo|Aig|Pr|Kar|Jen|Dig|Jim|Say"
)
MONTH_MAP = {
    "jan": 1,
    "feb": 2,
    "mar": 3,
    "apr": 4,
    "may": 5,
    "jun": 6,
    "jul": 7,
    "aug": 8,
    "sep": 9,
    "oct": 10,
    "nov": 11,
    "dec": 12,
    "january": 1,
    "february": 2,
    "march": 3,
    "april": 4,
    "june": 6,
    "july": 7,
    "august": 8,
    "september": 9,
    "october": 10,
    "november": 11,
    "december": 12,
    "lee": 12,
    "hay": 5,
    "tan": 1,
    "bee": 2,
    "liar": 3,
    "han": 1,
    "dee": 12,
    "deo": 12,
    "aig": 8,
    "pr": 4,
    "kar": 3,
    "jen": 1,
    "dig": 8,
    "jim": 1,
    "say": 5,
}
FULL_DATE_RE = re.compile(
    rf"^(\d{{1,2}})\s+({MONTHS})\.?\s+(\d{{2,4}})$",
    re.I,
)
INVALID_DAY_RE = re.compile(
    rf"^(\d{{2}})\s+({MONTHS})\.?\s+(\d{{2}})$",
    re.I,
)
PARTIAL_DATE_RE = re.compile(
    rf"^({MONTHS})\.?\s+(\d{{2,4}})$",
    re.I,
)

TARGET_FILES = ("eto_oob_attachments.csv",)

GROUP_HEADER_RE = re.compile(r"\b(?:Group|Gp)\b", re.I)
UNIT_LIKE_RE = re.compile(
    r"\b(?:Battalion|Battallion|Bn|Company|Cos?|Squadron|Regiment|Battery|Btry|"
    r"Platoon|Plat|Detachment|Det|CT|Infantry|Inf|Division|Div|Artillery|Arty|"
    r"Engineer|Engr|Reconnaissance|Ren|Cavalry|Cav|Armored|Armd|Chemical|Cml|"
    r"Mortar|Mort|Tank|Tk|Destroyer|TD|Obsn|Tr|Group|Gp|FA|CT)\b",
    re.I,
)
MAX_PROPAGATION_GAP = 30
MAX_WIDE_GAP = 45



def normalize_whitespace(value: str) -> str:
    return re.sub(r"\s+", " ", value.replace("\u00a0", " ")).strip()


def normalize_year(year: str) -> str:
    if len(year) == 2:
        return f"19{year}" if int(year) >= 40 else f"20{year}"
    return year


def fix_ocr_day(day: int, month: str, year: str) -> int:
    month_num = MONTH_MAP.get(month[:3].lower())

    def is_valid(candidate: int) -> bool:
        if candidate < 1 or candidate > 31:
            return False
        if month_num:
            return candidate <= calendar.monthrange(int(year), month_num)[1]
        return True

    if is_valid(day):
        return day
    if day < 32:
        return day
    day_text = str(day)
    if len(day_text) == 2:
        ones = day_text[1]
        for tens in "23456789":
            candidate = int(f"{tens}{ones}")
            if is_valid(candidate):
                return candidate
    for start in range(1, len(day_text)):
        candidate = int(day_text[start:])
        if is_valid(candidate):
            return candidate
    return day


def fix_ocr_year(original_day: int, fixed_day: int, month: str, year: str) -> str:
    if year == "1946" and original_day >= 40:
        return "1944"
    return year


def parse_full_date(value: str) -> tuple[int, str, str] | None:
    match = FULL_DATE_RE.match(normalize_whitespace(value))
    if not match:
        return None
    original_day = int(match.group(1))
    month = match.group(2).rstrip(".").lower()
    year = normalize_year(match.group(3))
    day = fix_ocr_day(original_day, month, year)
    year = fix_ocr_year(original_day, day, month, year)
    return day, month, year


def parse_partial_date(value: str) -> tuple[str, str] | None:
    match = PARTIAL_DATE_RE.match(normalize_whitespace(value))
    if not match:
        return None
    month = match.group(1).rstrip(".").lower()
    year = normalize_year(match.group(2))
    return month, year


MONTH_ABBR = (
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
)


def canonical_month_num(day: int, month: str) -> int | None:
    month_key = month[:3].lower()
    month_num = MONTH_MAP.get(month_key)
    if month_key == "bee" and day >= 29:
        return 12
    if month_num and day <= calendar.monthrange(2000, month_num)[1]:
        return month_num
    return month_num


def format_full_date(day: int, month: str, year: str) -> str:
    month_num = canonical_month_num(day, month)
    if month_num:
        month_title = MONTH_ABBR[month_num - 1]
    else:
        month_title = month[:1].upper() + month[1:3].lower()
    return f"{day} {month_title} {year[-2:]}"


def year_field_duplicates_day(day: int, year: str) -> bool:
    if len(year) == 2:
        return int(year) == day
    if len(year) == 4:
        return int(year[-2:]) == day
    return False


def same_day_month(left: str, right: str) -> bool:
    left_match = FULL_DATE_RE.match(normalize_whitespace(left))
    right_match = FULL_DATE_RE.match(normalize_whitespace(right))
    if not left_match or not right_match:
        return False
    left_day = int(left_match.group(1))
    right_day = int(right_match.group(1))
    left_month = left_match.group(2).rstrip(".").lower()[:3]
    right_month = right_match.group(2).rstrip(".").lower()[:3]
    return left_day == right_day and left_month == right_month


def fix_duplicated_day_as_year_pair(date_from: str, date_to: str) -> tuple[str, str]:
    from_match = FULL_DATE_RE.match(normalize_whitespace(date_from))
    to_match = FULL_DATE_RE.match(normalize_whitespace(date_to))
    if not from_match or not to_match:
        return date_from, date_to
    day = int(from_match.group(1))
    month = from_match.group(2).rstrip(".").lower()
    from_year = from_match.group(3)
    to_year = normalize_year(to_match.group(3))
    if not year_field_duplicates_day(day, from_year):
        return date_from, date_to
    if int(to_year) < 1940:
        return date_from, date_to
    fixed_from = format_full_date(day, month, to_year)
    fixed_to = format_full_date(
        int(to_match.group(1)),
        to_match.group(2).rstrip(".").lower(),
        to_year,
    )
    return fixed_from, fixed_to


def repair_duplicated_day_as_year(rows: list[dict]) -> int:
    changes = 0
    for row in rows:
        original_from = row.get("date_from", "").strip()
        original_to = row.get("date_to", "").strip()
        if not original_from or not original_to:
            continue
        date_from, date_to = fix_duplicated_day_as_year_pair(original_from, original_to)
        if date_from != original_from or date_to != original_to:
            row["date_from"] = date_from
            row["date_to"] = date_to
            changes += 1
    return changes


def expand_partial_date(
    partial: str,
    reference: str | None = None,
    *,
    as_end: bool = False,
) -> str:
    partial_bits = parse_partial_date(partial)
    if not partial_bits:
        return normalize_whitespace(partial)
    month, year = partial_bits
    month_num = MONTH_MAP.get(month[:3].lower())
    if reference:
        ref = parse_full_date(reference)
        if ref:
            day, ref_month, ref_year = ref
            same_month = MONTH_MAP.get(ref_month[:3].lower(), 0) == MONTH_MAP.get(
                month[:3].lower(), -1
            )
            if as_end and month_num:
                last_day = calendar.monthrange(int(year), month_num)[1]
                return format_full_date(last_day, month, year)
            if same_month or ref_year == year:
                return format_full_date(day, month, year)
    if month_num:
        last_day = calendar.monthrange(int(year), month_num)[1]
        return format_full_date(last_day, month, year)
    month_title = month[:1].upper() + month[1:3].lower()
    return f"{month_title} {year[-2:]}"


def is_full_date(value: str) -> bool:
    return bool(value and parse_full_date(value))


def is_partial_date(value: str) -> bool:
    return bool(value and parse_partial_date(value))


def source_line_num(row: dict) -> int:
    try:
        return int(row.get("source_line") or 0)
    except ValueError:
        return 0


def same_month_year(left: str, right: str) -> bool:
    left_bits = parse_full_date(left) or (
        (1, *parse_partial_date(left)) if parse_partial_date(left) else None
    )
    right_bits = parse_full_date(right) or (
        (1, *parse_partial_date(right)) if parse_partial_date(right) else None
    )
    if not left_bits or not right_bits:
        return False
    left_month = MONTH_MAP.get(str(left_bits[1])[:3].lower(), -1)
    right_month = MONTH_MAP.get(str(right_bits[1])[:3].lower(), -1)
    left_year = normalize_year(str(left_bits[2]))
    right_year = normalize_year(str(right_bits[2]))
    return left_month == right_month and left_year == right_year


def month_start_from_date(date_value: str) -> str:
    parsed = parse_full_date(date_value)
    if not parsed:
        partial = parse_partial_date(date_value)
        if not partial:
            return ""
        month, year = partial
        return format_full_date(1, month, year)
    _day, month, year = parsed
    return format_full_date(1, month, year)


def is_group_header(name: str) -> bool:
    name = normalize_whitespace(name)
    return bool(GROUP_HEADER_RE.search(name)) and "(" not in name


def is_propagatable_unit(name: str) -> bool:
    name = normalize_whitespace(name)
    if not name or len(name) < 4:
        return False
    if re.match(r"^[\W\d_]+$", name):
        return False
    if re.match(r"^ATTAC(?:H|IM)\b", name, re.I):
        return False
    return (
        bool(UNIT_LIKE_RE.search(name))
        or "(" in name
        or is_group_header(name)
        or bool(re.search(r"\b\d{1,3}(?:st|nd|rd|th)\b", name, re.I))
    )


def row_dates(row: dict) -> tuple[str, str]:
    return row.get("date_from", "").strip(), row.get("date_to", "").strip()


def row_has_full_range(row: dict) -> bool:
    date_from, date_to = row_dates(row)
    return is_full_date(date_from) and is_full_date(date_to)


def normalize_row_dates(row: dict) -> tuple[str, str]:
    date_from, date_to = row_dates(row)
    if is_partial_date(date_from):
        date_from = expand_partial_date(date_from, date_to or None)
    if is_partial_date(date_to):
        date_to = expand_partial_date(date_to, date_from or None, as_end=True)
    date_from = normalize_date_field(date_from)
    date_to = normalize_date_field(date_to)
    if is_partial_date(date_to) and is_full_date(date_from):
        date_to = expand_partial_date(date_to, date_from, as_end=True)
    if is_partial_date(date_from) and is_full_date(date_to):
        date_from = month_start_from_date(date_to)
    return date_from, date_to


def nearest_anchor(
    ordered: list[dict],
    index: int,
    *,
    direction: int,
    max_gap: int = MAX_PROPAGATION_GAP,
) -> dict | None:
    origin = source_line_num(ordered[index])
    best: dict | None = None
    best_gap = max_gap + 1
    if direction < 0:
        candidates = range(index - 1, -1, -1)
    else:
        candidates = range(index + 1, len(ordered))
    for candidate in candidates:
        row = ordered[candidate]
        if not row_has_full_range(row):
            continue
        gap = abs(source_line_num(row) - origin)
        if gap <= max_gap and gap < best_gap:
            best = row
            best_gap = gap
    return best


def find_matching_start_in_block(ordered: list[dict], date_from: str) -> str:
    normalized_from = normalize_date_field(date_from)
    for row in ordered:
        row_from, row_to = row_dates(row)
        if (
            normalize_date_field(row_from) == normalized_from
            and is_full_date(row_to)
        ):
            return row_to
    return ""


def fill_shared_start_clusters(ordered: list[dict]) -> int:
    changes = 0
    index = 0
    while index < len(ordered):
        row = ordered[index]
        if not is_propagatable_unit(row.get("unit_name", "")):
            index += 1
            continue
        date_from, date_to = normalize_row_dates(row)
        if not is_full_date(date_from) or date_to:
            index += 1
            continue

        cluster = [row]
        cursor = index + 1
        while cursor < len(ordered):
            candidate = ordered[cursor]
            candidate_from, candidate_to = normalize_row_dates(candidate)
            if (
                is_propagatable_unit(candidate.get("unit_name", ""))
                and normalize_date_field(candidate_from) == normalize_date_field(date_from)
                and not candidate_to
            ):
                cluster.append(candidate)
                cursor += 1
            else:
                break

        shared_to = find_matching_start_in_block(ordered, date_from) or date_from
        shared_to = normalize_date_field(shared_to)
        for cluster_row in cluster:
            original_to = cluster_row.get("date_to", "").strip()
            if original_to != shared_to:
                cluster_row["date_to"] = shared_to
                changes += 1
        index = cursor
    return changes


def fill_open_end_dates(ordered: list[dict]) -> int:
    changes = 0
    for index, row in enumerate(ordered):
        if not is_propagatable_unit(row.get("unit_name", "")):
            continue
        date_from, date_to = normalize_row_dates(row)
        if not is_full_date(date_from) or date_to:
            continue
        prev_anchor = nearest_anchor(ordered, index, direction=-1)
        next_anchor = nearest_anchor(ordered, index, direction=1)
        if prev_anchor and row_dates(prev_anchor)[0] == date_from:
            date_to = row_dates(prev_anchor)[1]
        elif next_anchor and row_dates(next_anchor)[0] == date_from:
            date_to = row_dates(next_anchor)[1]
        else:
            date_to = date_from
        date_to = normalize_date_field(date_to)
        if date_to != row.get("date_to", "").strip():
            row["date_to"] = date_to
            changes += 1
    return changes


def propagate_dates_wide(ordered: list[dict]) -> int:
    changes = 0
    for index, row in enumerate(ordered):
        if not is_propagatable_unit(row.get("unit_name", "")):
            continue
        if row_has_full_range(row):
            continue
        original_from, original_to = row_dates(row)
        if original_from and original_to:
            continue
        prev_anchor = nearest_anchor(
            ordered, index, direction=-1, max_gap=MAX_WIDE_GAP
        )
        next_anchor = nearest_anchor(
            ordered, index, direction=1, max_gap=MAX_WIDE_GAP
        )
        date_from, date_to = normalize_row_dates(row)
        if not date_from and not date_to:
            if prev_anchor:
                date_from, date_to = row_dates(prev_anchor)
            elif next_anchor:
                date_from, date_to = row_dates(next_anchor)
        date_from = normalize_date_field(date_from)
        date_to = normalize_date_field(date_to)
        if date_from != original_from or date_to != original_to:
            row["date_from"] = date_from
            row["date_to"] = date_to
            changes += 1
    return changes


def propagate_dates_in_block(rows: list[dict]) -> int:
    changes = 0
    ordered = sorted(rows, key=source_line_num)
    for index, row in enumerate(ordered):
        if not is_propagatable_unit(row.get("unit_name", "")):
            continue
        original_from, original_to = row_dates(row)
        date_from, date_to = normalize_row_dates(row)
        name = row.get("unit_name", "")

        if row_has_full_range({"date_from": date_from, "date_to": date_to}):
            if date_from != original_from or date_to != original_to:
                row["date_from"] = date_from
                row["date_to"] = date_to
                changes += 1
            continue

        prev_anchor = nearest_anchor(ordered, index, direction=-1)
        next_anchor = nearest_anchor(ordered, index, direction=1)

        if not date_from and not date_to:
            if prev_anchor:
                date_from, date_to = row_dates(prev_anchor)
            elif next_anchor:
                date_from, date_to = row_dates(next_anchor)
        elif not date_from and date_to:
            anchor_from = row_dates(prev_anchor)[0] if prev_anchor else ""
            anchor_to = row_dates(prev_anchor)[1] if prev_anchor else ""
            if (
                prev_anchor
                and anchor_from
                and same_month_year(anchor_to or anchor_from, date_to)
            ):
                date_from = anchor_from
            else:
                inferred = month_start_from_date(date_to)
                if inferred:
                    date_from = inferred
        elif date_from and not date_to:
            matched_to = find_matching_start_in_block(ordered, date_from)
            if matched_to:
                date_to = matched_to
            elif prev_anchor and normalize_date_field(row_dates(prev_anchor)[0]) == normalize_date_field(date_from):
                date_to = row_dates(prev_anchor)[1]
            elif next_anchor and normalize_date_field(row_dates(next_anchor)[0]) == normalize_date_field(date_from):
                date_to = row_dates(next_anchor)[1]

        date_from = normalize_date_field(date_from)
        date_to = normalize_date_field(date_to)

        if date_from != original_from or date_to != original_to:
            row["date_from"] = date_from
            row["date_to"] = date_to
            changes += 1

    for index, row in enumerate(ordered):
        if not is_propagatable_unit(row.get("unit_name", "")):
            continue
        original_from, original_to = row_dates(row)
        date_from, date_to = original_from, original_to
        prev_anchor = nearest_anchor(ordered, index, direction=-1)
        if (
            prev_anchor
            and is_full_date(date_from)
            and is_full_date(date_to)
            and date_from == row_dates(prev_anchor)[0]
            and not same_month_year(row_dates(prev_anchor)[1] or date_from, date_to)
        ):
            corrected_from = month_start_from_date(date_to)
            if corrected_from:
                date_from = corrected_from
        if date_from != original_from or date_to != original_to:
            row["date_from"] = date_from
            row["date_to"] = date_to
            changes += 1
    return changes


def preprocess_date_ocr(value: str) -> str:
    value = normalize_whitespace(value)
    value = re.sub(r"\b81\s+(?:Kar|Mar)\b", "21 Mar", value, flags=re.I)
    value = re.sub(r"\bS3\s+(?:Liar|Mar)\b", "23 Mar", value, flags=re.I)
    return value


def normalize_date_field(value: str) -> str:
    value = preprocess_date_ocr(value)
    if not value:
        return ""
    invalid_day = INVALID_DAY_RE.match(value)
    if invalid_day:
        original_day = int(invalid_day.group(1))
        month = invalid_day.group(2).rstrip(".").lower()
        year = normalize_year(invalid_day.group(3))
        fixed_day = fix_ocr_day(original_day, month, year)
        fixed_year = fix_ocr_year(original_day, fixed_day, month, year)
        if fixed_day != original_day or fixed_year != year:
            return format_full_date(fixed_day, month, fixed_year)
    if is_full_date(value):
        parsed = parse_full_date(value)
        assert parsed is not None
        return format_full_date(*parsed)
    if is_partial_date(value):
        partial = parse_partial_date(value)
        assert partial is not None
        month_title = partial[0][:1].upper() + partial[0][1:3].lower()
        return f"{month_title} {partial[1][-2:]}"
    return value


def subblocks_for_rows(rows: list[dict]) -> list[list[dict]]:
    """Split a category block around rows that already have two full dates."""
    subblocks: list[list[dict]] = []
    current: list[dict] = []
    for row in rows:
        date_from = row.get("date_from", "").strip()
        date_to = row.get("date_to", "").strip()
        if is_full_date(date_from) and is_full_date(date_to):
            if current:
                subblocks.append(current)
                current = []
            continue
        current.append(row)
    if current:
        subblocks.append(current)
    return subblocks


def repair_subblock(rows: list[dict]) -> int:
    changes = 0
    orphan_from = [
        row["date_from"].strip()
        for row in rows
        if is_full_date(row.get("date_from", "")) and not is_full_date(row.get("date_to", ""))
    ]
    orphan_to = [
        row["date_to"].strip()
        for row in rows
        if row.get("date_to", "").strip() and not is_full_date(row.get("date_to", ""))
    ]
    full_to = [
        row["date_to"].strip()
        for row in rows
        if is_full_date(row.get("date_to", "")) and not is_full_date(row.get("date_from", ""))
    ]

    shared_from = orphan_from[0] if len(orphan_from) == 1 else ""
    shared_to_raw = orphan_to[0] if orphan_to else (full_to[0] if len(full_to) == 1 else "")
    if not shared_from and not shared_to_raw:
        for row in rows:
            for field in ("date_from", "date_to"):
                original = row.get(field, "")
                normalized = normalize_date_field(original)
                if normalized != original:
                    row[field] = normalized
                    changes += 1
        return changes

    shared_to = ""
    if shared_to_raw:
        shared_to = (
            expand_partial_date(shared_to_raw, shared_from, as_end=True)
            if is_partial_date(shared_to_raw)
            else normalize_date_field(shared_to_raw)
        )

    for row in rows:
        original_from = row.get("date_from", "").strip()
        original_to = row.get("date_to", "").strip()
        new_from = original_from
        new_to = original_to

        if shared_from and (not new_from or is_partial_date(new_from)):
            new_from = shared_from
        if shared_to and (not new_to or is_partial_date(new_to)):
            new_to = (
                expand_partial_date(new_to, new_from or shared_from, as_end=True)
                if is_partial_date(new_to)
                else shared_to
            )

        new_from = normalize_date_field(new_from)
        new_to = normalize_date_field(new_to)

        if is_partial_date(new_to) and is_full_date(new_from):
            new_to = expand_partial_date(new_to, new_from, as_end=True)
        if is_partial_date(new_from) and is_full_date(new_to):
            new_from = expand_partial_date(new_from, new_to)

        if new_from != original_from or new_to != original_to:
            row["date_from"] = new_from
            row["date_to"] = new_to
            changes += 1
    return changes


def propagate_dates_page_wide(page_rows: list[dict]) -> int:
    changes = 0
    ordered = sorted(page_rows, key=source_line_num)
    for index, row in enumerate(ordered):
        if not is_propagatable_unit(row.get("unit_name", "")):
            continue
        original_from, original_to = row_dates(row)
        if original_from and original_to:
            continue
        date_from, date_to = normalize_row_dates(row)
        prev_anchor = nearest_anchor(
            ordered, index, direction=-1, max_gap=MAX_WIDE_GAP
        )
        next_anchor = nearest_anchor(
            ordered, index, direction=1, max_gap=MAX_WIDE_GAP
        )
        if not date_from and not date_to:
            if prev_anchor:
                date_from, date_to = row_dates(prev_anchor)
            elif next_anchor:
                date_from, date_to = row_dates(next_anchor)
        elif date_from and not date_to:
            matched_to = find_matching_start_in_block(ordered, date_from)
            if matched_to:
                date_to = matched_to
            elif prev_anchor and normalize_date_field(row_dates(prev_anchor)[0]) == normalize_date_field(date_from):
                date_to = row_dates(prev_anchor)[1]
            elif next_anchor and normalize_date_field(row_dates(next_anchor)[0]) == normalize_date_field(date_from):
                date_to = row_dates(next_anchor)[1]
            else:
                date_to = date_from
        elif not date_from and date_to:
            if prev_anchor:
                date_from = row_dates(prev_anchor)[0]
            else:
                date_from = month_start_from_date(date_to)
        date_from = normalize_date_field(date_from)
        date_to = normalize_date_field(date_to)
        if date_from != original_from or date_to != original_to:
            row["date_from"] = date_from
            row["date_to"] = date_to
            changes += 1
    return changes


def repair_rows(rows: list[dict]) -> int:
    changes = 0
    block: list[dict] = []
    block_key: tuple[str, str, str] | None = None

    def flush() -> None:
        nonlocal changes, block, block_key
        if not block:
            return
        for subblock in subblocks_for_rows(block):
            changes += repair_subblock(subblock)
        ordered = sorted(block, key=source_line_num)
        changes += propagate_dates_in_block(block)
        changes += fill_shared_start_clusters(ordered)
        changes += fill_open_end_dates(ordered)
        changes += propagate_dates_wide(ordered)
        block = []
        block_key = None

    for row in rows:
        key = (
            row.get("division", ""),
            row.get("source_page", ""),
            row.get("category", ""),
        )
        if block_key is not None and key != block_key:
            flush()
        block_key = key
        block.append(row)
    flush()

    page_groups: dict[tuple[str, str], list[dict]] = {}
    for row in rows:
        page_key = (row.get("division", ""), row.get("source_page", ""))
        page_groups.setdefault(page_key, []).append(row)
    for page_rows in page_groups.values():
        changes += propagate_dates_page_wide(page_rows)
    changes += repair_duplicated_day_as_year(rows)
    for row in rows:
        for field in ("date_from", "date_to"):
            original = row.get(field, "").strip()
            if not original:
                continue
            normalized = normalize_date_field(original)
            if normalized != original:
                row[field] = normalized
                changes += 1
    return changes


def clean_csv(path: Path, *, dry_run: bool = False) -> dict[str, int]:
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames:
            return {}
        fieldnames = reader.fieldnames
        rows = list(reader)

    changes = repair_rows(rows)
    if not dry_run:
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames, lineterminator="\n")
            writer.writeheader()
            writer.writerows(rows)
    return {"rows": len(rows), "changes": changes}


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Repair misaligned date_from/date_to fields in ETO OOB attachment CSVs."
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=DEFAULT_ROOT,
        help="Directory containing ETO OOB CSV files",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report changes without writing files",
    )
    args = parser.parse_args()

    total_changes = 0
    for filename in TARGET_FILES:
        path = args.root / filename
        if not path.is_file():
            continue
        stats = clean_csv(path, dry_run=args.dry_run)
        suffix = " (dry run)" if args.dry_run else ""
        print(
            f"{filename}: {stats.get('rows', 0)} rows{suffix}; "
            f"repaired {stats.get('changes', 0)} date field(s)"
        )
        total_changes += stats.get("changes", 0)

    action = "Would repair" if args.dry_run else "Repaired"
    print(f"\n{action} {total_changes} date field(s).")


if __name__ == "__main__":
    main()
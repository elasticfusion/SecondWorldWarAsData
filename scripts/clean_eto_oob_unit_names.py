#!/usr/bin/env python3
"""Remove chronology junk rows and strip embedded date bleed from ETO OOB CSVs."""

from __future__ import annotations

import argparse
import csv
import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ROOT = (
    PROJECT_ROOT
    / "contentrepository"
    / "European Thater of Operations - Order of Battle"
)

sys.path.insert(0, str(PROJECT_ROOT / "scripts"))
from extract_eto_order_of_battle import (  # noqa: E402
    clean_text,
    is_division_page_header_unit_name,
    normalize_attachment_category,
)

MONTHS = (
    r"Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec|"
    r"January|February|March|April|June|July|August|September|"
    r"October|November|December|"
    r"Lee|Hay|Tan|Bee|Liar|Han|Dee|Deo|Aig|Pr|Kar|Jen|Dig|Jim|Say"
)
MONTH_TOKEN = rf"!?(?:{MONTHS})"
DATE_LIKE = re.compile(rf"\d{{1,2}}\s+!?[A-Za-z]{{2,6}}\s+\d{{2,4}}", re.I)
DATE_ANY = re.compile(rf"\d{{1,2}}\s+{MONTH_TOKEN}\.?\s+\d{{2,4}}", re.I)
DATE_START = re.compile(rf"^\d{{1,2}}\s+{MONTH_TOKEN}", re.I)
UNIT_KW = re.compile(
    r"\b(Bn|Battalion|Company|Regiment|Infantry|Division|CCR|CCA|CCB|"
    r"Gp|Group|Squadron|Platoon|CT|Cav|Cavalry|Engineer|Artillery|Cml|"
    r"Mort|Tank|Field|Armored|Battery|Btry|Reconnaissance|Antiaircraft|"
    r"Automatic|Weapons|Engineers?|Battallions?|Inf|FA|TD|TB|Arty|Ren|Hq|"
    r"Plat|Cos|Batteries|Tr|Propelled|Mobile)\b",
    re.I,
)
STAFF = re.compile(
    r"^(?:Comdg\s*Gen|CofS|ACofS|Asst\s+Division|Arty\s*Comdr|Adj\s*Gen|"
    r"CO\s+\d|AcofS|JXofS|Adj\s*Qr|CO\s+Sloth|i\s*rty\s*Comdr|Comdg\s*Gea|"
    r"CQfS|inf\s+\d|GpfS|xXofS|CC\s*,1\s*Comdr|CG\s*R|Arty\s*\"?comdr|"
    r"ACo'?f\s*S|XofS|CofS~|/'\s*AC|CCR\s*Comdr)",
    re.I,
)
CHRONOLOGY_NOTE = re.compile(
    r"^(?:#|@|/|French\s+attachments|First\s+Elements|Arrived\s+STO|"
    r"Disbanded|Reorganized|Activated\b|To\s+Higher|Indicates\s+relieved|"
    r"\(-\)\s*Indicates)",
    re.I,
)
ASSIGNED_ATTACHED_HEADER = re.compile(
    r"ASSIG[A-Za-z0-9'£\s]*ATTAC[A-Za-z0-9'£\s]*",
    re.I,
)
ARMY_ECHELON_JUNK = re.compile(
    r"\b(?:I{1,3}|IV|VI{1,2}|XV|xx|XXI|XX)\s*(?:\.?\s*)?Seventh\b",
    re.I,
)
ATTACHMENT_HEADER = re.compile(
    r"^(?:"
    r"[ALit]?TTACH[A-Za-z0-9'£\s]{2,24}"
    r"|ATTAC[A-Za-z0-9'£\s]{0,24}"
    r"|ATTACKS"
    r"|ASSIG[A-Za-z0-9'£\s]*ATTAC[A-Za-z0-9'£\s]*"
    r")(?:\s*\([^)]*\))?\s*$",
    re.I,
)
DETACHMENT_HEADER = re.compile(
    r"^[\.\s]*(?:DETACHMENTS?|DETACH\s+SOTS?)[';]?(?:\s*\([^)]*\))?\s*$",
    re.I,
)
JUNK_CHARS = re.compile(r"[\.,;:\*•'\"<>/\\^%vV~_!?=-]")
DATE_TAIL = re.compile(
    rf"\d{{1,2}}\s+{MONTH_TOKEN}\.?\s+\d{{2,4}}"
    rf"(?:\s+[\w'?£\s~^•\*-]{{0,20}}{MONTH_TOKEN}\.?\s+\d{{2,4}})?\s*$",
    re.I,
)
HEAVY_JUNK_LEAD = re.compile(r"^[©©=|~^«%]")
HEAVY_JUNK_PATTERN = re.compile(
    r"r~:::|:~:~|~:~|\\V;;\*|Task\s+force['\*]|^\d+\^|\bFABh\b|\bDSIACBMIKIS\b"
)
SEMI_AST_DENSE = re.compile(r"[;:*]")

STRIP_RULES = [
    (
        rf"(Division),(?:\s*)?\d{{1,2}}\s+{MONTH_TOKEN}\.?\s+\d{{2,4}}\s*~?\s*$",
        r"\1",
    ),
    (
        rf"\s*((?:Corps|DiV|Div|Liv|Piv|CT))\d{{1,2}}\s+{MONTH_TOKEN}\.?\s+\d{{2,4}}\s*~?\s*$",
        r" \1",
    ),
    (
        rf"(?<![0-9])(?:Div|DiV|Liv|Piv)\d{{1,2}}\s+{MONTH_TOKEN}\.?\s+\d{{2,4}}\s*~?\s*$",
        "",
    ),
    (
        rf"(?<=[a-zA-Z])(?=\d{{1,2}}\s+{MONTH_TOKEN}\.?\s+\d{{2,4}}\s*$)",
        " ",
    ),
    (
        rf"\s+\d{{1,2}}\s+{MONTH_TOKEN}\.?\s+\d{{2,4}}\s+"
        rf"[\d'?£\s~^•\*-]{{0,12}}{MONTH_TOKEN}\.?\s+\d{{2,4}}\s*[~^'\"]*\s*$",
        "",
    ),
    (rf"\s*[,;]\s*\d{{1,2}}\s+{MONTH_TOKEN}\.?\s+\d{{2,4}}\s*$", ""),
    (rf"\s*[,;]{{2,}}\s*\d{{1,2}}\s+{MONTH_TOKEN}\.?\s+\d{{2,4}}\s*$", ""),
    (rf"\s+\d{{1,2}}\s+{MONTH_TOKEN}\.?\s+\d{{2,4}}\s*~\s*$", ""),
    (rf"\s+\d{{1,2}}\s+{MONTH_TOKEN}\.?\s+\d{{2,4}}\s*$", ""),
    (rf"\s*[\.,;:]+[A-Za-z]?\d{{1,2}}\s+{MONTH_TOKEN}\.?\s+\d{{2,4}}\s*$", ""),
    (rf"\s+\d{{1,2}}\s+{MONTH_TOKEN}\.?\s+\d\s+\d\s*$", ""),
    (
        rf"\s+\d{{1,2}}\s+{MONTH_TOKEN}\.?\s+\d{{2,4}}"
        rf"(?:\s+[\d\s']+)?(?:\s+[A-Za-z]\s+){{2,5}}\d{{2,4}}\s*$",
        "",
    ),
    (rf"\s+\d{{1,2}}\s+{MONTH_TOKEN}\.?\s+\d{{2,4}}.*$", ""),
    (r"\s*„\s*$", ""),
]
COMPILED_STRIP = [(re.compile(pattern), repl) for pattern, repl in STRIP_RULES]

TARGET_FILES = (
    "eto_oob_attachments.csv",
    "eto_oob_detachments.csv",
    "eto_oob_organic_units.csv",
)


def is_chronology_junk_unit_name(name: str) -> bool:
    name = name.strip()
    if not name:
        return True
    if CHRONOLOGY_NOTE.search(name):
        return True
    if ATTACHMENT_HEADER.match(name) and not UNIT_KW.search(name):
        return True
    if DETACHMENT_HEADER.match(name):
        return True
    if re.search(r"field\s+art", name, re.I) and re.search(
        r"(?:\(contd|\(cantd|\(gontd)\b", name, re.I
    ):
        return True
    if STAFF.search(name):
        return True
    if DATE_START.match(name) and not UNIT_KW.search(name):
        return True
    if (
        re.match(rf"^\d{{1,2}}\s+(?:{MONTHS})\b", name, re.I)
        and len(name) < 25
        and not UNIT_KW.search(name)
    ):
        return True
    if re.match(r"^[\d\s/:'\"•\*\.;,=-]+$", name):
        return True
    if re.match(r"^_\s*\d+\s*_$", name):
        return True
    if re.match(r"^[_\s\d]+$", name) and len(name) < 15 and not UNIT_KW.search(name):
        return True
    if re.match(rf"^(?:[a-z]\s+)?\d{{1,2}}\s+(?:{MONTHS})\b", name, re.I) and not UNIT_KW.search(
        name
    ):
        return True
    if re.match(r"^[A-Za-z]\s+r\s+[\W\s]+\d", name):
        return True
    if re.match(rf"^lb\s+(?:{MONTHS})\b", name, re.I):
        return True
    date_like = DATE_LIKE.findall(name)
    if len(date_like) >= 2 and not UNIT_KW.search(name):
        return True
    if re.search(r"\bffnfant\b", name, re.I):
        return True
    if re.match(r"^[a-z]*fant\s+rv_?$", name, re.I):
        return True
    if ASSIGNED_ATTACHED_HEADER.search(name) and not UNIT_KW.search(name):
        return True
    if re.search(r"\bAsgd\b", name, re.I) and not UNIT_KW.search(name):
        return True
    if ARMY_ECHELON_JUNK.search(name) and not UNIT_KW.search(name):
        return True
    if re.match(r"^t\s*\\?$", name):
        return True
    if re.match(r"^mm$", name, re.I):
        return True
    if re.search(r"\bWovkk", name, re.I):
        return True
    if re.search(r"100th\s+Infantry\s+Div", name, re.I) and len(name) < 40:
        return True
    return False


def is_heavily_corrupted_unit_name(name: str) -> bool:
    name = name.strip()
    if not name:
        return True
    if HEAVY_JUNK_LEAD.match(name):
        return True
    if HEAVY_JUNK_PATTERN.search(name):
        return True
    if len(name) < 6:
        return False
    alnum = sum(char.isalnum() for char in name)
    ratio = alnum / len(name)
    junk = len(SEMI_AST_DENSE.findall(name))
    has_unit_kw = bool(UNIT_KW.search(name))
    if ratio < 0.20 and not has_unit_kw:
        return True
    if ratio < 0.30 and junk >= 8:
        return True
    if junk >= 10 and ratio < 0.45:
        return True
    if not has_unit_kw and ratio < 0.35 and junk >= 5:
        return True
    if re.search(r";{3,}", name) and ratio < 0.55:
        return True
    if re.search(
        r"['\"]>SGiCo|Y CbA|Gml :Sn:|7Gth\.:inf|0\?k Battalion|r'0o B|Bov\^Tfrr",
        name,
        re.I,
    ):
        return True
    return False


def is_corrupted_unit_name(name: str) -> bool:
    if DATE_ANY.search(name):
        junk = len(JUNK_CHARS.findall(name))
        if junk >= 8:
            return True
        if junk >= 4 and len(name) > 40:
            return True
        if re.search(r"\d\s+'\s*\d", name) or re.search(
            r"[A-Za-z]{2,}\d{1,2}\s+(?:Apr|Mar)", name
        ):
            return True
        if len(DATE_LIKE.findall(name)) >= 2 and name.count("'") >= 2:
            return True
    if re.match(r"^[\d.]+?\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)", name, re.I):
        return True
    return False


def strip_date_bleed(name: str) -> str:
    cur = name.strip()
    for _ in range(4):
        prev = cur
        for pattern, repl in COMPILED_STRIP:
            cur = pattern.sub(repl, cur).strip()
        match = DATE_TAIL.search(cur)
        if match and UNIT_KW.search(cur[: match.start()]):
            gap = cur[: match.start()][-30:]
            if JUNK_CHARS.search(gap) or re.search(r"\s{2,}", gap):
                cur = cur[: match.start()].rstrip(" .,;:'\"-*")
        if cur == prev:
            break
    return cur


def is_attachment_category_header_unit_name(name: str) -> bool:
    """True when unit_name is a section category label (e.g. OCR Tank Lestroyer), not a unit."""
    text = clean_text(name)
    if not text or len(text) > 48:
        return False
    return bool(normalize_attachment_category(text))


def clean_unit_name(name: str) -> str | None:
    if is_chronology_junk_unit_name(name):
        return None
    cleaned = strip_date_bleed(name)
    if is_chronology_junk_unit_name(cleaned):
        return None
    if is_corrupted_unit_name(cleaned):
        return None
    if is_heavily_corrupted_unit_name(cleaned):
        return None
    return cleaned


def clean_csv(path: Path, *, dry_run: bool = False) -> dict[str, int]:
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames or "unit_name" not in reader.fieldnames:
            return {}
        fieldnames = reader.fieldnames
        rows = list(reader)

    stats = {
        "rows_in": len(rows),
        "rows_out": 0,
        "deleted_junk": 0,
        "deleted_corrupt": 0,
        "stripped_dates": 0,
    }
    cleaned_rows: list[dict] = []
    for row in rows:
        original = row.get("unit_name", "")
        if path.name == "eto_oob_attachments.csv" and is_division_page_header_unit_name(
            original, row.get("division", "")
        ):
            stats["deleted_junk"] += 1
            continue
        if path.name == "eto_oob_attachments.csv" and is_attachment_category_header_unit_name(
            original
        ):
            stats["deleted_junk"] += 1
            continue
        cleaned = clean_unit_name(original)
        if cleaned is None:
            if is_chronology_junk_unit_name(original.strip()):
                stats["deleted_junk"] += 1
            else:
                stats["deleted_corrupt"] += 1
            continue
        if cleaned != original:
            stats["stripped_dates"] += 1
            if not dry_run:
                row["unit_name"] = cleaned
        cleaned_rows.append(row)

    stats["rows_out"] = len(cleaned_rows)
    if not dry_run:
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames, lineterminator="\n")
            writer.writeheader()
            writer.writerows(cleaned_rows)
    return stats


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Remove chronology junk and strip embedded dates from ETO OOB unit_name fields."
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

    total_deleted = 0
    total_stripped = 0
    for filename in TARGET_FILES:
        path = args.root / filename
        if not path.is_file():
            continue
        stats = clean_csv(path, dry_run=args.dry_run)
        if not stats:
            continue
        deleted = stats["deleted_junk"] + stats["deleted_corrupt"]
        total_deleted += deleted
        total_stripped += stats["stripped_dates"]
        suffix = " (dry run)" if args.dry_run else ""
        print(
            f"{filename}: {stats['rows_in']} -> {stats['rows_out']} rows{suffix}; "
            f"deleted {stats['deleted_junk']} junk + {stats['deleted_corrupt']} corrupt; "
            f"stripped dates in {stats['stripped_dates']}"
        )

    action = "Would change" if args.dry_run else "Changed"
    print(f"\n{action} {total_deleted} row deletions and {total_stripped} unit_name strips.")


if __name__ == "__main__":
    main()
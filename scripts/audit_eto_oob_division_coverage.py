#!/usr/bin/env python3
"""Audit ETO OOB CSV division coverage against the source PDF."""

from __future__ import annotations

import csv
import re
from collections import defaultdict
from pathlib import Path

import fitz
import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[1]
import sys

sys.path.insert(0, str(PROJECT_ROOT / "scripts"))
from extract_eto_order_of_battle import (  # noqa: E402
    CANONICAL_DIVISIONS,
    build_page_lines,
    match_division,
)

DEFAULT_ROOT = (
    PROJECT_ROOT
    / "contentrepository"
    / "European Thater of Operations - Order of Battle"
)
DEFAULT_CONFIG = PROJECT_ROOT / "config" / "eto_oob_pdf_page_division_overrides.yaml"
DEFAULT_REPORT = PROJECT_ROOT / "docs/current/dataquality/eto_oob_division_coverage_report.md"

CSV_FILES = {
    "command_and_staff": "eto_oob_command_and_staff.csv",
    "statistics": "eto_oob_statistics.csv",
    "campaigns": "eto_oob_campaigns.csv",
    "organic_units": "eto_oob_organic_units.csv",
    "attachments": "eto_oob_attachments.csv",
    "detachments": "eto_oob_detachments.csv",
    "higher_units": "eto_oob_higher_unit_assignments.csv",
    "command_posts": "eto_oob_command_posts.csv",
}

SECTION_LABELS = {
    "command_and_staff": "Command & Staff",
    "statistics": "Statistics",
    "campaigns": "Campaigns",
    "organic_units": "Organic Units",
    "attachments": "Attachments",
    "detachments": "Detachments",
    "higher_units": "Higher Units",
    "command_posts": "Command Posts",
}

# 1st Armored is Mediterranean-only and not in this ETO volume body.
ETO_VOLUME_DIVISIONS = [d for d in CANONICAL_DIVISIONS if d != "1st Armored Division"]


def sort_division(name: str) -> tuple:
    kind_order = {"Infantry": 0, "Armored": 1, "Airborne": 2}
    kind = next((k for k in kind_order if k in name), "Other")
    match = re.match(r"(\d+)", name)
    return (kind_order.get(kind, 9), int(match.group(1)) if match else 999, name)


TOC_LINE_OVERRIDES = {
    "lid infantry division": "2d Infantry Division",
    "kl3th infantry division": "9th Infantry Division",
    "xi3th airborne division": "13th Airborne Division",
    "i-50th infantry division": "30th Infantry Division",
    "715 th infantry division": "76th Infantry Division",
    "ktfeth infantry division": "95th Infantry Division",
    "c%6 armored division": "4th Armored Division",
    "tjst yi armored division": "13th Armored Division",
    "k 6th armored division": "16th Armored Division",
}


def canonical_from_toc_line(line: str) -> str | None:
    cleaned = re.sub(r"[^\x20-\x7E]", " ", line)
    cleaned = re.sub(r"\s+", " ", cleaned).strip().lower()
    cleaned = re.sub(r"^[^a-z0-9]+", "", cleaned)
    if cleaned in TOC_LINE_OVERRIDES:
        return TOC_LINE_OVERRIDES[cleaned]
    for pattern, repl in (
        (r",;3d\b", "3d"),
        (r"i-50th\b", "30th"),
        (r"h34th\b", "84th"),
        (r"\^s6th\b", "86th"),
        (r"u-£04th\b", "104th"),
        (r"h06th\b", "106th"),
        (r'\*-"07th\b', "97th"),
        (r">63rd\b", "63d"),
        (r"l/71st\b", "71st"),
        (r",t-78th\b", "78th"),
        (r'~-4\'5th\b', "45th"),
        (r'"i 20th\b', "20th"),
        (r"v—-5th\b", "5th"),
        (r"\*-5th\b", "5th"),
        (r"\*\^6th\b", "6th"),
        (r"-s\^\"12th\b", "12th"),
        (r"•!\^14th\b", "14th"),
        (r"zc\s+", ""),
        (r"i-\'i02d\b", "102d"),
        (r"x\'70th\b", "70th"),
        (r"iy69th\b", "69th"),
        (r"£-66th\b", "66th"),
        (r"/\.65th\b", "65th"),
        (r"\\44th\b", "44th"),
        (r";'42d\b", "42d"),
        (r"\^\^f ~\^36th\b", "36th"),
        (r"\^35th\b", "35th"),
        (r"i\^'i7th\b", "17th"),
        (r"t-28th\b", "28th"),
        (r"^-sd\b", "3d"),
        (r"^-th\b", "4th"),
        (r"v-8\"th\b", "8th"),
        (r"i-i0th\b", "10th"),
        (r"\^11 th\b", "11th"),
        (r"t-l0lst\b", "101st"),
        (r"x\*103d\b", "103d"),
        (r"t-£6th\b", "26th"),
        (r"!^29th\b", "29th"),
        (r"kl3th\b", "9th"),
        (r"xi3th\b", "13th"),
        (r"715 th\b", "76th"),
        (r"ktfeth\b", "95th"),
        (r"c%6\b", "4th"),
        (r"tjst yi\b", "13th"),
        (r"k\['6th\b", "16th"),
        (r"->;s-s-2'd\b", "82d"),
        (r"h\^i3th\b", "13th"),
    ):
        cleaned = re.sub(pattern, repl, cleaned, flags=re.I)
    match = re.search(
        r"(\d{1,3})(?:st|nd|rd|th|d)?\s+(infantry|armored|airborne)\s+division",
        cleaned,
        re.I,
    )
    if not match:
        return None
    number = int(match.group(1))
    kind = match.group(2).lower()
    for name in CANONICAL_DIVISIONS:
        if kind not in name.lower():
            continue
        if re.match(rf"^{number}\D", name):
            return name
    return None


def parse_toc_divisions(doc: fitz.Document) -> list[str]:
    toc_text = "\n".join(doc[i].get_text("text") for i in range(14, 18))
    found: list[str] = []
    seen: set[str] = set()
    for line in toc_text.splitlines():
        division = canonical_from_toc_line(line)
        if division and division not in seen:
            seen.add(division)
            found.append(division)
    return found


def scan_pdf_divisions(doc: fitz.Document) -> tuple[set[str], dict[str, list[int]]]:
    divisions: set[str] = set()
    page_ranges: dict[str, list[int]] = defaultdict(list)
    for page_index in range(20, min(570, doc.page_count)):
        text = doc[page_index].get_text("text")
        if "ORGANIC COMPOSITION" in text.upper() and "DIVISIONS" in text.upper():
            break
        lines = [line for _, line in build_page_lines(text)]
        matched = None
        for line in lines[:12]:
            matched = match_division(line)
            if matched:
                break
        if not matched and re.search(r"Black\s+Cat", text, re.I):
            if re.search(r"A\s*I\s*R\s*.*B\s*O\s*R\s*N\s*E", text, re.I):
                matched = "13th Airborne Division"
        if not matched and re.search(r"I\s+N\s+F\s+A\s+N\s+T\s+R\s+Y", text):
            if re.search(r"D\s+I\s+V\s+I\s+S\s+I\s+O\s+N", text):
                nums = re.findall(r"(\d{1,3})\s*\n\s*I\s+N\s+F\s+A\s+N", text)
                if nums:
                    for name in CANONICAL_DIVISIONS:
                        if "Infantry" in name and re.match(rf"^{nums[0]}\D", name):
                            matched = name
                            break
        if matched:
            divisions.add(matched)
            page_ranges[matched].append(page_index + 1)
    return divisions, page_ranges


def load_csv_coverage(root: Path) -> tuple[set[str], dict[str, set[str]], dict[str, dict[str, int]]]:
    all_divisions: set[str] = set()
    presence: dict[str, set[str]] = {key: set() for key in CSV_FILES}
    counts: dict[str, dict[str, int]] = {key: defaultdict(int) for key in CSV_FILES}
    for key, filename in CSV_FILES.items():
        path = root / filename
        with path.open(encoding="utf-8") as handle:
            for row in csv.DictReader(handle):
                division = (row.get("division") or "").strip()
                if not division:
                    continue
                all_divisions.add(division)
                presence[key].add(division)
                counts[key][division] += 1
    return all_divisions, presence, counts


def missing_printed_pages_note(spec: object) -> list[str]:
    notes: list[str] = []
    if not isinstance(spec, dict):
        return notes
    printed_pages = spec.get("printed_pages")
    if isinstance(printed_pages, list) and len(printed_pages) >= 2:
        notes.append(
            f"Printed pp. {printed_pages[0]}–{printed_pages[1]} missing from scan"
        )
    config_note = spec.get("note")
    if isinstance(config_note, str):
        note_text = " ".join(config_note.split())
        if note_text and note_text not in notes:
            notes.append(note_text)
    return notes


def division_notes(
    division: str,
    *,
    in_toc: bool,
    in_pdf: bool,
    missing_scan: set[str],
    missing_scan_details: dict[str, object],
    missing_printed_pages_details: dict[str, object],
    presence: dict[str, set[str]],
    counts: dict[str, dict[str, int]],
) -> list[str]:
    notes: list[str] = []
    if division in missing_scan:
        spec = missing_scan_details.get(division)
        if isinstance(spec, dict):
            printed_pages = spec.get("printed_pages")
            if (
                isinstance(printed_pages, list)
                and len(printed_pages) >= 2
            ):
                notes.append(
                    f"Printed pp. {printed_pages[0]}–{printed_pages[1]} "
                    "missing from scan"
                )
            else:
                notes.append("PDF section missing from scan (TOC only)")
            config_note = spec.get("note")
            if isinstance(config_note, str):
                note_text = " ".join(config_note.split())
                if note_text and note_text not in notes:
                    notes.append(note_text)
        else:
            notes.append("PDF section missing from scan (TOC only)")
        return notes
    notes.extend(
        missing_printed_pages_note(missing_printed_pages_details.get(division))
    )
    if not in_pdf:
        notes.append("No identifiable PDF section header/insignia page")
    if division not in CANONICAL_DIVISIONS:
        notes.append("Non-canonical division label in CSV")
    absent = [SECTION_LABELS[key] for key in CSV_FILES if division not in presence[key]]
    if absent:
        notes.append(f"Absent from: {', '.join(absent)}")
    if counts["attachments"].get(division, 0) == 0:
        notes.append("No attachments recorded")
    if counts["detachments"].get(division, 0) == 0:
        notes.append("No detachments recorded")
    return notes


def build_report(root: Path, config_path: Path, report_path: Path) -> str:
    doc = fitz.open(root / "ETO_Order_of_Battle.pdf")
    toc_divisions = parse_toc_divisions(doc)
    pdf_divisions, pdf_page_ranges = scan_pdf_divisions(doc)
    csv_divisions, presence, counts = load_csv_coverage(root)

    config = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    missing_scan_details: dict[str, object] = config.get("missing_from_scan") or {}
    missing_scan = set(missing_scan_details.keys())
    missing_printed_pages_details: dict[str, object] = (
        config.get("missing_printed_pages") or {}
    )

    toc_set = set(toc_divisions)
    master = sorted(
        set(ETO_VOLUME_DIVISIONS) | csv_divisions | pdf_divisions | toc_set | missing_scan,
        key=sort_division,
    )

    lines: list[str] = [
        "# ETO Order of Battle — Division Coverage Report",
        "",
        "Comparison of extracted CSV coverage against the source PDF (`ETO_Order_of_Battle.pdf`).",
        "",
        "## Summary",
        "",
    ]

    in_csv = {d for d in master if d in csv_divisions}
    in_pdf_or_missing = {d for d in master if d in pdf_divisions or d in missing_scan}
    fully_present = {
        d
        for d in master
        if d in csv_divisions
        and all(d in presence[key] for key in CSV_FILES)
    }

    pdf_divisions_adjusted = pdf_divisions
    toc_missing_csv = sorted(
        (d for d in toc_set | missing_scan if d not in csv_divisions),
        key=sort_division,
    )
    partial_csv = sorted(
        (d for d in in_csv if d not in fully_present),
        key=sort_division,
    )

    lines.extend(
        [
            f"- **ETO volume divisions (canonical, excl. 1st Armored):** {len(ETO_VOLUME_DIVISIONS)}",
            f"- **TOC divisions parsed:** {len(toc_divisions)}",
            f"- **PDF sections identified (headers/insignia):** {len(pdf_divisions_adjusted)}",
            f"- **Unique division labels in CSVs:** {len(csv_divisions)}",
            f"- **Divisions with at least one CSV row:** {len(in_csv)}",
            f"- **Divisions present in all eight CSV types:** {len(fully_present)}",
            f"- **Divisions in TOC but with zero CSV rows:** {len(toc_missing_csv)}",
            f"- **Divisions with partial CSV coverage:** {len(partial_csv)}",
            "",
            "### Key findings",
            "",
            "- **28th Infantry Division** is listed in the TOC (printed pp. 109–119) but the scanned PDF",
            "  jumps from p. 108 to p. 120; no extractable section exists in this file.",
            "- **71st Infantry Division** insignia is at printed p. 224 (pdf 233), but printed",
            "  pp. 225–226 are missing from the scan; attachments resume at pdf 234. Command &",
            "  staff, statistics, campaigns, and organic units for the 71st cannot be extracted.",
            "- **`1do3th Armored Division`** is a spurious CSV label (1 attachment row); the source page",
            "  (pdf 544) is **13th Armored Division** — OCR `103d` → `1do3d`.",
            "- **13th Airborne Division** (Black Cat; TOC printed p. 88) starts at **pdf 108**",
            "  (insignia) through pdf 113. The volume lists no separate 13th Infantry Division.",
            "- Pdf 305–306 headers OCR as `13d Infantry` but are **83d Infantry** command-post",
            "  continuations (page overrides applied).",
            "- **1st Armored Division** is in the canonical list but is Mediterranean-only and not part of",
            "  this ETO volume body.",
            "",
            "### CSV anomalies",
            "",
        ]
    )

    anomalies = sorted(d for d in csv_divisions if d not in CANONICAL_DIVISIONS)
    if anomalies:
        for name in anomalies:
            files = [CSV_FILES[k] for k in CSV_FILES if name in presence[k]]
            lines.append(f"- `{name}` — rows in: {', '.join(files)}")
    else:
        lines.append("- None")

    lines.extend(["", "### Divisions in TOC but absent from all CSVs", ""])
    pdf_not_csv = toc_missing_csv
    if pdf_not_csv:
        for name in pdf_not_csv:
            lines.append(f"- {name}")
    else:
        lines.append("- None")

    lines.extend(["", "## Per-division coverage", ""])
    lines.append(
        "| Division | PDF | TOC | Cmd Staff | Stats | Campaigns | Organic | Attach | Detach | Higher | Cmd Posts | Notes |"
    )
    lines.append(
        "|---|:---:|:---:|---:|---:|---:|---:|---:|---:|---:|---:|---|"
    )

    no_attach: list[str] = []
    no_detach: list[str] = []

    for division in master:
        if division == "1st Armored Division":
            continue
        in_toc = "Y" if division in toc_set else ("—" if division in missing_scan else "N")
        if division in missing_scan:
            in_pdf = "—"
        elif division in pdf_divisions_adjusted:
            in_pdf = "Y"
        else:
            in_pdf = "N"
        cols = [str(counts[key].get(division, 0) or "—") for key in CSV_FILES]
        notes = division_notes(
            division,
            in_toc=division in toc_set,
            in_pdf=division in pdf_divisions,
            missing_scan=missing_scan,
            missing_scan_details=missing_scan_details,
            missing_printed_pages_details=missing_printed_pages_details,
            presence=presence,
            counts=counts,
        )
        if counts["attachments"].get(division, 0) == 0 and division in csv_divisions:
            no_attach.append(division)
        if counts["detachments"].get(division, 0) == 0 and division in csv_divisions:
            no_detach.append(division)
        lines.append(
            f"| {division} | {in_pdf} | {in_toc} | {' | '.join(cols)} | {'; '.join(notes)} |"
        )

    lines.extend(["", "## Divisions with no attachments", ""])
    if no_attach:
        for name in sorted(no_attach, key=sort_division):
            lines.append(f"- {name}")
    else:
        lines.append("- None among divisions present in CSVs")

    lines.extend(["", "## Divisions with no detachments", ""])
    if no_detach:
        for name in sorted(no_detach, key=sort_division):
            lines.append(f"- {name}")
    else:
        lines.append("- None among divisions present in CSVs")

    lines.extend(["", "## Divisions with complete CSV coverage (all 8 files)", ""])
    if fully_present:
        for name in sorted(fully_present, key=sort_division):
            lines.append(f"- {name}")
    else:
        lines.append("- None")

    lines.extend(["", "## PDF page ranges (header/insignia hits)", ""])
    for division in sorted(pdf_page_ranges, key=sort_division):
        pages = pdf_page_ranges[division]
        lines.append(f"- **{division}:** pdf {pages[0]}–{pages[-1]} ({len(pages)} header/insignia hits)")

    lines.extend(["", "## Method", ""])
    lines.extend(
        [
            "- **CSV side:** unique `division` values and row counts per output file.",
            "- **PDF side (independent):** TOC parse from pdf pages 15–18; per-page header OCR",
            "  (`match_division`) and spaced insignia title pages through the division section",
            "  (before ORGANIC COMPOSITION). Does not use CSV carry-forward logic.",
            "- **Missing scan:** `config/eto_oob_pdf_page_division_overrides.yaml`",
            "  `missing_from_scan` and `missing_printed_pages`.",
            "",
        ]
    )

    report = "\n".join(lines) + "\n"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(report, encoding="utf-8")
    return report


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    args = parser.parse_args()
    report = build_report(args.root, args.config, args.report)
    print(report)
    print(f"Wrote {args.report}")


if __name__ == "__main__":
    main()
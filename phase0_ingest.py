"""Phase 0: ingestion normalization — scanned OOB tables -> structured rows.

Runs ahead of Phase 1. For scanned Order-of-Battle sources whose tables live in
Chandra OCR+AI markdown, Phase 0 parses each section (command-staff, campaigns,
command-posts, statistics, organic-units) into structured rows, persists them to
``output/oob/<section>/``, and builds a non-destructive name->PersonID crosswalk.

This is the runnable entry point for the built ``src/ingestion`` front-end; it
executes locally (``python phase0_ingest.py``) and, in AWS, via
``ecs_entrypoint.py phase0_ingest.py`` (which handles S3 download/sync).

Scope note: this increment wires the OOB markdown structured-parsing path (the
fully built + tested capability). Generic media detection / PDF->markdown region
conversion also exist in ``src/ingestion`` and can be added to this orchestrator
as their end-to-end (Chandra OCR) path is wired.

See docs/current/dataquality/INGESTION_FRONT_END.md.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import List, Tuple

from src.ingestion.oob_markdown.run import run_oob_markdown_file
from src.ingestion.pdf_pipeline import (
    STATUS_CONVERTED,
    STATUS_NEEDS_OCR,
    convert_pdf_to_markdown,
)
from src.utils.config import get_paths, load_config
from src.utils.logger import setup_logging

# OOB Chandra markdown lives in an ``ocr_output`` directory within the source.
_OCR_OUTPUT_DIRNAME = "ocr_output"

# Markdown files that are not per-division/section OOB content. ``abbreviations``
# is a reference table. (``eto_order_of_battle`` is handled by content, not name:
# see ``_BOOK_NAMED_STEMS`` — one such file is a real single-division source.)
#
# ``1st_infantry`` is a confirmed misfiling: ``ocr_output/1st_infantry.md``
# contains no 1st Infantry data — its content is 14th Armored Division, byte-for-
# byte equivalent (17 staff rows, 22 valued statistics, identical names) to the
# correctly-named ``ETO_Order_of_Battle_chandra.md/14th_armored.md`` that already
# sources 14th Armored. The real 1st Infantry is recovered from the misnamed
# Chandra ``ETO_Order_of_Battle.md`` (see ``_BOOK_NAMED_STEMS``). Skipping this
# file removes the duplicate without losing any division.
_SKIP_STEMS = frozenset({"00-missing", "abbreviations", "1st_infantry"})

# Per-division OOB files are named for the division and end in its type. The
# aggregate/reference files in the same directory (``preface``, ``tables_of_*``,
# ``summary_of_*``, ``comparitive_statistics_*``, ``european_geographical_*``) are
# NOT per-division and must not be parsed by the per-division section parsers,
# which would otherwise emit thousands of misattributed rows (and, e.g., collide
# a reference table's statistics with a real division's). Their aggregate content
# is valuable but belongs to a dedicated parser, not this discovery path.
_DIVISION_STEM_SUFFIXES = ("infantry", "armored", "airborne")

# Some Chandra ``ETO_Order_of_Battle.md`` files are whole-book dumps or empty
# stubs (skip), but one is a real single-division file misnamed after the book
# (it holds the 1st Infantry Division — the only source for it). Such a
# book-named file is included only when its content actually names a division,
# which distinguishes the real per-division file from the empty stub without
# hard-coding a division. A single division title is required (a true whole-book
# dump would name many and is left to a dedicated parser).
_BOOK_NAMED_STEMS = frozenset({"eto_order_of_battle"})
_DIVISION_TITLE_RE = re.compile(
    r"\b\d{1,3}(?:st|nd|rd|d|th)\s+(?:Infantry|Armored|Airborne)\s+Division\b",
    re.IGNORECASE,
)


def _is_division_markdown(path: Path) -> bool:
    """True if a markdown file is a per-division OOB source worth parsing.

    Per-division files are normally named for the division and end in a division
    type (e.g. ``100th_infantry``, ``10th_armored``, ``82nd_airborne``);
    aggregate/reference files (``00-missing``, ``abbreviations``, ``preface``,
    ``tables_of_*``, ...) are not and are excluded by name.

    A book-named file (``ETO_Order_of_Battle.md``) is a special case: one such
    file is a real single-division source misnamed after the book. It is
    included only when its content names exactly one division (the real file),
    which excludes the empty stub and any multi-division whole-book dump.
    """
    stem = path.stem.lower()
    if stem in _SKIP_STEMS:
        return False
    if stem.endswith(_DIVISION_STEM_SUFFIXES):
        return True
    if stem in _BOOK_NAMED_STEMS:
        return _names_single_division(path)
    return False


def _names_single_division(path: Path) -> bool:
    """True if a file's content names exactly one division (a per-division file).

    Distinguishes a misnamed single-division source (include) from an empty stub
    (zero titles) or a whole-book dump (many titles) without hard-coding which
    division it is.
    """
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    titles = {m.group(0).lower() for m in _DIVISION_TITLE_RE.finditer(text)}
    return len(titles) == 1


def _converge_enabled(config: dict) -> bool:
    """Whether OOB command-staff rows should converge into output/people/.

    Off by default (crosswalk stays a read-only artifact). Enable via
    ``ingestion.converge_people: true`` in config.yaml, or the
    ``OOB_CONVERGE_PEOPLE`` env var (``1``/``true``/``yes``) which overrides.
    """
    env = os.environ.get("OOB_CONVERGE_PEOPLE")
    if env is not None:
        return env.strip().lower() in {"1", "true", "yes"}
    return bool(config.get("ingestion", {}).get("converge_people", False))


def _convert_pdfs_enabled(config: dict) -> bool:
    """Whether Phase 0 should convert source PDFs to markdown first.

    Off by default. Enable via ``ingestion.convert_pdfs: true`` in config.yaml,
    or the ``PHASE0_CONVERT_PDFS`` env var (``1``/``true``/``yes``) which
    overrides. Digital-text PDFs are converted in-process; scanned PDFs are
    detected and deferred to Chandra OCR (no in-process conversion).
    """
    env = os.environ.get("PHASE0_CONVERT_PDFS")
    if env is not None:
        return env.strip().lower() in {"1", "true", "yes"}
    return bool(config.get("ingestion", {}).get("convert_pdfs", False))


def discover_oob_markdown(content_root: Path) -> List[Path]:
    """Find OOB Chandra markdown files under the content repository.

    Discovers per-division/section markdown from the two Chandra OCR output
    layouts, de-duplicated by resolved path:

    * ``*/ocr_output/*.md`` — the original layout (infantry, airborne, and
      low-numbered armored divisions).
    * ``*/*_chandra.md/*.md`` — a second layout whose directory name ends with
      ``_chandra.md`` (the high-numbered armored divisions: 10th-20th). Without
      this, those divisions are never ingested.
    * ``*/*_chandra*/**/*.md`` — the same Chandra family including a sibling
      ``_chandra`` tree (no ``.md`` suffix) with a nested subfolder; one file
      there is the 1st Infantry Division misnamed ``ETO_Order_of_Battle.md``.

    Inclusion is decided by :func:`_is_division_markdown`: per-division files
    (stem ending in a division type) are included by name; aggregate/reference
    files (``preface``, ``tables_of_*``, ``summary_of_*``, the empty book stub)
    are excluded; a book-named file is included only when its content names a
    single division. De-duplicated by resolved path.
    """
    files: List[Path] = []
    seen: set = set()

    def _add_markdown(directory: Path, recurse: bool = False) -> None:
        pattern = "**/*.md" if recurse else "*.md"
        for md in sorted(directory.glob(pattern)):
            if not _is_division_markdown(md):
                continue
            key = md.resolve()
            if key in seen:
                continue
            seen.add(key)
            files.append(md)

    for ocr_dir in content_root.rglob(_OCR_OUTPUT_DIRNAME):
        if ocr_dir.is_dir():
            _add_markdown(ocr_dir)

    # Match the whole Chandra family: ``*_chandra.md`` and ``*_chandra`` dirs,
    # recursing so nested per-division files (e.g. the misnamed 1st Infantry
    # ``ETO_Order_of_Battle.md``) are found. The content-aware filter keeps only
    # real per-division files, so recursion does not admit aggregate noise.
    for chandra_dir in content_root.rglob("*_chandra*"):
        if chandra_dir.is_dir():
            _add_markdown(chandra_dir, recurse=True)

    return sorted(files)


def discover_pdfs(content_root: Path) -> List[Path]:
    """Find source PDFs under the content repository.

    Returns every ``*.pdf`` under ``content_root`` (case-insensitive), excluding
    anything already inside an ``ocr_output`` directory (those are outputs, not
    sources). Used by the optional PDF->markdown pre-step.
    """
    pdfs: List[Path] = []
    seen: set = set()
    for pattern in ("*.pdf", "*.PDF"):
        for pdf in content_root.rglob(pattern):
            if not pdf.is_file():
                continue
            if _OCR_OUTPUT_DIRNAME in pdf.parts:
                continue
            key = pdf.resolve()
            if key in seen:
                continue
            seen.add(key)
            pdfs.append(pdf)
    return sorted(pdfs)


def _convert_source_pdfs(content_root: Path, logger) -> Tuple[int, int, int]:
    """Convert digital source PDFs to markdown in their ``ocr_output/`` dir.

    Scanned PDFs are detected and deferred to Chandra (not converted here).
    Digital PDFs are written to ``<pdf-dir>/ocr_output/<stem>.md`` so the
    existing markdown discovery picks them up unchanged.

    Returns: ``(converted, needs_ocr, other)`` counts.
    """
    pdfs = discover_pdfs(content_root)
    logger.info("[phase0 pre-step] Found %d source PDF(s)", len(pdfs))
    converted = needs_ocr = other = 0
    for pdf in pdfs:
        source_id = pdf.stem
        ocr_dir = pdf.parent / _OCR_OUTPUT_DIRNAME
        markdown_out = ocr_dir / f"{pdf.stem}.md"
        outcome = convert_pdf_to_markdown(pdf, source_id, markdown_out)
        if outcome.status == STATUS_CONVERTED:
            converted += 1
            logger.info("  converted %s -> %s", pdf.name, markdown_out)
        elif outcome.status == STATUS_NEEDS_OCR:
            needs_ocr += 1
            logger.info(
                "  %s: scanned, deferred to Chandra OCR (manifest: %s)",
                pdf.name,
                outcome.manifest_path,
            )
        else:
            other += 1
            logger.info("  %s: %s (%s)", pdf.name, outcome.status, outcome.note)
    logger.info(
        "[phase0 pre-step] PDF routing: %d converted, %d need OCR (Chandra), %d other",
        converted,
        needs_ocr,
        other,
    )
    return converted, needs_ocr, other


def _log_file_summary(index, count, md_path, summary, logger) -> list:
    """Log one file's parse result (rows/review/crosswalk/gaps); return gaps."""
    rows = sum(s["rows"] for s in summary["sections"].values())
    review = sum(s["review"] for s in summary["sections"].values())
    gaps = summary.get("coverage_gaps", [])
    crosswalk = (
        f", crosswalk {summary['crosswalk']['matched']}/"
        f"{summary['crosswalk']['links']} matched"
        if "crosswalk" in summary
        else ""
    )
    gap_note = (
        f", {len(gaps)} coverage gap(s): {', '.join(g['section'] for g in gaps)}"
        if gaps
        else ""
    )
    logger.info(
        "  (%d/%d) %s: %d row(s), %d for review%s%s",
        index,
        count,
        md_path.name,
        rows,
        review,
        crosswalk,
        gap_note,
    )
    return gaps


def _parse_all_sources(
    sources, output_root, people_dir, converge_people, logger
) -> Tuple[dict, List[dict]]:
    """Parse every source file, logging each; return (totals, coverage_report).

    ``totals`` accumulates rows/review/merged/created across files;
    ``coverage_report`` collects per-file section-coverage gaps.
    """
    totals = {"rows": 0, "review": 0, "merged": 0, "created": 0}
    coverage_report: List[dict] = []
    for index, md_path in enumerate(sources, start=1):
        summary = run_oob_markdown_file(
            md_path, output_root, people_dir, converge_people=converge_people
        )
        totals["rows"] += sum(s["rows"] for s in summary["sections"].values())
        totals["review"] += sum(s["review"] for s in summary["sections"].values())
        if "emit" in summary:
            totals["merged"] += summary["emit"]["merged"]
            totals["created"] += summary["emit"]["created"]
        gaps = _log_file_summary(index, len(sources), md_path, summary, logger)
        if gaps:
            coverage_report.append(
                {"source_file": summary["source_file"], "gaps": gaps}
            )
    return totals, coverage_report


def _write_coverage_report(output_root: Path, report: List[dict], logger) -> None:
    """Persist the per-file section-coverage gaps to output/oob/coverage.

    A coverage gap is a section marker present in a source file that produced no
    parsed rows (a silently-skipped region). Writing them makes the gaps a
    reviewable artifact rather than an invisible omission. Nothing is written
    when there are no gaps beyond an empty report (so a clean run is explicit).
    """
    from src.utils.file_lock import write_json_with_lock

    total_gaps = sum(len(entry["gaps"]) for entry in report)
    out_path = output_root / "oob" / "coverage" / "report.json"
    write_json_with_lock(
        out_path,
        {
            "files_with_gaps": len(report),
            "total_gaps": total_gaps,
            "reports": report,
        },
    )
    logger.info(
        "Coverage report: %d file(s) with gaps, %d total gap(s) -> %s",
        len(report),
        total_gaps,
        out_path,
    )


def main() -> None:
    """Main entry point for Phase 0."""
    base_dir = Path(__file__).parent
    config = load_config(base_dir / "config.yaml")
    paths = get_paths(config, base_dir)

    log_config = config.get("logging", {})
    logger = setup_logging(
        level=log_config.get("level", "INFO"),
        log_file=log_config.get("file"),
        console=log_config.get("console", True),
    )

    logger.info("Phase 0: ingestion normalization (OOB markdown -> structured rows)")

    content_root = paths["content_root"]
    output_root = paths["output_root"]
    people_dir = output_root / "people"
    output_root.mkdir(parents=True, exist_ok=True)

    converge_people = _converge_enabled(config)
    if converge_people:
        logger.info(
            "OOB->people convergence ENABLED: command-staff rows will be "
            "emitted/merged into %s for dedup unification",
            people_dir,
        )

    if _convert_pdfs_enabled(config):
        logger.info(
            "[phase0 pre-step] PDF->markdown conversion ENABLED "
            "(digital in-process; scanned deferred to Chandra)"
        )
        _convert_source_pdfs(content_root, logger)

    logger.info("[phase0 step 1/2] Scanning for OOB markdown under %s", content_root)
    sources = discover_oob_markdown(content_root)
    logger.info("Found %d OOB markdown file(s)", len(sources))

    logger.info("[phase0 step 2/2] Parsing %d file(s)", len(sources))
    totals, coverage_report = _parse_all_sources(
        sources, output_root, people_dir, converge_people, logger
    )
    _write_coverage_report(output_root, coverage_report, logger)

    logger.info(
        "Phase 0 complete: %d file(s), %d row(s), %d flagged for review",
        len(sources),
        totals["rows"],
        totals["review"],
    )
    total_merged = totals["merged"]
    total_created = totals["created"]
    if converge_people:
        logger.info(
            "OOB->people convergence: %d merged into existing, %d new people minted "
            "(dedup will unify new ones)",
            total_merged,
            total_created,
        )


if __name__ == "__main__":
    main()

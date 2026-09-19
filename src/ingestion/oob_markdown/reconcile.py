"""Cross-file integrity reconciliation for parsed OOB markdown.

The per-file parsers deliberately never trust a markdown *filename* (OOB
filenames are misaligned with content, e.g. ``101st_airborne.md`` actually holds
``100th Infantry Division`` data). Each parser therefore reads the division from
in-content titles and flags per-file uncertainty. What no single parser can see,
though, is the *cross-file* picture — and that is where the most dangerous silent
errors live:

* **Duplicate content across files** — two source files whose STATISTICS blocks
  are byte-for-byte identical (the misfiled ``101st_airborne.md`` / ``100th``
  case). Attributed to the same division from content, this silently yields
  *two* copies of one division and *zero* rows for the other.
* **Missing divisions** — a source file that produced no rows at all (parser
  skipped every region), so a division silently vanishes from the dataset.
* **Split divisions** — one division's rows scattered across several files,
  which may be legitimate (multi-page) or a symptom of misattribution.

This module runs *after* :mod:`src.ingestion.oob_markdown.run` has persisted the
per-section rows to ``output/oob/<section>/*.json``. It reads those outputs back
and produces findings — it never rewrites rows. Verification, not correction:
the same posture as the parsers (see
docs/current/dataquality/INGESTION_FRONT_END.md, "Scanned documents").
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Tuple

from src.ingestion.oob_markdown._common import UNKNOWN_DIVISION
from src.ingestion.oob_markdown.persist import OOB_OUTPUT_SUBDIR
from src.utils.file_lock import write_json_with_lock

logger = logging.getLogger(__name__)

# Finding severities. ``error`` = almost certainly a data-integrity fault a
# human must resolve; ``warning`` = suspicious, likely-benign, worth a look.
SEVERITY_ERROR = "error"
SEVERITY_WARNING = "warning"

# Finding kinds (stable keys for downstream triage/filtering).
KIND_DUPLICATE_STATISTICS = "duplicate_statistics"
KIND_EMPTY_SOURCE_FILE = "empty_source_file"
KIND_UNKNOWN_DIVISION_ROWS = "unknown_division_rows"
KIND_DIVISION_MULTIPLE_FILES = "division_in_multiple_files"

# The section whose identical block across two files is the strongest duplicate
# signal: a division's STATISTICS (casualties/awards/PWs) is effectively a
# fingerprint — two files sharing it are the misfiled-duplicate case.
_FINGERPRINT_SECTION = "statistics"


@dataclass
class ReconcileFinding:
    """One cross-file integrity finding.

    Attributes:
        kind: Stable finding kind (one of the ``KIND_*`` constants).
        severity: ``error`` or ``warning``.
        message: Human-readable explanation.
        divisions: Division name(s) the finding concerns.
        source_files: Source markdown file name(s) the finding concerns.
        detail: Optional structured extras (e.g. the shared fingerprint).
    """

    kind: str
    severity: str
    message: str
    divisions: List[str] = field(default_factory=list)
    source_files: List[str] = field(default_factory=list)
    detail: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Return a JSON-serializable dict."""
        return asdict(self)


@dataclass
class ReconcileReport:
    """All cross-file findings for one reconciliation run."""

    findings: List[ReconcileFinding] = field(default_factory=list)
    files_scanned: int = 0
    sections_scanned: int = 0

    @property
    def error_count(self) -> int:
        """Number of error-severity findings."""
        return sum(1 for f in self.findings if f.severity == SEVERITY_ERROR)

    @property
    def warning_count(self) -> int:
        """Number of warning-severity findings."""
        return sum(1 for f in self.findings if f.severity == SEVERITY_WARNING)

    def to_dict(self) -> Dict[str, Any]:
        """Return a JSON-serializable dict."""
        return {
            "files_scanned": self.files_scanned,
            "sections_scanned": self.sections_scanned,
            "finding_count": len(self.findings),
            "error_count": self.error_count,
            "warning_count": self.warning_count,
            "findings": [f.to_dict() for f in self.findings],
        }


def _load_section_files(oob_root: Path) -> List[Tuple[str, Path, Dict[str, Any]]]:
    """Return ``(section, path, data)`` for every persisted OOB section file.

    Skips the ``crosswalk`` and ``reconcile`` subdirs (derived artifacts, not
    section rows) and any file that is not the expected ``{rows: [...]}`` shape.
    """
    loaded: List[Tuple[str, Path, Dict[str, Any]]] = []
    if not oob_root.is_dir():
        return loaded
    for section_dir in sorted(p for p in oob_root.iterdir() if p.is_dir()):
        if section_dir.name in {"crosswalk", "reconcile"}:
            continue
        for path in sorted(section_dir.glob("*.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                logger.warning("reconcile: could not read %s: %s", path, exc)
                continue
            if isinstance(data, dict) and isinstance(data.get("rows"), list):
                loaded.append((section_dir.name, path, data))
    return loaded


def _rows_by_source(
    data: Dict[str, Any],
) -> str:
    """Return the source_file recorded in a section file (or its stem)."""
    return str(data.get("source_file") or "")


def _fingerprint_rows(rows: List[Dict[str, Any]]) -> str:
    """Stable hash of a section's rows, independent of the division label.

    Uses the value-bearing fields only (category/metric/value), so two files
    that hold the *same* statistics but were attributed to different divisions
    still collide — that collision is exactly the misfiled-duplicate signal.
    """
    payload = [
        (
            str(r.get("category", "")),
            str(r.get("metric", "")),
            str(r.get("value", "")),
        )
        for r in rows
    ]
    payload.sort()
    digest = hashlib.sha256(
        json.dumps(payload, ensure_ascii=False).encode("utf-8")
    ).hexdigest()
    return digest


def _has_values(rows: List[Dict[str, Any]]) -> bool:
    """True if any row carries a non-empty value.

    An all-empty STATISTICS block is a parse artifact (metric labels recovered
    but values not), not real data. Two files sharing an all-empty block are a
    parser-coverage symptom, not a misfiled division, so duplicate detection
    ignores them to avoid false-positive misfiling errors.
    """
    return any(str(r.get("value", "")).strip() for r in rows)


def _divisions_in(rows: List[Dict[str, Any]]) -> List[str]:
    """Distinct division labels present in a section's rows, in first-seen order."""
    seen: Dict[str, None] = {}
    for row in rows:
        div = str(row.get("division", "")).strip()
        if div and div not in seen:
            seen[div] = None
    return list(seen.keys())


def _check_duplicate_statistics(
    section_files: List[Tuple[str, Path, Dict[str, Any]]],
) -> List[ReconcileFinding]:
    """Flag distinct source files that share an identical STATISTICS block.

    This is the misfiled-duplicate detector: two files whose statistics rows are
    identical almost certainly describe the *same* division under two filenames
    — meaning one real division is missing from the dataset.
    """
    by_fingerprint = _group_statistics_by_fingerprint(section_files)

    findings: List[ReconcileFinding] = []
    for fp, entries in by_fingerprint.items():
        distinct_sources = sorted({src for src, _ in entries if src})
        if len(distinct_sources) < 2:
            continue
        divisions = sorted({d for _, divs in entries for d in divs})
        findings.append(
            ReconcileFinding(
                kind=KIND_DUPLICATE_STATISTICS,
                severity=SEVERITY_ERROR,
                message=(
                    "Identical STATISTICS block found in "
                    f"{len(distinct_sources)} different source files "
                    f"({', '.join(distinct_sources)}). One file is almost "
                    "certainly misfiled — a real division may be missing. "
                    "Confirm which source each division truly belongs to."
                ),
                divisions=divisions,
                source_files=distinct_sources,
                detail={"fingerprint": fp[:16]},
            )
        )
    return findings


def _group_statistics_by_fingerprint(
    section_files: List[Tuple[str, Path, Dict[str, Any]]],
) -> Dict[str, List[Tuple[str, List[str]]]]:
    """Group value-bearing STATISTICS files by their content fingerprint.

    Skips empty/all-empty blocks: a shared all-empty block is a parser artifact,
    not a misfiled division. (Now that table-format stats are parsed, real
    divisions have values and only true duplicates collide.)
    """
    by_fingerprint: Dict[str, List[Tuple[str, List[str]]]] = {}
    for section, _path, data in section_files:
        if section != _FINGERPRINT_SECTION:
            continue
        rows = data["rows"]
        if not rows or not _has_values(rows):
            continue
        fp = _fingerprint_rows(rows)
        src = _rows_by_source(data)
        by_fingerprint.setdefault(fp, []).append((src, _divisions_in(rows)))
    return by_fingerprint


def _check_division_spread(
    section_files: List[Tuple[str, Path, Dict[str, Any]]],
) -> List[ReconcileFinding]:
    """Warn when one division's rows are spread across multiple source files.

    Legitimate for multi-page divisions, but also a misattribution symptom, so
    this is a warning (look), not an error (fix).
    """
    # division -> set of source files that produced rows for it (any section).
    files_for_division: Dict[str, set] = {}
    for _section, _path, data in section_files:
        src = _rows_by_source(data)
        for div in _divisions_in(data["rows"]):
            if div == UNKNOWN_DIVISION:
                continue
            files_for_division.setdefault(div, set()).add(src)

    findings: List[ReconcileFinding] = []
    for div, sources in sorted(files_for_division.items()):
        real_sources = sorted(s for s in sources if s)
        if len(real_sources) < 2:
            continue
        findings.append(
            ReconcileFinding(
                kind=KIND_DIVISION_MULTIPLE_FILES,
                severity=SEVERITY_WARNING,
                message=(
                    f"Division '{div}' has rows from {len(real_sources)} source "
                    f"files ({', '.join(real_sources)}). Expected for a "
                    "multi-page division; verify it is not a misattribution."
                ),
                divisions=[div],
                source_files=real_sources,
            )
        )
    return findings


def _check_empty_and_unknown(
    section_files: List[Tuple[str, Path, Dict[str, Any]]],
) -> List[ReconcileFinding]:
    """Flag files that produced no rows, and rows with an unknown division.

    An empty section file usually means the parser skipped a region whose format
    it did not recognize — a silent gap. Unknown-division rows were captured but
    could not be attributed and need a human to assign the division.
    """
    findings: List[ReconcileFinding] = []
    # Aggregate unknown-division row counts per source file across sections.
    unknown_by_source: Dict[str, int] = {}
    for section, path, data in section_files:
        rows = data["rows"]
        src = _rows_by_source(data) or path.stem
        if not rows:
            findings.append(
                ReconcileFinding(
                    kind=KIND_EMPTY_SOURCE_FILE,
                    severity=SEVERITY_WARNING,
                    message=(
                        f"Section '{section}' file for '{src}' parsed to zero "
                        "rows. If the source has this section, the parser may "
                        "have skipped an unrecognized format."
                    ),
                    source_files=[src],
                    detail={"section": section},
                )
            )
            continue
        unknown = sum(
            1 for r in rows if str(r.get("division", "")).strip() == UNKNOWN_DIVISION
        )
        if unknown:
            unknown_by_source[src] = unknown_by_source.get(src, 0) + unknown

    for src, count in sorted(unknown_by_source.items()):
        findings.append(
            ReconcileFinding(
                kind=KIND_UNKNOWN_DIVISION_ROWS,
                severity=SEVERITY_WARNING,
                message=(
                    f"{count} row(s) from '{src}' have an unknown division "
                    "(no title signal). Assign a division during review."
                ),
                source_files=[src],
                detail={"unknown_row_count": count},
            )
        )
    return findings


def reconcile_oob(output_root: Path) -> ReconcileReport:
    """Run all cross-file integrity checks over persisted OOB section rows.

    Reads ``output/oob/<section>/*.json`` (written by
    :func:`src.ingestion.oob_markdown.run.run_oob_markdown_file`) and returns a
    :class:`ReconcileReport`. Does not modify any rows.

    Args:
        output_root: The pipeline output root (e.g. ``output/``).

    Returns:
        A :class:`ReconcileReport` of findings across all files.
    """
    oob_root = output_root / OOB_OUTPUT_SUBDIR
    section_files = _load_section_files(oob_root)

    report = ReconcileReport(
        files_scanned=len(section_files),
        sections_scanned=len({section for section, _p, _d in section_files}),
    )
    report.findings.extend(_check_duplicate_statistics(section_files))
    report.findings.extend(_check_division_spread(section_files))
    report.findings.extend(_check_empty_and_unknown(section_files))

    logger.info(
        "OOB reconciliation: %d file(s), %d finding(s) (%d error, %d warning)",
        report.files_scanned,
        len(report.findings),
        report.error_count,
        report.warning_count,
    )
    return report


def write_reconcile_report(output_root: Path, report: ReconcileReport) -> Path:
    """Persist a reconciliation report to ``output/oob/reconcile/report.json``.

    Args:
        output_root: The pipeline output root (e.g. ``output/``).
        report: The report to write.

    Returns:
        The path written.
    """
    out_path = output_root / OOB_OUTPUT_SUBDIR / "reconcile" / "report.json"
    write_json_with_lock(out_path, report.to_dict())
    logger.info("Wrote OOB reconciliation report to %s", out_path)
    return out_path

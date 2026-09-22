"""Merge PP-StructureV3 recovered tables back into Chandra markdown.

This closes the loop on table recovery: Chandra flattens complex 2-D task-org
tables into vertical lists with no ``<table>`` markup, so the OOB section
parsers (which key off ``<table>`` via ``iter_section_tables``) drop those
regions entirely (they surface only as coverage gaps). PP-StructureV3 recovers
the real grid as ``<table>`` HTML (``scripts/paddle_recover_page.py`` -> a
``p<N>.recovery.json``). This module splices that recovered table back into the
markdown *in place of* the flattened region, so that by the time Phase 0's OOB
parsers run they see real ``<table>`` markup and parse it normally — no change
to the parsers themselves.

The splice is line-based: ``detect_flattened_tables`` reports each flattened
region as an inclusive ``[start_line, end_line]`` range (the detector works
line-by-line), and this module replaces those lines with the recovered HTML.
Regions are spliced last-to-first so earlier line indices stay valid.

Design posture (matches the rest of the pipeline):
* **Non-destructive by default toward provenance** — the flattened original
  lines are preserved in an HTML comment adjacent to the spliced table (unless
  ``keep_original=False``), so the raw OCR text remains auditable.
* **Verification, not blind trust** — a recovered table carries a marker comment
  noting it came from PP-StructureV3 and is review-flagged upstream, so a human
  can still audit the reconstruction.
* **Safe no-op** — if there are no flattened regions, or no recovered HTML, the
  markdown is returned unchanged.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Sequence

logger = logging.getLogger(__name__)

# Marker wrapping a spliced-in recovered table, so downstream / humans can see
# the region was reconstructed by a second engine (ensemble-as-verification).
_RECOVER_OPEN = "<!-- table recovered by PP-StructureV3 (review) -->"
_RECOVER_CLOSE = "<!-- /recovered table -->"
_ORIGINAL_OPEN = "<!-- original flattened OCR (pre-recovery):"
_ORIGINAL_CLOSE = "-->"


@dataclass(frozen=True)
class MergeRegion:
    """One flattened region to replace with recovered table HTML.

    Attributes:
        start_line: 0-based first line of the flattened region (inclusive).
        end_line: 0-based last line of the flattened region (inclusive).
        recovered_html: The ``<table>...</table>`` HTML to splice in.
    """

    start_line: int
    end_line: int
    recovered_html: str


@dataclass
class MergeResult:
    """Outcome of a merge-back.

    Attributes:
        markdown: The markdown with recovered tables spliced in.
        regions_merged: How many flattened regions were replaced.
        changed: True if any region was replaced.
    """

    markdown: str
    regions_merged: int = 0
    changed: bool = False


def _splice_region(
    lines: List[str], region: MergeRegion, *, keep_original: bool
) -> List[str]:
    """Return ``lines`` with ``region``'s line span replaced by recovered HTML."""
    start = max(0, region.start_line)
    end = min(len(lines) - 1, region.end_line)
    if start > end:
        return lines
    replacement: List[str] = [_RECOVER_OPEN, region.recovered_html.strip()]
    if keep_original:
        original = "\n".join(lines[start : end + 1])
        replacement += [_ORIGINAL_OPEN, original, _ORIGINAL_CLOSE]
    replacement.append(_RECOVER_CLOSE)
    return lines[:start] + replacement + lines[end + 1 :]


def merge_recovered_tables(
    markdown: str,
    regions: Sequence[MergeRegion],
    *,
    keep_original: bool = True,
) -> MergeResult:
    """Splice recovered tables into markdown in place of flattened regions.

    Args:
        markdown: The original Chandra markdown for the page/document.
        regions: Flattened regions to replace, each with recovered ``<table>``
            HTML. Regions with empty ``recovered_html`` are skipped.
        keep_original: When True (default), the replaced flattened lines are
            preserved in an adjacent HTML comment for provenance/audit.

    Returns:
        A :class:`MergeResult`. A no-op (``changed=False``) when there is
        nothing to merge.
    """
    usable = [r for r in regions if r.recovered_html.strip()]
    if not usable:
        return MergeResult(markdown=markdown, regions_merged=0, changed=False)

    lines = markdown.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    # Splice last-to-first so earlier indices remain valid as we mutate.
    merged = 0
    for region in sorted(usable, key=lambda r: r.start_line, reverse=True):
        lines = _splice_region(lines, region, keep_original=keep_original)
        merged += 1
    logger.info("Merged %d recovered table(s) into markdown", merged)
    return MergeResult(markdown="\n".join(lines), regions_merged=merged, changed=True)


def _regions_from_recovery(recovery: dict) -> List[MergeRegion]:
    """Build merge regions from a ``p<N>.recovery.json`` dict.

    The recovery JSON reports N flattened ``flattened_hints`` (each with
    ``start_line``/``end_line``) and a single ``recovered.html`` blob (Paddle
    recovers the page as a whole). The common real case is one flattened table
    per page -> one recovered table -> a clean 1:1 splice. When a page has
    multiple flattened regions, the recovered HTML is spliced at the FIRST
    region and the remaining flattened regions are removed (their content is
    within the single recovered table); this keeps the page's structure without
    duplicating the recovered blob.
    """
    hints = recovery.get("flattened_hints") or []
    recovered = recovery.get("recovered") or {}
    html = (recovered.get("html") or "").strip()
    if not hints or not html:
        return []
    # Order by position; first region gets the recovered HTML, later regions are
    # collapsed to empty (removed) so nothing is duplicated or left flattened.
    ordered = sorted(hints, key=lambda h: h.get("start_line", 0))
    regions: List[MergeRegion] = [
        MergeRegion(
            start_line=int(ordered[0].get("start_line", 0)),
            end_line=int(ordered[0].get("end_line", 0)),
            recovered_html=html,
        )
    ]
    for hint in ordered[1:]:
        regions.append(
            MergeRegion(
                start_line=int(hint.get("start_line", 0)),
                end_line=int(hint.get("end_line", 0)),
                recovered_html="<!-- recovered above -->",
            )
        )
    return regions


def merge_recovery_json(
    markdown: str, recovery: dict, *, keep_original: bool = True
) -> MergeResult:
    """Merge a parsed ``p<N>.recovery.json`` dict into its source markdown."""
    return merge_recovered_tables(
        markdown, _regions_from_recovery(recovery), keep_original=keep_original
    )


def merge_recovery_file(
    markdown_path: Path,
    recovery_path: Path,
    *,
    output_path: Optional[Path] = None,
    keep_original: bool = True,
) -> MergeResult:
    """Merge a recovery JSON file into a markdown file, optionally writing out.

    Args:
        markdown_path: The original Chandra markdown file.
        recovery_path: The ``p<N>.recovery.json`` produced by the recovery worker.
        output_path: When given, the merged markdown is written here (defaults to
            not writing — returns the result only).
        keep_original: Preserve the flattened original in an adjacent comment.

    Returns:
        The :class:`MergeResult`.
    """
    markdown = markdown_path.read_text(encoding="utf-8")
    recovery = json.loads(recovery_path.read_text(encoding="utf-8"))
    result = merge_recovery_json(markdown, recovery, keep_original=keep_original)
    if output_path is not None and result.changed:
        output_path.write_text(result.markdown, encoding="utf-8")
        logger.info("Wrote merged markdown -> %s", output_path)
    return result

"""Run all OOB section parsers over markdown and persist the results.

Thin orchestration layer used by Phase 0 (``phase0_ingest.py``): given one OOB
markdown file, run every section parser, persist each section's rows to
``output/oob/<section>/``, and build the non-destructive command-staff
name->PersonID crosswalk against the people store.

Uses only the built, merged parsers/persist/crosswalk — no new parsing logic.

See docs/current/dataquality/INGESTION_FRONT_END.md.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Callable, Dict, List

from src.ingestion.oob_markdown.campaigns import parse_campaigns
from src.ingestion.oob_markdown.command_posts import parse_command_posts
from src.ingestion.oob_markdown.command_staff import parse_command_staff
from src.ingestion.oob_markdown.crosswalk import build_command_staff_crosswalk
from src.ingestion.oob_markdown.entity_emit import emit_crosswalk_to_people
from src.ingestion.oob_markdown.organic_units import parse_organic_units
from src.ingestion.oob_markdown.persist import persist_parse_result
from src.ingestion.oob_markdown.statistics import parse_statistics
from src.utils.file_lock import write_json_with_lock

logger = logging.getLogger(__name__)

# section key -> parser function (markdown, source_file) -> ParseResult
_SECTION_PARSERS: Dict[str, Callable[[str, str], Any]] = {
    "command_staff": parse_command_staff,
    "campaigns": parse_campaigns,
    "command_posts": parse_command_posts,
    "statistics": parse_statistics,
    "organic_units": parse_organic_units,
}


def run_oob_markdown_file(
    markdown_path: Path,
    output_root: Path,
    people_dir: Path,
    converge_people: bool = False,
) -> Dict[str, Any]:
    """Parse one OOB markdown file across all sections, persist, and crosswalk.

    Args:
        markdown_path: Path to an OOB Chandra markdown file.
        output_root: Pipeline output root (e.g. ``output/``); sections are
            written under ``output/oob/<section>/``.
        people_dir: The people entity dir (e.g. ``output/people/``) the
            command-staff crosswalk resolves names against. May not exist yet.
        converge_people: When True, converge command-staff crosswalk links into
            ``output/people/`` (exact -> merge bio into the resolved person;
            fuzzy/none -> mint a new person for the existing dedup pass to
            unify). When False (default) the crosswalk stays a derived,
            read-only artifact and people files are never modified.

    Returns:
        A summary dict: per-section row/review counts + crosswalk match counts
        (and, when ``converge_people``, ``emit`` merge/create counts).
    """
    markdown = markdown_path.read_text(encoding="utf-8")
    source_file = markdown_path.name
    summary: Dict[str, Any] = {"source_file": source_file, "sections": {}}

    command_staff_rows: List[Any] = []
    for section, parser in _SECTION_PARSERS.items():
        result = parser(markdown, source_file)
        if not result.rows:
            continue
        persist_parse_result(result, section, output_root)
        summary["sections"][section] = {
            "rows": len(result.rows),
            "review": result.review_count,
        }
        if section == "command_staff":
            command_staff_rows = result.rows

    if command_staff_rows:
        crosswalk = build_command_staff_crosswalk(command_staff_rows, people_dir)
        crosswalk_path = (
            output_root / "oob" / "crosswalk" / f"{markdown_path.stem}.json"
        )
        _write_crosswalk(crosswalk_path, crosswalk.to_dict())
        summary["crosswalk"] = {
            "links": len(crosswalk.links),
            "matched": crosswalk.matched_count,
            "review": crosswalk.review_count,
        }
        if converge_people:
            emit = emit_crosswalk_to_people(crosswalk, people_dir)
            summary["emit"] = emit.to_dict()

    return summary


def _write_crosswalk(path: Path, data: Dict[str, Any]) -> None:
    """Persist the crosswalk JSON via the durable writer (metadata-stamped)."""
    write_json_with_lock(path, data)

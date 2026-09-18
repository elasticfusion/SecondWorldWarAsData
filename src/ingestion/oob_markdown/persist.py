"""Persist parsed OOB markdown rows to JSON under ``output/oob/<section>/``.

Part of Piece 2 / C1: the parsed rows are written as their own structured
dataset, kept separate from the narrative-derived entity store
(``output/people/`` etc.). This is deliberately non-destructive — nothing here
touches people/group files. Linkage to ``PersonID`` is a separate, re-runnable
crosswalk (see ``crosswalk``), so a better (fuzzy/verified) matcher can be
applied later without redoing ingestion.

Writes go through ``write_json_with_lock`` for atomic writes + metadata stamping
(``_schema_version``/``_last_updated``). OOB section dirs are not entity dirs, so
that helper's entity validation and DynamoDB dual-write are no-ops for them.

See docs/current/dataquality/INGESTION_FRONT_END.md ("Scanned documents").
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any, Dict, Protocol

from src.utils.file_lock import write_json_with_lock

logger = logging.getLogger(__name__)

# Section name -> output subdirectory under output/oob/.
OOB_OUTPUT_SUBDIR = "oob"


class _ParseResult(Protocol):  # pylint: disable=too-few-public-methods
    """Structural type for a section parse result (has source_file + to_dict)."""

    source_file: str

    def to_dict(self) -> Dict[str, Any]:
        """Return a JSON-serializable dict."""


def _slug(text: str) -> str:
    """Filesystem-safe slug from a source-file name (or 'unknown')."""
    stem = Path(text).stem if text else "unknown"
    slug = re.sub(r"[^A-Za-z0-9]+", "_", stem).strip("_").lower()
    return slug or "unknown"


def persist_parse_result(
    result: _ParseResult,
    section: str,
    output_root: Path,
) -> Path:
    """Write one section parse result to ``output/oob/<section>/<slug>.json``.

    Args:
        result: A parse result exposing ``source_file`` and ``to_dict()``.
        section: Section key (e.g. "command_staff", "campaigns",
            "command_posts") — used as the subdirectory name.
        output_root: The pipeline output root (e.g. ``output/``).

    Returns:
        The path written.
    """
    section_dir = output_root / OOB_OUTPUT_SUBDIR / section
    out_path = section_dir / f"{_slug(result.source_file)}.json"
    write_json_with_lock(out_path, result.to_dict())
    logger.info("Wrote OOB %s rows to %s", section, out_path)
    return out_path

"""Link OOB command-staff rows to ``PersonID`` — non-destructively.

Part of Piece 2 / C1. Builds a **crosswalk**: a derived, re-runnable record of
"this OOB command-staff row -> this PersonID (or none)", computed by matching the
row's name against the existing people store. It never writes to people files,
so a better matcher can be run later over pristine inputs.

Matching is **exact normalized-name only** for now (the safe baseline: no false
merges — e.g. "McLuliffe" will NOT match "McAuliffe"). Each link records a
``match_method`` so a future ``"fuzzy"``/``"verified"`` pass can upgrade
``"none"``/low-confidence links in place without changing this code's contract.

See docs/current/dataquality/INGESTION_FRONT_END.md ("Scanned documents").
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from src.utils.entity_index import build_name_index
from src.utils.text_utils import normalize_name

logger = logging.getLogger(__name__)

MATCH_EXACT = "exact"
MATCH_NONE = "none"


@dataclass
class CrosswalkLink:  # pylint: disable=too-many-instance-attributes
    """One OOB-row -> PersonID link (or a recorded non-match)."""

    name: str
    rank: str
    position: str
    division: str
    source_file: str
    person_id: Optional[str] = None
    matched_name: Optional[str] = None
    match_method: str = MATCH_NONE
    confidence: float = 0.0
    needs_review: bool = True
    notes: str = ""

    def to_dict(self) -> Dict[str, Any]:
        """Return a JSON-serializable dict."""
        return asdict(self)


@dataclass
class CrosswalkResult:
    """All links for a set of command-staff rows."""

    links: List[CrosswalkLink] = field(default_factory=list)

    @property
    def matched_count(self) -> int:
        """Number of rows linked to a PersonID."""
        return sum(1 for link in self.links if link.person_id)

    @property
    def review_count(self) -> int:
        """Number of links flagged for review."""
        return sum(1 for link in self.links if link.needs_review)

    def to_dict(self) -> Dict[str, Any]:
        """Return a JSON-serializable dict."""
        return {
            "link_count": len(self.links),
            "matched_count": self.matched_count,
            "review_count": self.review_count,
            "links": [link.to_dict() for link in self.links],
        }


def _exact_link(row: Any, name_index: Dict[str, str]) -> CrosswalkLink:
    """Build a link for one row by exact normalized-name match."""
    link = CrosswalkLink(
        name=row.name,
        rank=row.rank,
        position=row.position,
        division=row.division,
        source_file=row.source_file,
    )
    key = normalize_name(row.name) if row.name else ""
    person_id = name_index.get(key) if key else None
    if person_id:
        link.person_id = person_id
        link.matched_name = row.name
        link.match_method = MATCH_EXACT
        link.confidence = 0.9
        link.needs_review = False
    else:
        link.match_method = MATCH_NONE
        link.needs_review = True
        link.notes = "no exact name match in people store (fuzzy match pending)"
    return link


def build_command_staff_crosswalk(
    rows: Sequence[Any],
    people_dir: Path,
) -> CrosswalkResult:
    """Build a name->PersonID crosswalk for command-staff rows (exact match).

    Args:
        rows: Command-staff rows (``models.CommandStaffRow``), each with
            ``name``/``rank``/``position``/``division``/``source_file``.
        people_dir: The people entity directory (e.g. ``output/people/``).

    Returns:
        A :class:`CrosswalkResult`. People files are never modified.
    """
    name_index = build_name_index(people_dir, "PersonID", "name")
    result = CrosswalkResult()
    for row in rows:
        result.links.append(_exact_link(row, name_index))
    logger.info(
        "Command-staff crosswalk: %d link(s), %d matched, %d for review",
        len(result.links),
        result.matched_count,
        result.review_count,
    )
    return result

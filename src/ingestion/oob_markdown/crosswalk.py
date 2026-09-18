"""Link OOB command-staff rows to ``PersonID`` — non-destructively.

Part of Piece 2 / C1. Builds a **crosswalk**: a derived, re-runnable record of
"this OOB command-staff row -> this PersonID (or none)", computed by matching the
row's name against the existing people store. It never writes to people files,
so a better matcher can be run later over pristine inputs.

Matching has two tiers (see :mod:`src.ingestion.oob_markdown.name_resolver`):
an **exact** normalized-name match (auto-confirmed) and a last-name-gated
**fuzzy** match (flagged ``needs_review`` so a human confirms it via the
existing dedup review flow). The gate prevents OCR garbles like "McLuliffe"
from resolving to "McAuliffe" while still catching real variants. Each link
records a ``match_method`` (``"exact"``/``"fuzzy"``/``"none"``).

See docs/current/dataquality/INGESTION_FRONT_END.md ("Scanned documents").
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from src.ingestion.oob_markdown.name_resolver import (
    MATCH_EXACT,
    MATCH_FUZZY,
    MATCH_NONE,
    NameResolver,
)
from src.utils.entity_index import build_name_index

logger = logging.getLogger(__name__)

__all__ = [
    "MATCH_EXACT",
    "MATCH_FUZZY",
    "MATCH_NONE",
    "CrosswalkLink",
    "CrosswalkResult",
    "build_command_staff_crosswalk",
]


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


def _link_row(row: Any, resolver: NameResolver) -> CrosswalkLink:
    """Build a link for one row by resolving its name (exact then fuzzy)."""
    link = CrosswalkLink(
        name=row.name,
        rank=row.rank,
        position=row.position,
        division=row.division,
        source_file=row.source_file,
    )
    match = resolver.resolve(row.name or "")
    if match.entity_id and match.method == MATCH_EXACT:
        link.person_id = match.entity_id
        link.matched_name = match.matched_name
        link.match_method = MATCH_EXACT
        link.confidence = 0.9
        link.needs_review = False
    elif match.entity_id and match.method == MATCH_FUZZY:
        link.person_id = match.entity_id
        link.matched_name = match.matched_name
        link.match_method = MATCH_FUZZY
        link.confidence = match.confidence
        link.needs_review = True
        link.notes = (
            f"fuzzy match to '{match.matched_name}' "
            f"(similarity {match.confidence:.0%}) — needs review"
        )
    else:
        link.match_method = MATCH_NONE
        link.needs_review = True
        link.notes = "no exact or fuzzy name match in people store"
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
    resolver = NameResolver(name_index)
    result = CrosswalkResult()
    for row in rows:
        result.links.append(_link_row(row, resolver))
    logger.info(
        "Command-staff crosswalk: %d link(s), %d matched, %d for review",
        len(result.links),
        result.matched_count,
        result.review_count,
    )
    return result

"""Data models for parsed OOB command-and-staff markdown rows.

Verification, not correction: suspect cells are flagged (``needs_review`` +
``confidence`` + ``notes``) rather than rewritten. A confidently-wrong
correction is worse than visible garble for a citable historical reference; see
docs/current/dataquality/INGESTION_FRONT_END.md ("Scanned documents", Piece 2).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List


@dataclass
class CommandStaffRow:  # pylint: disable=too-many-instance-attributes
    """One parsed command-and-staff succession entry.

    Attributes:
        division: Division the row belongs to (tracked from the markdown title/
            inline name lines, NOT the filename, which is unreliable).
        position: Position label as read (e.g. "Comdg Gen", "ACofS G-1").
        effective_date: Effective date string as read (e.g. "15 Sep 1943").
        rank: Rank split from the combined rank+name cell (e.g. "Maj Gen").
        name: Person name split from the combined cell (e.g. "William C Lee").
        acting: True when the entry was marked acting ("(actg)"/"(Actg)").
        confidence: Parse confidence in [0.0, 1.0].
        needs_review: True when a cell looks suspect (empty/garbled/unknown
            rank) and a human should confirm it.
        notes: Free text explaining a review flag or parse note.
        source_file: The markdown file the row came from (provenance).
        raw_cell: The original combined rank+name cell text (provenance; lets a
            reviewer see exactly what was read).
    """

    division: str
    position: str
    effective_date: str
    rank: str
    name: str
    acting: bool = False
    confidence: float = 1.0
    needs_review: bool = False
    notes: str = ""
    source_file: str = ""
    raw_cell: str = ""

    def to_dict(self) -> Dict[str, Any]:
        """Return a JSON-serializable dict."""
        return asdict(self)


@dataclass
class CommandStaffParseResult:
    """Result of parsing one markdown file's command-and-staff sections."""

    source_file: str
    rows: List[CommandStaffRow] = field(default_factory=list)

    @property
    def review_count(self) -> int:
        """Number of rows flagged for review."""
        return sum(1 for r in self.rows if r.needs_review)

    def to_dict(self) -> Dict[str, Any]:
        """Return a JSON-serializable dict."""
        return {
            "source_file": self.source_file,
            "row_count": len(self.rows),
            "review_count": self.review_count,
            "rows": [r.to_dict() for r in self.rows],
        }

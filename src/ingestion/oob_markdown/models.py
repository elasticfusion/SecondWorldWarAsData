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
    division_source: str = "title"  # title | inferred_next_title | unknown

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


@dataclass
class CampaignRow:
    """One campaign a division participated in.

    Attributes:
        division: Division the campaign belongs to (tracked from content).
        campaign: Campaign name as read (e.g. "Normandy", "Rhineland").
        confidence: Parse confidence in [0.0, 1.0].
        needs_review: True when the value looks suspect or the division is
            unknown.
        notes: Free text explaining a review flag.
        source_file: The markdown file the row came from (provenance).
    """

    division: str
    campaign: str
    confidence: float = 1.0
    needs_review: bool = False
    notes: str = ""
    source_file: str = ""
    division_source: str = "title"  # title | inferred_next_title | unknown

    def to_dict(self) -> Dict[str, Any]:
        """Return a JSON-serializable dict."""
        return asdict(self)


@dataclass
class CampaignParseResult:
    """Result of parsing a markdown file's CAMPAIGNS content."""

    source_file: str
    rows: List[CampaignRow] = field(default_factory=list)

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


@dataclass
class CommandPostRow:  # pylint: disable=too-many-instance-attributes
    """One command-post location entry.

    Attributes:
        division: Division the command post belongs to (tracked from content).
        date: Day-month date as read (e.g. "20 Oct"); year is separate.
        year: Year inherited from the most recent underlined year context row.
        town: Town/place name.
        region: Region/administrative area (may be empty in the source).
        country: Country (as read; may be abbreviated, e.g. "Neth").
        confidence: Parse confidence in [0.0, 1.0].
        needs_review: True when a cell looks suspect or the division is unknown.
        notes: Free text explaining a review flag.
        source_file: The markdown file the row came from (provenance).
    """

    division: str
    date: str
    year: str
    town: str
    region: str
    country: str
    confidence: float = 1.0
    needs_review: bool = False
    notes: str = ""
    source_file: str = ""
    division_source: str = "title"  # title | inferred_next_title | unknown

    def to_dict(self) -> Dict[str, Any]:
        """Return a JSON-serializable dict."""
        return asdict(self)


@dataclass
class CommandPostParseResult:
    """Result of parsing a markdown file's COMMAND POSTS tables."""

    source_file: str
    rows: List[CommandPostRow] = field(default_factory=list)

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


@dataclass
class StatisticRow:  # pylint: disable=too-many-instance-attributes
    """One statistic (metric/value) under a category for a division.

    Attributes:
        division: Division the statistic belongs to (tracked from content).
        category: Sub-block category, e.g. "Chronology", "Casualties",
            "Individual Awards".
        metric: Metric name as read (e.g. "Activated", "Killed", "DSC").
        value: Value as read (e.g. "15 Nov 42", "533", "3,667"); kept as text.
        confidence: Parse confidence in [0.0, 1.0].
        needs_review: True when the value looks suspect or the division is
            unknown.
        notes: Free text explaining a review flag.
        source_file: The markdown file the row came from (provenance).
    """

    division: str
    category: str
    metric: str
    value: str
    confidence: float = 1.0
    needs_review: bool = False
    notes: str = ""
    source_file: str = ""
    division_source: str = "title"  # title | inferred_next_title | unknown

    def to_dict(self) -> Dict[str, Any]:
        """Return a JSON-serializable dict."""
        return asdict(self)


@dataclass
class StatisticParseResult:
    """Result of parsing a markdown file's STATISTICS content."""

    source_file: str
    rows: List[StatisticRow] = field(default_factory=list)

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


@dataclass
class OrganicUnitRow:
    """One organic unit of a division.

    Attributes:
        division: Division the unit belongs to (tracked from content).
        unit_name: Unit name as read, with any leading footnote glyph removed.
        notes: Footnote glyph(s) or annotation captured from the cell (e.g. "*").
        confidence: Parse confidence in [0.0, 1.0].
        needs_review: True when the value looks suspect or the division is
            unknown.
        source_file: The markdown file the row came from (provenance).
    """

    division: str
    unit_name: str
    notes: str = ""
    confidence: float = 1.0
    needs_review: bool = False
    source_file: str = ""
    division_source: str = "title"  # title | inferred_next_title | unknown

    def to_dict(self) -> Dict[str, Any]:
        """Return a JSON-serializable dict."""
        return asdict(self)


@dataclass
class OrganicUnitParseResult:
    """Result of parsing a markdown file's ORGANIC UNITS content."""

    source_file: str
    rows: List[OrganicUnitRow] = field(default_factory=list)

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

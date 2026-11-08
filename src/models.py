"""Data models for markdown parsing and entity extraction."""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional


@dataclass
class Paragraph:
    """Represents a single paragraph with absolute numbering."""

    absolute_number: int
    text: str
    page_number: Optional[int] = None
    # The page the paragraph ENDS on, when it runs past page_number (HyperWar paragraphs that
    # cross a printed page are rejoined; a citation of their second half needs this page).
    page_end: Optional[int] = None
    section_id: str = ""
    source_file: str = ""
    # Quotation provenance (set when a block was re-marked as a block quote by
    # the markdown-structure repair, so the parser preserves it rather than
    # flattening it into ordinary prose). Defaults keep existing behavior.
    is_quote: bool = False
    quote_attribution: Optional[str] = None


@dataclass
class Image:
    """Represents an image reference."""

    type: Literal["embedded", "external", "combined"]
    resource_id: Optional[str] = None
    url: Optional[str] = None
    alt_text: str = ""
    caption: Optional[str] = None
    # Paragraph the image FOLLOWS in the source (parser anchor); 0 = unknown / before all text.
    paragraph_number: int = 0
    # The note whose text links the image ("See naval command chart"), 0 = linked from the text.
    # Its paragraph_number is then the paragraph citing that note.
    note_number: int = 0

    def to_dict(self) -> dict:
        """The ONE serialized form of an image (parsed.json, Lambda output)."""
        return {
            "type": self.type,
            "resource_id": self.resource_id,
            "url": self.url,
            "alt_text": self.alt_text,
            "caption": self.caption,
            "paragraph_number": self.paragraph_number,
            "note_number": self.note_number,
        }


@dataclass
class Map:
    """Represents a map reference."""

    url: str
    description: str
    map_id: str
    paragraph_number: int = 0
    # In-document reference footprint (OI3 — provenance paramount): every paragraph that references
    # this map. Each entry: {"paragraph_number": int (MANDATORY), "text": verbatim snippet}.
    # Sub-event association is added downstream (Phase 1 has only paragraphs; sub-events come later).
    references: List[Dict[str, Any]] = field(default_factory=list)


@dataclass
class Footnote:
    """Represents a footnote/endnote reference."""

    number: int
    url: str
    paragraph_number: int = 0
    text: str = (
        ""  # the citation text from the end-of-chapter #cn definition (if present)
    )
    # "footnote" | "endnote" - the SOURCE's own label (its notes-section heading).
    kind: str = "footnote"

    def to_dict(self) -> dict:
        """The ONE serialized form of a note (parsed.json, endnotes.json, Lambda output)."""
        return {
            "number": self.number,
            "url": self.url,
            "text": self.text,
            "kind": self.kind,
            # first paragraph citing this note (0 = not located) - provenance for the citation
            "paragraph_number": self.paragraph_number,
        }


@dataclass
class PageMarker:
    """Represents a page marker in the text."""

    page_number: int
    paragraph_number: int
    marker_type: Literal["anchor", "separator"]


@dataclass
class Metadata:
    """Chapter metadata from -meta.md files."""

    series: str = ""
    book: str = ""
    author: str = ""
    chapter_title: str = ""
    license: str = ""
    copyright_date: str = ""
    source_url: str = ""


@dataclass
class MarkdownDocument:  # pylint: disable=too-many-instance-attributes
    """Complete parsed markdown document."""

    book: str
    chapter_number: int
    chapter_title: str
    section_id: str
    author: str
    series: str
    license: str
    source_url: str = ""

    paragraphs: List[Paragraph] = field(default_factory=list)
    images: List[Image] = field(default_factory=list)
    maps: List[Map] = field(default_factory=list)
    footnotes: List[Footnote] = field(default_factory=list)
    page_markers: List[PageMarker] = field(default_factory=list)
    # Flattened task-org / 2-D table detections from the markdown-structure
    # repair (opt-in). Review-flagged hint trees, not authoritative grids; each
    # dict carries the snapshot->group->units structure + provenance. Empty
    # unless structure repair ran and found a flattened table.
    table_hints: List[dict] = field(default_factory=list)

    file_path: Optional[Path] = None
    meta_path: Optional[Path] = None


@dataclass
class ChapterGroup:
    """Groups all sections of a chapter together."""

    book: str
    chapter_number: int
    meta_file: Path
    content_files: dict[str, Path]  # section_id -> file_path

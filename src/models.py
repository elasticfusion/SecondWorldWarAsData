"""Data models for markdown parsing and entity extraction."""

from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Literal, Optional


@dataclass
class Paragraph:
    """Represents a single paragraph with absolute numbering."""

    absolute_number: int
    text: str
    page_number: Optional[int] = None
    section_id: str = ""
    source_file: str = ""


@dataclass
class Image:
    """Represents an image reference."""

    type: Literal["embedded", "external", "combined"]
    resource_id: Optional[str] = None
    url: Optional[str] = None
    alt_text: str = ""
    caption: Optional[str] = None
    paragraph_number: int = 0


@dataclass
class Map:
    """Represents a map reference."""

    url: str
    description: str
    map_id: str
    paragraph_number: int = 0


@dataclass
class Footnote:
    """Represents a footnote/endnote reference."""

    number: int
    url: str
    paragraph_number: int = 0


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
class MarkdownDocument:
    """Complete parsed markdown document."""

    book: str
    chapter_number: int
    chapter_title: str
    section_id: str
    author: str
    series: str
    license: str

    paragraphs: List[Paragraph] = field(default_factory=list)
    images: List[Image] = field(default_factory=list)
    maps: List[Map] = field(default_factory=list)
    footnotes: List[Footnote] = field(default_factory=list)
    page_markers: List[PageMarker] = field(default_factory=list)

    file_path: Optional[Path] = None
    meta_path: Optional[Path] = None


@dataclass
class ChapterGroup:
    """Groups all sections of a chapter together."""

    book: str
    chapter_number: int
    meta_file: Path
    content_files: dict[str, Path]  # section_id -> file_path

"""Markdown content parsing with entity extraction."""

import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import yaml

from src.models import (
    ChapterGroup,
    Footnote,
    Image,
    Map,
    MarkdownDocument,
    Metadata,
    Paragraph,
)

# Compiled regex patterns for performance
_BLOCKQUOTE_PATTERN = re.compile(r"^>\s*")
_PAGE_MARKER_PATTERN = re.compile(r'<a id="page\d+"></a>')
_FOOTNOTE_PATTERN = re.compile(r"\*\\--\d+--\*")
_SEPARATOR_PATTERN = re.compile(r"\n\* \* \*\n")
_HEADING_PATTERN = re.compile(r"^#{1,6}\s+\*.*\*$")
_CHAPTER_NUM_PATTERN = re.compile(r"chapter(\d+)")


def _parse_yaml_metadata(yaml_file: Path) -> Metadata:
    """Parse metadata from YAML file."""
    with open(yaml_file, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)

    return Metadata(
        series=data.get("series", ""),
        book=data.get("book", ""),
        author=data.get("author", ""),
        chapter_title=data.get("chapter_title", ""),
        license=data.get("license", "Public Domain"),
        copyright_date=data.get("copyright_date", ""),
        source_url=data.get("source_url", ""),
    )


def _parse_legacy_metadata(content: str) -> Metadata:
    """Parse metadata from legacy .md format."""
    lines = [line.strip() for line in content.split("\n") if line.strip()]
    metadata = Metadata()

    if len(lines) >= 1:
        metadata.series = lines[0]
    if len(lines) >= 3:
        metadata.book = lines[2]
    elif len(lines) >= 2:
        metadata.book = lines[1]
    if len(lines) >= 4:
        metadata.author = lines[3]
    if len(lines) >= 5:
        ch = lines[4]
        metadata.chapter_title = ch.split(" - ", 1)[1] if " - " in ch else ch
    if len(lines) >= 6:
        metadata.license = lines[5]

    return metadata


def parse_metadata(meta_file: Path) -> Metadata:
    """Parse metadata from -meta.yaml file (or fallback to .md)."""
    # Try YAML first
    yaml_file = meta_file.with_suffix(".yaml")
    if yaml_file.exists():
        return _parse_yaml_metadata(yaml_file)

    # Fallback to old .md parsing
    content = meta_file.read_text(encoding="utf-8")
    return _parse_legacy_metadata(content)


def extract_page_markers(text: str) -> List[Tuple[int, int, str]]:
    """Extract page markers and their positions."""
    markers: List[Tuple[int, int, str]] = []

    # Find anchor markers: <a id="page4"></a>
    for match in re.finditer(r'<a id="page(\d+)"></a>', text):
        page_num = int(match.group(1))
        markers.append((match.start(), page_num, "anchor"))

    # Find separator markers: *\--3--*
    for match in re.finditer(r"\*\\--(\d+)--\*", text):
        page_num = int(match.group(1))
        markers.append((match.start(), page_num, "separator"))

    return sorted(markers, key=lambda x: x[0])


def extract_images(text: str) -> List[Tuple[str, str, str, str]]:
    """
    Extract images from markdown, avoiding duplicates.

    Returns:
        List of (type, resource_id_or_url, alt_text, external_url)
    """
    images = []
    seen_alts = set()

    # Combined format: [![alt](:/resource-id)](https://url)
    # This is a clickable image with both Joplin resource and external URL
    for match in re.finditer(r"!\[(.*?)\]\(:(.*?)\)\]\((https?://[^\)]+)\)", text):
        alt_text = match.group(1)
        resource_id = match.group(2)
        url = match.group(3)
        if alt_text not in seen_alts:
            images.append(("combined", resource_id, alt_text, url))
            seen_alts.add(alt_text)

    # Embedded images only: ![alt](:/resource-id) - standalone Joplin resource
    # Use negative lookahead to exclude those followed by ](url)
    for match in re.finditer(r"!\[(.*?)\]\(:(.*?)\)(?!\]\()", text):
        alt_text = match.group(1)
        resource_id = match.group(2)
        if alt_text not in seen_alts:
            images.append(("embedded", resource_id, alt_text, ""))
            seen_alts.add(alt_text)

    # External images only: ![alt](https://url) - standalone external image
    # Must NOT be preceded by [![ (which would make it part of combined format)
    for match in re.finditer(r"(?<!\[)!\[(.*?)\]\((https?://[^\)]+)\)(?!\])", text):
        alt_text = match.group(1)
        url = match.group(2)
        if alt_text not in seen_alts:
            images.append(("external", url, alt_text, ""))
            seen_alts.add(alt_text)

    return images


def extract_maps(text: str) -> List[Tuple[str, str]]:
    """
    Extract map references.

    Returns:
        List of (map_id, url)
    """
    maps = []
    seen_urls = set()

    # Pattern 1: [Map X](url) or [Map I](url) - with "Map" in description
    for match in re.finditer(
        r"\[Map\s+([^\]]+)\]\((https?://[^\)]+)\)", text, re.IGNORECASE
    ):
        map_id = match.group(1).strip()
        url = match.group(2).split("%20target=")[0].rstrip()

        if url not in seen_urls:
            maps.append((map_id, url))
            seen_urls.add(url)

    return maps


def extract_footnotes(text: str) -> List[Tuple[int, str]]:
    """
    Extract footnote references.

    Returns:
        List of (number, url)
    """
    footnotes = []

    # Footnotes: <sup>[1](url)</sup> or <sup>[1](url")</sup>
    for match in re.finditer(r'<sup>\[(\d+)\]\((https?://[^\)"]+)', text):
        number = int(match.group(1))
        url = match.group(2)
        footnotes.append((number, url))

    return footnotes


def split_into_paragraphs(text: str) -> List[str]:
    """Split text into paragraphs, preserving all content."""
    # Remove blockquote markers but keep content
    lines = text.split("\n")
    cleaned_lines = []

    for line in lines:
        # Remove blockquote marker
        line = _BLOCKQUOTE_PATTERN.sub("", line)
        cleaned_lines.append(line)

    text = "\n".join(cleaned_lines)

    # Remove page markers but keep surrounding content
    text = _PAGE_MARKER_PATTERN.sub("", text)
    text = _FOOTNOTE_PATTERN.sub("", text)
    text = _SEPARATOR_PATTERN.sub("\n\n", text)

    # Split by double newlines
    blocks = text.split("\n\n")

    paragraphs = []
    for block in blocks:
        block = block.strip()
        if not block:
            continue

        # Skip standalone headings
        if _HEADING_PATTERN.match(block):
            continue

        # Keep everything else including images with captions
        paragraphs.append(block)

    return paragraphs


def _build_page_map(content: str) -> Dict[int, int]:
    """Build map of text position to page number."""
    page_markers_raw = extract_page_markers(content)
    return {pos: page_num for pos, page_num, _ in page_markers_raw}


def _find_page_number(
    para_text: str, content: str, page_map: Dict[int, int]
) -> Optional[int]:
    """Find page number for a paragraph based on its position."""
    para_pos = content.find(para_text[:50])

    current_page = None
    for pos in sorted(page_map.keys()):
        if pos < para_pos:
            current_page = page_map[pos]
        else:
            break

    return current_page


def _create_paragraphs(
    paragraphs_text: List[str],
    start_paragraph_num: int,
    section_id: str,
    file_path: Path,
    content: str,
    page_map: Dict[int, int],
) -> List[Paragraph]:
    """Create paragraph objects with page numbers."""
    paragraphs = []

    for i, para_text in enumerate(paragraphs_text):
        para_num = start_paragraph_num + i
        current_page = _find_page_number(para_text, content, page_map)

        para = Paragraph(
            absolute_number=para_num,
            text=para_text,
            page_number=current_page,
            section_id=section_id,
            source_file=file_path.name,
        )
        paragraphs.append(para)

    return paragraphs


def _add_images_to_doc(doc: MarkdownDocument, content: str) -> None:
    """Extract and add images to document."""
    for img_type, resource_or_url, alt_text, external_url in extract_images(content):
        if img_type == "combined":
            img = Image(
                type="combined",
                resource_id=resource_or_url,
                url=external_url,
                alt_text=alt_text,
                paragraph_number=0,
            )
        elif img_type == "embedded":
            img = Image(
                type="embedded",
                resource_id=resource_or_url,
                url=None,
                alt_text=alt_text,
                paragraph_number=0,
            )
        else:  # external
            img = Image(
                type="external",
                resource_id=None,
                url=resource_or_url,
                alt_text=alt_text,
                paragraph_number=0,
            )
        doc.images.append(img)


def _add_maps_to_doc(doc: MarkdownDocument, content: str) -> None:
    """Extract and add maps to document."""
    for map_id, url in extract_maps(content):
        map_obj = Map(
            url=url, description=f"Map {map_id}", map_id=map_id, paragraph_number=0
        )
        doc.maps.append(map_obj)


def _add_footnotes_to_doc(doc: MarkdownDocument, content: str) -> None:
    """Extract and add footnotes to document."""
    for number, url in extract_footnotes(content):
        footnote = Footnote(number=number, url=url, paragraph_number=0)
        doc.footnotes.append(footnote)


def parse_content_file(
    file_path: Path, section_id: str, start_paragraph_num: int, metadata: Metadata
) -> MarkdownDocument:
    """Parse a single content markdown file."""
    content = file_path.read_text(encoding="utf-8")

    # Extract chapter number from filename
    match = _CHAPTER_NUM_PATTERN.search(file_path.name)
    chapter_num = int(match.group(1)) if match else 0

    # Create document
    doc = MarkdownDocument(
        book=metadata.book,
        chapter_number=chapter_num,
        chapter_title=metadata.chapter_title,
        section_id=section_id,
        author=metadata.author,
        series=metadata.series,
        license=metadata.license,
        file_path=file_path,
    )

    # Build page map and create paragraphs
    page_map = _build_page_map(content)
    paragraphs_text = split_into_paragraphs(content)
    doc.paragraphs = _create_paragraphs(
        paragraphs_text, start_paragraph_num, section_id, file_path, content, page_map
    )

    # Add images, maps, and footnotes
    _add_images_to_doc(doc, content)
    _add_maps_to_doc(doc, content)
    _add_footnotes_to_doc(doc, content)

    return doc


def parse_chapter(chapter_group: ChapterGroup) -> List[MarkdownDocument]:
    """Parse all sections of a chapter with continuous paragraph numbering."""
    metadata = parse_metadata(chapter_group.meta_file)

    documents = []
    current_para_num = 1

    # Sort sections to ensure consistent ordering
    sorted_sections = sorted(chapter_group.content_files.items())

    for section_id, file_path in sorted_sections:
        doc = parse_content_file(file_path, section_id, current_para_num, metadata)
        documents.append(doc)

        # Update paragraph counter for next section
        if doc.paragraphs:
            current_para_num = doc.paragraphs[-1].absolute_number + 1

    return documents

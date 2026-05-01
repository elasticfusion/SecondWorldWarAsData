"""Phase 1: Parse markdown content into structured JSON."""

import json
import re
from pathlib import Path

from src.discovery import discover_content_structure
from src.parser import parse_chapter
from src.utils.config import load_config, get_paths
from src.utils.logger import setup_logging


def _doc_to_dict(doc):
    """Convert a MarkdownDocument to a serializable dict."""
    return {
        "book": doc.book,
        "chapter_number": doc.chapter_number,
        "chapter_title": doc.chapter_title,
        "section_id": doc.section_id,
        "author": doc.author,
        "series": doc.series,
        "license": doc.license,
        "source_file": str(doc.file_path),
        "paragraphs": [
            {
                "absolute_number": p.absolute_number,
                "text": p.text,
                "page_number": p.page_number,
                "section_id": p.section_id,
                "source_file": p.source_file,
            }
            for p in doc.paragraphs
        ],
        "images": [
            {
                "type": img.type,
                "resource_id": img.resource_id,
                "url": img.url,
                "alt_text": img.alt_text,
                "caption": img.caption,
            }
            for img in doc.images
        ],
        "maps": [
            {"url": m.url, "description": m.description, "map_id": m.map_id}
            for m in doc.maps
        ],
        "footnotes": [{"number": f.number, "url": f.url} for f in doc.footnotes],
    }


def _is_footnotes_chapter(doc):
    """Check if a document is a footnotes/endnotes chapter."""
    title_match = any(
        kw in doc.chapter_title.lower() for kw in ["footnote", "endnote", "notes"]
    )
    text_match = doc.paragraphs and any(
        kw in doc.paragraphs[0].text.lower() for kw in ["footnote", "endnote"]
    )
    return title_match or text_match


def _save_footnotes_chapter(doc, book_output, logger):
    """Extract and save footnote/endnote definitions. Returns True if saved."""
    notes_content = []
    for para in doc.paragraphs:
        match = re.match(r"^\[?(\d+)\]?\.?\s+(.+)", para.text)
        if match:
            notes_content.append(
                {"number": int(match.group(1)), "text": match.group(2).strip()}
            )

    if not notes_content:
        return False

    is_endnotes = "endnote" in (
        doc.chapter_title.lower()
        + (doc.paragraphs[0].text.lower() if doc.paragraphs else "")
    )
    file_suffix = "endnotes" if is_endnotes else "footnotes"
    notes_file = book_output / f"chapter{doc.chapter_number}-{file_suffix}.json"
    notes_data = {
        "book": doc.book,
        "chapter_number": doc.chapter_number,
        "chapter_title": doc.chapter_title,
        file_suffix: notes_content,
    }
    with open(notes_file, "w", encoding="utf-8") as f:
        json.dump(notes_data, f, indent=2, ensure_ascii=False)
    logger.info(
        f"  Saved: {notes_file.name} ({len(notes_content)} {file_suffix} with content)"
    )
    return True


def _save_split_chapter(doc, book_output, logger):
    """Split a large chapter into chunks and save. Returns True if split."""
    total_chars = sum(len(p.text) for p in doc.paragraphs)
    if total_chars <= 400000:
        return False

    logger.warning(
        f"  Large chapter: {total_chars:,} chars ({len(doc.paragraphs)} paragraphs)"
    )
    chunk_size = 50
    for i in range(0, len(doc.paragraphs), chunk_size):
        chunk_paras = doc.paragraphs[i : i + chunk_size]
        chunk_suffix = chr(97 + i // chunk_size)
        chunk_file = (
            book_output / f"chapter{doc.chapter_number}{chunk_suffix}-parsed.json"
        )
        chunk_data = _doc_to_dict(doc)
        chunk_data["section_id"] = f"{doc.section_id or 'full'}-{chunk_suffix}"
        chunk_data["paragraphs"] = [
            {
                "absolute_number": p.absolute_number,
                "text": p.text,
                "page_number": p.page_number,
                "section_id": p.section_id,
                "source_file": p.source_file,
            }
            for p in chunk_paras
        ]
        with open(chunk_file, "w", encoding="utf-8") as f:
            json.dump(chunk_data, f, indent=2, ensure_ascii=False)
        logger.info(
            f"  Saved: {chunk_file.name} ({len(chunk_paras)} paragraphs, chunk {i // chunk_size + 1})"
        )
    return True


def _save_endnote_refs(doc, section_suffix, book_output, logger):
    """Save endnote/footnote references from main content."""
    if not doc.footnotes:
        return
    has_urls = any(f.url for f in doc.footnotes)
    if not has_urls:
        return
    endnotes_file = (
        book_output / f"chapter{doc.chapter_number}{section_suffix}-endnotes.json"
    )
    endnotes_data = {
        "book": doc.book,
        "chapter_number": doc.chapter_number,
        "chapter_title": doc.chapter_title,
        "endnotes": [{"number": f.number, "url": f.url} for f in doc.footnotes],
    }
    with open(endnotes_file, "w", encoding="utf-8") as f:
        json.dump(endnotes_data, f, indent=2, ensure_ascii=False)
    logger.info(f"  Saved: {endnotes_file.name} ({len(doc.footnotes)} endnotes)")


def _process_document(doc, book_output, logger):
    """Process a single parsed document — save as JSON, handling special cases."""
    section_suffix = doc.section_id if doc.section_id else "full"

    if _is_footnotes_chapter(doc):
        _save_footnotes_chapter(doc, book_output, logger)
        return

    if _save_split_chapter(doc, book_output, logger):
        return

    output_file = (
        book_output / f"chapter{doc.chapter_number}{section_suffix}-parsed.json"
    )
    output_data = _doc_to_dict(doc)
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(output_data, f, indent=2, ensure_ascii=False)
    logger.info(f"  Saved: {output_file.name} ({len(doc.paragraphs)} paragraphs)")

    _save_endnote_refs(doc, section_suffix, book_output, logger)


def main():
    """Main entry point for Phase 1."""
    base_dir = Path(__file__).parent
    config = load_config(base_dir / "config.yaml")
    paths = get_paths(config, base_dir)

    log_config = config.get("logging", {})
    logger = setup_logging(
        level=log_config.get("level", "INFO"),
        log_file=log_config.get("file"),
        console=log_config.get("console", True),
    )

    logger.info("Starting Phase 1: File Discovery and Parsing")

    content_root = paths["content_root"]
    logger.info(f"Scanning content directory: {content_root}")

    structure = discover_content_structure(content_root)

    logger.info(f"Found {len(structure)} book(s)")
    for book_name, chapters in structure.items():
        logger.info(f"  {book_name}: {len(chapters)} chapter(s)")
        for chapter in chapters:
            sections = ", ".join(chapter.content_files.keys()) or "single file"
            logger.info(f"    Chapter {chapter.chapter_number}: sections [{sections}]")

    output_root = paths["output_root"]
    output_root.mkdir(exist_ok=True)

    for book_name, chapters in structure.items():
        book_output = output_root / book_name
        book_output.mkdir(exist_ok=True)

        for chapter_group in chapters:
            logger.info(f"Parsing {book_name} - Chapter {chapter_group.chapter_number}")
            documents = parse_chapter(chapter_group)
            for doc in documents:
                _process_document(doc, book_output, logger)

    logger.info("Phase 1 complete!")


if __name__ == "__main__":
    main()

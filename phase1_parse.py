#!/usr/bin/env python3
"""
Phase 1: File Discovery and Markdown Parsing
"""

import logging
from pathlib import Path
import json

from src.utils.config import load_config, get_paths
from src.utils.logger import setup_logging
from src.discovery import discover_content_structure
from src.parser import parse_chapter


def main():
    # Load configuration
    base_dir = Path(__file__).parent
    config = load_config(base_dir / "config.yaml")
    paths = get_paths(config, base_dir)

    # Setup logging
    log_config = config.get("logging", {})
    logger = setup_logging(
        level=log_config.get("level", "INFO"),
        log_file=log_config.get("file"),
        console=log_config.get("console", True),
    )

    logger.info("Starting Phase 1: File Discovery and Parsing")

    # Discover content structure
    content_root = paths["content_root"]
    logger.info(f"Scanning content directory: {content_root}")

    structure = discover_content_structure(content_root)

    logger.info(f"Found {len(structure)} book(s)")
    for book_name, chapters in structure.items():
        logger.info(f"  {book_name}: {len(chapters)} chapter(s)")
        for chapter in chapters:
            sections = ", ".join(chapter.content_files.keys()) or "single file"
            logger.info(f"    Chapter {chapter.chapter_number}: sections [{sections}]")

    # Parse all chapters
    output_root = paths["output_root"]
    output_root.mkdir(exist_ok=True)

    for book_name, chapters in structure.items():
        book_output = output_root / book_name
        book_output.mkdir(exist_ok=True)

        for chapter_group in chapters:
            logger.info(f"Parsing {book_name} - Chapter {chapter_group.chapter_number}")

            documents = parse_chapter(chapter_group)

            # Save parsed documents
            for doc in documents:
                section_suffix = doc.section_id if doc.section_id else "full"
                
                # Check if this is a footnotes/endnotes chapter
                # Check: 1) chapter title, 2) first paragraph text
                is_footnotes_chapter = (
                    any(keyword in doc.chapter_title.lower() for keyword in ['footnote', 'endnote', 'notes'])
                    or (doc.paragraphs and any(keyword in doc.paragraphs[0].text.lower() 
                                               for keyword in ['footnote', 'endnote']))
                )
                
                if is_footnotes_chapter:
                    # Extract footnote/endnote definitions - don't create parsed file
                    import re
                    notes_content = []
                    for para in doc.paragraphs:
                        # Match pattern like "[1] citation text" or "1. citation text"
                        match = re.match(r'^\[?(\d+)\]?\.?\s+(.+)', para.text)
                        if match:
                            notes_content.append({
                                "number": int(match.group(1)),
                                "text": match.group(2).strip()
                            })
                    
                    if notes_content:
                        # Determine if footnotes or endnotes based on title/content
                        is_endnotes = 'endnote' in (doc.chapter_title.lower() + (doc.paragraphs[0].text.lower() if doc.paragraphs else ''))
                        file_suffix = 'endnotes' if is_endnotes else 'footnotes'
                        notes_file = book_output / f"chapter{doc.chapter_number}-{file_suffix}.json"
                        notes_data = {
                            "book": doc.book,
                            "chapter_number": doc.chapter_number,
                            "chapter_title": doc.chapter_title,
                            file_suffix: notes_content
                        }
                        with open(notes_file, "w", encoding="utf-8") as f:
                            json.dump(notes_data, f, indent=2, ensure_ascii=False)
                        logger.info(f"  Saved: {notes_file.name} ({len(notes_content)} {file_suffix} with content)")
                    
                    # Skip creating parsed file for footnotes chapter
                    continue
                
                # Create parsed file for regular chapters
                output_file = (
                    book_output / f"chapter{doc.chapter_number}{section_suffix}-parsed.json"
                )

                # Check chapter size and split if needed (>400K chars to leave headroom)
                total_chars = sum(len(p.text) for p in doc.paragraphs)
                if total_chars > 400000:
                    logger.warning(
                        f"  Large chapter detected: {total_chars:,} chars "
                        f"({len(doc.paragraphs)} paragraphs)"
                    )
                    logger.warning(f"  Splitting into smaller sections...")
                    
                    # Split by paragraph count to keep related content together
                    # Aim for ~50 paragraphs per section
                    chunk_size = 50
                    for i in range(0, len(doc.paragraphs), chunk_size):
                        chunk_paras = doc.paragraphs[i:i + chunk_size]
                        chunk_suffix = chr(97 + i // chunk_size)  # a, b, c, etc.
                        chunk_file = (
                            book_output / 
                            f"chapter{doc.chapter_number}{chunk_suffix}-parsed.json"
                        )
                        
                        chunk_data = {
                            "book": doc.book,
                            "chapter_number": doc.chapter_number,
                            "chapter_title": doc.chapter_title,
                            "section_id": f"{doc.section_id or 'full'}-{chunk_suffix}",
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
                                for p in chunk_paras
                            ],
                            "images": doc.images,  # Include all images in each chunk
                            "maps": doc.maps,
                            "footnotes": doc.footnotes,
                        }
                        
                        with open(chunk_file, "w", encoding="utf-8") as f:
                            json.dump(chunk_data, f, indent=2, ensure_ascii=False)
                        
                        logger.info(
                            f"  Saved: {chunk_file.name} "
                            f"({len(chunk_paras)} paragraphs, chunk {i//chunk_size + 1})"
                        )
                    
                    # Skip creating the full file
                    continue

                # Convert to dict for JSON serialization
                output_data = {
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

                with open(output_file, "w", encoding="utf-8") as f:
                    json.dump(output_data, f, indent=2, ensure_ascii=False)

                logger.info(f"  Saved: {output_file.name} ({len(doc.paragraphs)} paragraphs)")
                
                # Detect and save footnote/endnote references from main content
                # Endnotes: Have URLs (external links)
                # Footnotes: Inline references without URLs
                if doc.footnotes:
                    has_urls = any(f.url for f in doc.footnotes)
                    
                    if has_urls:
                        # These are endnotes (linked references)
                        endnotes_file = book_output / f"chapter{doc.chapter_number}{section_suffix}-endnotes.json"
                        endnotes_data = {
                            "book": doc.book,
                            "chapter_number": doc.chapter_number,
                            "chapter_title": doc.chapter_title,
                            "endnotes": [{"number": f.number, "url": f.url} for f in doc.footnotes]
                        }
                        with open(endnotes_file, "w", encoding="utf-8") as f:
                            json.dump(endnotes_data, f, indent=2, ensure_ascii=False)
                        logger.info(f"  Saved: {endnotes_file.name} ({len(doc.footnotes)} endnotes)")
                    else:
                        # These are inline footnotes (no URLs)
                        footnotes_file = book_output / f"chapter{doc.chapter_number}{section_suffix}-footnotes.json"
                        footnotes_data = {
                            "book": doc.book,
                            "chapter_number": doc.chapter_number,
                            "chapter_title": doc.chapter_title,
                            "footnotes": [{"number": f.number} for f in doc.footnotes]
                        }
                        with open(footnotes_file, "w", encoding="utf-8") as f:
                            json.dump(footnotes_data, f, indent=2, ensure_ascii=False)
                        logger.info(f"  Saved: {footnotes_file.name} ({len(doc.footnotes)} footnotes)")

    logger.info("Phase 1 complete!")


if __name__ == "__main__":
    main()

"""File discovery for nested markdown content structure."""

import re
from pathlib import Path
from typing import Dict, List

from src.models import ChapterGroup


def discover_content_structure(content_root: Path) -> Dict[str, List[ChapterGroup]]:
    """
    Discover all books, chapters, and sections in the content repository.

    Returns:
        Dict mapping book names to lists of ChapterGroups
    """
    structure = {}
    pdf_files_found = []

    for book_dir in content_root.iterdir():
        if not book_dir.is_dir() or book_dir.name.startswith("."):
            continue

        book_name = book_dir.name
        chapters = []

        # Check for PDF files in book directory (excluding sourcedocument)
        for pdf_file in book_dir.rglob("*.pdf"):
            if "sourcedocument" not in pdf_file.parts:
                pdf_files_found.append(pdf_file)

        for chapter_dir in book_dir.iterdir():
            if not chapter_dir.is_dir() or chapter_dir.name.startswith("."):
                continue

            # Skip sourcedocument directory
            if chapter_dir.name == "sourcedocument":
                continue

            # Extract chapter number (digits or Roman numerals)
            match = re.match(
                r"chapter(\d+|[IVXLCDM]+)", chapter_dir.name, re.IGNORECASE
            )
            if not match:
                continue

            chapter_num = match.group(1)

            # Find meta file (YAML or legacy .md)
            meta_files = list(chapter_dir.glob("*-meta.yaml"))
            if not meta_files:
                meta_files = list(chapter_dir.glob("*-meta.md"))
            if not meta_files:
                continue

            meta_file = meta_files[0]

            # Find content files
            content_files = {}

            # Check for subsections (a, b, c, d)
            for section_letter in ["a", "b", "c", "d", "e", "f"]:
                pattern = f"chapter{chapter_num}{section_letter}-content.md"
                matches = list(chapter_dir.glob(pattern))
                if matches:
                    content_files[section_letter] = matches[0]

            # Check for single content file (no subsections)
            if not content_files:
                pattern = f"chapter{chapter_num}-content.md"
                matches = list(chapter_dir.glob(pattern))
                if matches:
                    content_files[""] = matches[0]

            if content_files:
                # Convert chapter_num to int, handling Roman numerals
                if isinstance(chapter_num, int):
                    chapter_int = chapter_num
                elif isinstance(chapter_num, str) and chapter_num.isdigit():
                    chapter_int = int(chapter_num)
                else:
                    chapter_int = 0  # Default for Roman numerals or unknown

                chapters.append(
                    ChapterGroup(
                        book=book_name,
                        chapter_number=chapter_int,
                        meta_file=meta_file,
                        content_files=content_files,
                    )
                )

        if chapters:
            structure[book_name] = sorted(chapters, key=lambda c: c.chapter_number)

    # Warn about PDF files
    if pdf_files_found:
        print("\n⚠️  PDF files detected in contentrepository:")
        for pdf_file in pdf_files_found:
            print(f"   {pdf_file.relative_to(content_root)}")
        print("\n💡 Convert PDFs to markdown first:")
        print("   python3 scripts/pdf_to_markdown.py <pdf_file> <book_name>")
        print("   See docs/current/PDF_CONVERSION.md for details\n")

    return structure

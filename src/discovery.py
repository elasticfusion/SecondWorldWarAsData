"""File discovery for nested markdown content structure."""

import re
from pathlib import Path
from typing import Dict, List, Optional

from src.models import ChapterGroup


def _find_pdf_files(book_dir: Path) -> List[Path]:
    """Find PDF files in book directory (excluding sourcedocument)."""
    return [pdf for pdf in book_dir.rglob("*.pdf") if "sourcedocument" not in pdf.parts]


def _extract_chapter_number(chapter_dir_name: str) -> Optional[str]:
    """Extract chapter number from directory name."""
    match = re.match(r"chapter(\d+|[IVXLCDM]+)", chapter_dir_name, re.IGNORECASE)
    return match.group(1) if match else None


def _find_meta_file(chapter_dir: Path) -> Optional[Path]:
    """Find meta file (YAML or legacy .md)."""
    meta_files = list(chapter_dir.glob("*-meta.yaml"))
    if not meta_files:
        meta_files = list(chapter_dir.glob("*-meta.md"))
    return meta_files[0] if meta_files else None


def _find_content_files(chapter_dir: Path, chapter_num: str) -> Dict[str, Path]:
    """Find content files (subsections or single file)."""
    content_files = {}

    # Check for subsections (a, b, c, d, e, f)
    for section_letter in ["a", "b", "c", "d", "e", "f"]:
        pattern = f"chapter{chapter_num}{section_letter}-content.md"
        matches = list(chapter_dir.glob(pattern))
        if matches:
            content_files[section_letter] = matches[0]

    # Check for single content file if no subsections
    if not content_files:
        pattern = f"chapter{chapter_num}-content.md"
        matches = list(chapter_dir.glob(pattern))
        if matches:
            content_files[""] = matches[0]

    return content_files


def _convert_chapter_number(chapter_num: str) -> int:
    """Convert chapter number to int, handling Roman numerals."""
    if isinstance(chapter_num, int):
        return chapter_num
    if isinstance(chapter_num, str) and chapter_num.isdigit():
        return int(chapter_num)
    return 0  # Default for Roman numerals or unknown


def _warn_about_pdfs(pdf_files: List[Path], content_root: Path) -> None:
    """Warn user about PDF files that need conversion."""
    if not pdf_files:
        return

    print("\n⚠️  PDF files detected in contentrepository:")
    for pdf_file in pdf_files:
        print(f"   {pdf_file.relative_to(content_root)}")
    print("\n💡 Convert PDFs to markdown first:")
    print("   python3 scripts/pdf_to_markdown.py <pdf_file> <book_name>")
    print("   See docs/current/PDF_CONVERSION.md for details\n")


def _process_chapter_dir(chapter_dir: Path, book_name: str) -> Optional[ChapterGroup]:
    """Process a single chapter directory."""
    # Extract chapter number
    chapter_num = _extract_chapter_number(chapter_dir.name)
    if not chapter_num:
        return None

    # Find meta file
    meta_file = _find_meta_file(chapter_dir)
    if not meta_file:
        return None

    # Find content files
    content_files = _find_content_files(chapter_dir, chapter_num)
    if not content_files:
        return None

    # Create chapter group
    return ChapterGroup(
        book=book_name,
        chapter_number=_convert_chapter_number(chapter_num),
        meta_file=meta_file,
        content_files=content_files,
    )


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

        # Check for PDF files
        pdf_files_found.extend(_find_pdf_files(book_dir))

        # Process each chapter directory
        for chapter_dir in book_dir.iterdir():
            if not chapter_dir.is_dir() or chapter_dir.name.startswith("."):
                continue
            if chapter_dir.name == "sourcedocument":
                continue

            chapter = _process_chapter_dir(chapter_dir, book_name)
            if chapter:
                chapters.append(chapter)

        if chapters:
            structure[book_name] = sorted(chapters, key=lambda c: c.chapter_number)

    _warn_about_pdfs(pdf_files_found, content_root)
    return structure

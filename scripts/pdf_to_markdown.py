#!/usr/bin/env python3
"""Convert PDF to markdown for pipeline processing."""

import sys
from pathlib import Path

try:
    import pymupdf4llm
except ImportError:
    print("Error: pymupdf4llm not installed")
    print("Install with: pip install pymupdf4llm")
    sys.exit(1)


def pdf_to_markdown(pdf_path: Path, output_dir: Path, book_name: str) -> None:
    """Convert PDF to markdown structure for pipeline."""

    # Create output structure
    chapter_dir = output_dir / book_name / "chapter1"
    chapter_dir.mkdir(parents=True, exist_ok=True)

    # Create sourcedocument directory
    source_dir = output_dir / book_name / "sourcedocument"
    source_dir.mkdir(parents=True, exist_ok=True)

    # Extract markdown from PDF with structure preservation
    print(f"Extracting structured markdown from {pdf_path.name}...")
    markdown_text = pymupdf4llm.to_markdown(str(pdf_path))

    # Write content file
    content_file = chapter_dir / "chapter1-content.md"
    with open(content_file, "w", encoding="utf-8") as f:
        f.write(markdown_text)

    # Create metadata template
    meta_file = chapter_dir / "chapter1-meta.yaml"
    with open(meta_file, "w", encoding="utf-8") as f:
        f.write(f"""series: "TODO - Add series name"
book: "{book_name}"
author: "TODO - Add author name"
chapter_number: "1"
chapter_title: "TODO - Add chapter/paper title"
license: "TODO - Add license (e.g., Public Domain, CC-BY-4.0)"
copyright_date: "TODO - Add year"
source_url: "TODO - Add source URL or DOI"
""")

    # Move PDF to sourcedocument directory
    import shutil

    dest_pdf = source_dir / pdf_path.name
    shutil.move(str(pdf_path), str(dest_pdf))

    # Count pages
    import fitz

    doc = fitz.open(str(dest_pdf))
    page_count = len(doc)
    doc.close()

    print(f"\n✅ Converted {pdf_path.name}")
    print(f"   Pages extracted: {page_count}")
    print(f"   Output directory: {chapter_dir}")
    print(f"   PDF moved to: {dest_pdf}")
    print(f"\n📝 Next steps:")
    print(f"   1. Edit metadata: {meta_file}")
    print(f"   2. Review/clean content: {content_file}")
    print(f"   3. Split into chapters if needed")
    print(f"   4. Run pipeline: python3 phase1_parse.py")


def main():
    """Main entry point."""
    if len(sys.argv) != 3:
        print("Usage: python3 pdf_to_markdown.py <pdf_file> <book_name>")
        print("\nExample:")
        print("  python3 scripts/pdf_to_markdown.py paper.pdf 'SmithPaper2024'")
        sys.exit(1)

    pdf_path = Path(sys.argv[1])
    book_name = sys.argv[2]
    output_dir = Path("contentrepository")

    # Validate input
    if not pdf_path.exists():
        print(f"Error: PDF file not found: {pdf_path}")
        sys.exit(1)

    if not pdf_path.suffix.lower() == ".pdf":
        print(f"Error: File must be a PDF: {pdf_path}")
        sys.exit(1)

    # Convert
    try:
        pdf_to_markdown(pdf_path, output_dir, book_name)
    except Exception as e:
        print(f"\n❌ Error during conversion: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()

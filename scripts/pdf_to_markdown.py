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


def _write_metadata_marker(output_dir: Path, book_name: str, pdf_path: Path) -> None:
    """Write a local needs-review/metadata marker recording the deferred-metadata
    gap (empty author/series/title/license), so the gap is DETECTABLE rather than
    masqueraded as real TODO strings (P2-C1). Fail-safe: never crashes the convert."""
    import json

    try:
        marker_dir = output_dir / "needs-review" / "metadata"
        marker_dir.mkdir(parents=True, exist_ok=True)
        marker = marker_dir / f"{book_name}.json"
        marker.write_text(
            json.dumps(
                {
                    "source_key": str(pdf_path),
                    "book": book_name,
                    "reason": "metadata not yet extracted "
                    "(author/series/title/license empty) — real metadata "
                    "extraction deferred (P2-C1 slug+meta half)",
                    "category": "metadata",
                    "disposition": "rejected-metadata",
                }
            ),
            encoding="utf-8",
        )
    except Exception as e:  # noqa: BLE001 - marker must never crash the convert
        print(f"⚠ Could not write metadata needs-review marker: {e}")


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

    # Create metadata template. P2-C1: emit SAFE YAML via yaml.safe_dump (never an
    # f-string) so a book_name with YAML metacharacters cannot inject structure.
    # author/series/chapter_title/license are '' (empty) NOT a literal
    # 'TODO - Add ...' that masquerades as real data — real author/title extraction
    # is deferred. A sibling needs-review metadata marker records the gap so it is
    # DETECTABLE (this is a local CLI script with no S3/reject_to_review; the marker
    # is the local-filesystem equivalent of reject_to_review(category='metadata')).
    import yaml

    meta_file = chapter_dir / "chapter1-meta.yaml"
    meta = {
        "series": "",
        "book": book_name,
        "author": "",
        "chapter_number": "1",
        "chapter_title": "",
        "license": "",
        "copyright_date": "",
        "source_url": "",
    }
    with open(meta_file, "w", encoding="utf-8") as f:
        yaml.safe_dump(meta, f, default_flow_style=False, allow_unicode=True)

    # needs-review/metadata marker (detectable gap, not masqueraded) — the local
    # analogue of reject_to_review(category='metadata').
    _write_metadata_marker(output_dir, book_name, pdf_path)

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

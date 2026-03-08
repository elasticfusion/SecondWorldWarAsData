#!/usr/bin/env python3
"""
Generate missing metadata YAML files for all chapters.
"""

from pathlib import Path

import yaml


def find_chapters_without_metadata(content_dir: Path):
    """Find all chapter directories missing metadata files."""
    missing = []

    for book_dir in content_dir.iterdir():
        if not book_dir.is_dir() or book_dir.name.startswith("."):
            continue

        for chapter_dir in book_dir.iterdir():
            if not chapter_dir.is_dir() or chapter_dir.name.startswith("."):
                continue

            # Check for metadata files
            has_yaml = list(chapter_dir.glob("*-meta.yaml"))
            has_md = list(chapter_dir.glob("*-meta.md"))

            if not has_yaml and not has_md:
                missing.append(chapter_dir)

    return missing


def create_metadata_template(chapter_dir: Path, book_name: str):
    """Create a metadata template for a chapter."""
    chapter_name = chapter_dir.name

    # Determine metadata based on book
    if "Breakout" in book_name or "breakout" in book_name.lower():
        author = "Martin Blumenson"
        copyright_date = "1961"
        book_title = "Breakout and Pursuit"
    elif "Cross-Channel" in book_name or "cross" in book_name.lower():
        author = "Gordon A. Harrison"
        copyright_date = "1951"
        book_title = "Cross-Channel Attack"
    else:
        author = "[AUTHOR NEEDED]"
        copyright_date = "[YEAR NEEDED]"
        book_title = book_name

    metadata = {
        "series": "United States Army in World War II",
        "book": book_title,
        "author": author,
        "chapter_number": "[CHAPTER NUMBER]",
        "chapter_title": "[CHAPTER TITLE]",
        "license": "Public Domain",
        "copyright_date": copyright_date,
        "source_url": "[SOURCE URL IF AVAILABLE]",
    }

    # Create metadata file (don't overwrite if exists)
    meta_file = chapter_dir / f"{chapter_name}-meta.yaml"
    
    if meta_file.exists():
        print(f"  ⊘ Skipped: {meta_file.name} already exists")
        return None
    
    with open(meta_file, "w", encoding="utf-8") as f:
        yaml.dump(metadata, f, default_flow_style=False, sort_keys=False)

    return meta_file


def main():
    """Main entry point."""
    content_dir = Path("contentrepository")

    if not content_dir.exists():
        print(f"Content directory not found: {content_dir}")
        return 1

    # Find missing metadata
    missing = find_chapters_without_metadata(content_dir)

    if not missing:
        print("✓ All chapters have metadata files!")
        return 0

    print(f"Found {len(missing)} chapter(s) without metadata:\n")

    for chapter_dir in missing:
        book_name = chapter_dir.parent.name
        print(f"Creating: {book_name}/{chapter_dir.name}")

        meta_file = create_metadata_template(chapter_dir, book_name)
        if meta_file:
            print(f"  ✓ Created: {meta_file.name}\n")
        else:
            print()  # Just newline if skipped

    print(f"\n✓ Created {len(missing)} metadata template(s)")
    print("\nNext steps:")
    print("1. Review and complete the metadata files")
    print("2. Fill in [CHAPTER NUMBER], [CHAPTER TITLE], etc.")
    print("3. Run phase1_parse.py")

    return 0


if __name__ == "__main__":
    import sys

    sys.exit(main())

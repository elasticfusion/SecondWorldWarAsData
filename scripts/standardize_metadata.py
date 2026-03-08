#!/usr/bin/env python3
"""
Standardize chapter metadata files to YAML format for better parsing.
"""

import re
from pathlib import Path

import yaml


def parse_existing_meta(meta_file: Path) -> dict:
    """Parse existing meta file and extract what we can."""
    content = meta_file.read_text(encoding="utf-8")
    lines = [line.strip() for line in content.split("\n") if line.strip()]

    metadata = {
        "series": None,
        "book": None,
        "author": None,
        "chapter_number": None,
        "chapter_title": None,
        "license": None,
        "copyright_date": None,
        "source_url": None,
    }

    for line in lines:
        # Extract source URL
        if line.startswith("Source:"):
            metadata["source_url"] = line.replace("Source:", "").strip()

        # Extract book
        elif line.startswith("Book:"):
            metadata["book"] = line.replace("Book:", "").strip()

        # Extract chapter
        elif line.startswith("Chapter:"):
            chapter_text = line.replace("Chapter:", "").strip()
            # Remove markdown formatting
            chapter_text = re.sub(r"[>#\[\]']", "", chapter_text).strip()
            metadata["chapter_title"] = chapter_text

        # Extract license
        elif line.startswith("License:"):
            metadata["license"] = line.replace("License:", "").strip()
        
        # Extract copyright date
        elif line.startswith("Copyright:") or "©" in line:
            # Extract year from copyright line
            copyright_text = line.replace("Copyright:", "").strip()
            year_match = re.search(r"\b(19\d{2}|20\d{2})\b", copyright_text)
            if year_match:
                metadata["copyright_date"] = year_match.group(1)

        # Detect series
        elif "United States Army in World War II" in line:
            metadata["series"] = line

        # Detect theater/book continuation
        elif "European Theater" in line or "Theater of Operations" in line:
            pass  # Skip theater lines

        # Detect book title (common patterns)
        elif any(
            book in line
            for book in [
                "Breakout and Pursuit",
                "Cross-Channel Attack",
                "Cross-Channel-Attack",
            ]
        ):
            if not metadata["book"]:
                metadata["book"] = line

        # Detect author
        elif re.match(r"^[A-Z][a-z]+ [A-Z]\. [A-Z][a-z]+$", line):
            # Pattern: "Martin Blumenson" or "Gordon A. Harrison"
            metadata["author"] = line

        # Detect chapter with number
        elif re.match(r"^Chapter [IVX\d]+ - ", line):
            parts = line.split(" - ", 1)
            if len(parts) == 2:
                metadata["chapter_title"] = parts[1]
                # Extract chapter number
                chapter_num = parts[0].replace("Chapter", "").strip()
                metadata["chapter_number"] = chapter_num

    return metadata


def create_yaml_meta(metadata: dict, output_file: Path):
    """Create standardized YAML metadata file."""
    # Clean up metadata
    clean_meta = {k: v for k, v in metadata.items() if v}

    # Add defaults
    if not clean_meta.get("license"):
        clean_meta["license"] = "Public Domain"

    if not clean_meta.get("series"):
        clean_meta["series"] = "United States Army in World War II"

    yaml_content = yaml.dump(clean_meta, default_flow_style=False, sort_keys=False)

    output_file.write_text(yaml_content, encoding="utf-8")


def main():
    """Main entry point."""
    content_dir = Path("contentrepository")

    meta_files = list(content_dir.glob("**/*-meta.md"))
    print(f"Found {len(meta_files)} metadata files\n")

    for meta_file in meta_files:
        print(f"Processing: {meta_file.relative_to(content_dir)}")

        # Parse existing
        metadata = parse_existing_meta(meta_file)

        # Show what was extracted
        print(f"  Book: {metadata.get('book', 'NOT FOUND')}")
        print(f"  Author: {metadata.get('author', 'NOT FOUND')}")
        print(f"  Chapter: {metadata.get('chapter_title', 'NOT FOUND')}")
        print(f"  License: {metadata.get('license', 'NOT FOUND')}")

        # Create YAML version
        yaml_file = meta_file.with_suffix(".yaml")
        create_yaml_meta(metadata, yaml_file)
        print(f"  ✓ Created: {yaml_file.name}\n")

    print(f"\n✓ Created {len(meta_files)} YAML metadata files")
    print("\nNext steps:")
    print("1. Review the .yaml files")
    print("2. Update src/parser.py to read YAML instead of .md")
    print("3. Delete old .md files once confirmed")


if __name__ == "__main__":
    main()

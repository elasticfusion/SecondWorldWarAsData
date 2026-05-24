#!/usr/bin/env python3
"""Split a single markdown file into multiple chapter sections based on headings."""

import re
import sys
from pathlib import Path


def split_by_headings(content: str, min_heading_level: int = 1):
    """Split content by markdown headings."""
    # Find all headings with their positions
    heading_pattern = rf"^(#{{{min_heading_level},{min_heading_level}}}\s+.+)$"
    matches = list(re.finditer(heading_pattern, content, re.MULTILINE))

    if not matches:
        print(f"No level-{min_heading_level} headings found. Try a different level.")
        return []

    sections = []
    for i, match in enumerate(matches):
        start = match.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(content)

        heading = match.group(1)
        section_content = content[start:end].strip()

        # Extract heading text (remove # symbols)
        heading_text = re.sub(r"^#+\s+", "", heading).strip()

        sections.append({"heading": heading_text, "content": section_content})

    return sections


def main():
    if len(sys.argv) < 2:
        print("Usage: python3 split_chapters.py <markdown_file> [heading_level]")
        print("\nExample:")
        print("  python3 scripts/split_chapters.py path/to/chapter1-content.md 1")
        print("\nHeading levels:")
        print("  1 = # Heading (major sections)")
        print("  2 = ## Heading (subsections)")
        sys.exit(1)

    md_file = Path(sys.argv[1])
    heading_level = int(sys.argv[2]) if len(sys.argv) > 2 else 1

    if not md_file.exists():
        print(f"Error: File not found: {md_file}")
        sys.exit(1)

    # Read content
    with open(md_file, "r", encoding="utf-8") as f:
        content = f.read()

    # Split by headings
    sections = split_by_headings(content, heading_level)

    if not sections:
        sys.exit(1)

    print(f"\nFound {len(sections)} section(s):\n")
    for i, section in enumerate(sections, 1):
        print(f"{i}. {section['heading']}")

    # Ask user which sections to keep
    print("\nEnter section numbers to save (comma-separated, or 'all'):")
    print("Example: 1,3,5 or all")
    choice = input("> ").strip().lower()

    if choice == "all":
        selected = list(range(len(sections)))
    else:
        try:
            selected = [int(x.strip()) - 1 for x in choice.split(",")]
        except ValueError:
            print("Invalid input")
            sys.exit(1)

    # Save selected sections
    chapter_dir = md_file.parent
    base_name = md_file.stem.replace("-content", "")

    for idx in selected:
        if 0 <= idx < len(sections):
            section = sections[idx]
            letter = chr(ord("a") + idx)
            output_file = chapter_dir / f"{base_name}{letter}-content.md"

            with open(output_file, "w", encoding="utf-8") as f:
                f.write(section["content"])

            print(f"✓ Saved: {output_file.name} - {section['heading']}")

    print(f"\n✅ Split complete! Original file unchanged.")
    print(f"   Delete {md_file.name} if no longer needed.")


if __name__ == "__main__":
    main()

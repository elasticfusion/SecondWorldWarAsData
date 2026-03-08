#!/usr/bin/env python3
"""
Use Grok to fill in missing metadata fields from chapter content.
"""

import json
from pathlib import Path

import yaml

from src.grok_client import GrokClient


def is_metadata_incomplete(metadata: dict) -> bool:
    """Check if metadata has placeholder values or missing fields."""
    placeholders = [
        "[CHAPTER NUMBER]",
        "[CHAPTER TITLE]",
        "[AUTHOR NEEDED]",
        "[YEAR NEEDED]",
        "[SOURCE URL IF AVAILABLE]",
    ]

    for value in metadata.values():
        if isinstance(value, str) and any(p in value for p in placeholders):
            return True

    # Check for missing critical fields
    if not metadata.get("chapter_title") or not metadata.get("chapter_number"):
        return True

    return False


def extract_metadata_with_grok(
    chapter_dir: Path, existing_metadata: dict, grok: GrokClient
) -> dict:
    """Use Grok to extract missing metadata from chapter content."""
    # Make a copy to avoid modifying the original
    metadata = existing_metadata.copy()
    
    # Find content files
    content_files = list(chapter_dir.glob("*-content.md"))

    if not content_files:
        print("  ⚠ No content files found")
        return metadata

    # Read first content file (usually has chapter heading)
    content = content_files[0].read_text(encoding="utf-8")

    # Limit to first 2000 chars (chapter heading area)
    content_sample = content[:2000]

    prompt = f"""Extract chapter metadata from this WWII history book content.

Current metadata (may have placeholders):
{json.dumps(metadata, indent=2)}

Content sample:
{content_sample}

Extract:
1. Chapter number (Roman numerals or Arabic)
2. Chapter title (the main heading)

Return ONLY valid JSON:
{{
  "chapter_number": "I" or "1" etc,
  "chapter_title": "The Allies" etc
}}

If you cannot find a field, return null for that field."""

    response = grok.chat_completion(
        prompt, temperature=0.1, cache_type="metadata_extraction"
    )

    try:
        extracted = json.loads(response)

        # Update only if we found something and existing is placeholder
        if extracted.get("chapter_number"):
            if not metadata.get(
                "chapter_number"
            ) or "[CHAPTER NUMBER]" in metadata.get("chapter_number", ""):
                metadata["chapter_number"] = extracted["chapter_number"]

        if extracted.get("chapter_title"):
            if not metadata.get(
                "chapter_title"
            ) or "[CHAPTER TITLE]" in metadata.get("chapter_title", ""):
                metadata["chapter_title"] = extracted["chapter_title"]

        return metadata

    except json.JSONDecodeError as e:
        print(f"  ⚠ Failed to parse Grok response: {e}")
        return existing_metadata


def main():
    """Main entry point."""
    content_dir = Path("contentrepository")
    grok = GrokClient(Path("cache/api"))

    # Find all metadata files
    meta_files = list(content_dir.glob("**/*-meta.yaml"))

    if not meta_files:
        print("No metadata files found. Run generate_missing_metadata.py first.")
        return 1

    print(f"Checking {len(meta_files)} metadata file(s) for incomplete data...\n")

    updated_count = 0

    for meta_file in meta_files:
        # Load existing metadata
        with open(meta_file, "r", encoding="utf-8") as f:
            metadata = yaml.safe_load(f)

        # Check if incomplete
        if not is_metadata_incomplete(metadata):
            continue

        print(f"Processing: {meta_file.relative_to(content_dir)}")
        print(f"  Current chapter: {metadata.get('chapter_title', 'MISSING')}")

        # Extract with Grok
        updated_metadata = extract_metadata_with_grok(meta_file.parent, metadata, grok)

        # Save if changed
        if updated_metadata != metadata:
            with open(meta_file, "w", encoding="utf-8") as f:
                yaml.dump(
                    updated_metadata, f, default_flow_style=False, sort_keys=False
                )

            print(f"  ✓ Updated chapter: {updated_metadata.get('chapter_title')}")
            updated_count += 1
        else:
            print("  ⊘ No changes")

        print()

    if updated_count > 0:
        print(f"\n✓ Updated {updated_count} metadata file(s)")
    else:
        print("\n✓ All metadata files are complete!")

    return 0


if __name__ == "__main__":
    import sys

    sys.exit(main())

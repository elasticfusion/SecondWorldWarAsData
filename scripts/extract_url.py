#!/usr/bin/env python3
"""
Optional: Extract content from URL and convert to chapter structure
"""

import logging
import sys
from pathlib import Path
import click

from src.utils.logger import setup_logging
from src.url_extractor import URLExtractor


@click.command()
@click.option("--url", required=True, help="Source URL to extract")
@click.option("--book-name", required=True, help="Name for book directory")
@click.option("--output-dir", default="contentrepository", help="Output directory")
@click.option("--content-selector", default=None, help="CSS selector for main content")
@click.option(
    "--chapter-pattern", default=None, help="Regex pattern for chapter headings"
)
def main(
    url: str,
    book_name: str,
    output_dir: str,
    content_selector: str,
    chapter_pattern: str,
):
    """Extract content from URL and save as chapter structure."""

    # Setup logging
    logger = setup_logging(level="INFO", console=True)

    logger.info("URL Content Extraction")
    logger.info(f"URL: {url}")
    logger.info(f"Book: {book_name}")

    # Initialize extractor
    output_path = Path(output_dir)
    extractor = URLExtractor(output_path)

    try:
        # Extract and save
        saved_files = extractor.extract_from_url(
            url=url,
            book_name=book_name,
            content_selector=content_selector,
            chapter_pattern=chapter_pattern,
        )

        logger.info(f"\n✓ Extracted {len(saved_files)} file(s)")
        logger.info(f"✓ Saved to: {output_path / book_name}")
        logger.info("\nNext steps:")
        logger.info("  1. Review extracted chapters")
        logger.info("  2. Run: python3 phase1_parse.py")

    except Exception as e:
        logger.error(f"Extraction failed: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()

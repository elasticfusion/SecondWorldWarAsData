#!/usr/bin/env python3
"""Clean up low-value cache entries for completed books.

Keeps the expensive 'events' cache and removes everything else for a book
that has been fully processed. Works with both local filesystem and S3/DynamoDB.

Usage:
    python3 scripts/cleanup_book_cache.py BookName [--dry-run]
    python3 scripts/cleanup_book_cache.py --all [--dry-run]
"""

import logging
import sys
from pathlib import Path

from src.utils.backends import create_cache_backend
from src.utils.config import load_config

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)

# Cache types to KEEP (expensive to regenerate)
KEEP_TYPES = {"events"}

# Cache types to DELETE after a complete run
PURGE_TYPES = {
    "dates",
    "places",
    "people",
    "peoplegroups",
    "weather",
    "equipment",
    "logistics",
    "casualties",
    "supplemental",
    "supplemental_narrative",
    "supplemental_search",
    "supplemental_advanced",
}


def _find_completed_books(config: dict) -> list[str]:
    """Find books that have event files in output/."""
    output_root = Path(config.get("paths", {}).get("output_root", "output"))
    content_dir = output_root / "content"
    search_dir = content_dir if content_dir.exists() else output_root
    books = []
    for d in sorted(search_dir.iterdir()):
        if d.is_dir() and list(d.glob("*-event.json")):
            books.append(d.name)
    return books


def cleanup_book_cache(book_name: str, config: dict, dry_run: bool = False) -> dict:
    """Purge low-value cache for one book. Returns {kept, deleted} counts."""
    cache_backend = create_cache_backend(
        config, Path(config.get("paths", {}).get("api_cache", "cache/api"))
    )

    kept = 0
    deleted = 0

    for cache_type in PURGE_TYPES:
        sub = cache_backend.get_sub_cache(f"books/{book_name}/{cache_type}")
        try:
            if dry_run:
                logger.info("  would purge: books/%s/%s", book_name, cache_type)
            else:
                sub.clear()
                logger.info("  purged: books/%s/%s", book_name, cache_type)
            deleted += 1
        except Exception as e:
            logger.warning("  skip: books/%s/%s (%s)", book_name, cache_type, e)

    for cache_type in KEEP_TYPES:
        logger.info("  kept: books/%s/%s", book_name, cache_type)
        kept += 1

    return {"kept": kept, "deleted": deleted}


def main():
    dry_run = "--dry-run" in sys.argv
    all_books = "--all" in sys.argv
    args = [a for a in sys.argv[1:] if not a.startswith("--")]

    config = load_config()

    if all_books:
        books = _find_completed_books(config)
        if not books:
            print("No completed books found in output/")
            return
        print(f"Found {len(books)} completed book(s)")
    elif args:
        books = args
    else:
        print("Usage: python3 scripts/cleanup_book_cache.py BookName [--dry-run]")
        print("       python3 scripts/cleanup_book_cache.py --all [--dry-run]")
        sys.exit(1)

    total_deleted = 0
    for book in books:
        prefix = "Would clean" if dry_run else "Cleaning"
        print(f"\n{prefix}: {book}")
        result = cleanup_book_cache(book, config, dry_run)
        total_deleted += result["deleted"]

    action = "Would purge" if dry_run else "Purged"
    print(f"\n{action} {total_deleted} cache type(s), kept {len(KEEP_TYPES)} per book")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Migrate book directories from output/{Book}/ to output/content/{Book}/.

Moves parsed/event files into the new content/ subdirectory while leaving
entity directories (people, places, etc.) in place.

Usage:
    python3 scripts/migrate_output_content.py [--dry-run]
"""

import argparse
import shutil
import sys
from pathlib import Path

ENTITY_DIRS = frozenset(
    [
        "dates",
        "places",
        "people",
        "people_groups",
        "equipment",
        "casualties",
        "weather",
        "logistics",
        "maps",
        "maps_images",
        "external_maps",
        "bibliography",
        "supplemental",
        "images",
        "content",
        "metrics",
        "dedup",
    ]
)


def find_book_dirs(output_root: Path) -> list[Path]:
    """Find directories that are books (not entity dirs)."""
    books = []
    for d in sorted(output_root.iterdir()):
        if d.is_dir() and d.name not in ENTITY_DIRS and not d.name.startswith("."):
            # Confirm it has parsed or event files
            if list(d.glob("*-parsed.json")) or list(d.glob("*-event.json")):
                books.append(d)
    return books


def _is_already_migrated(content_dir: Path, book_dirs: list[Path]) -> bool:
    """Check if all books already exist in content/."""
    if not content_dir.exists() or not list(content_dir.iterdir()):
        return False
    existing = {d.name for d in content_dir.iterdir() if d.is_dir()}
    return {d.name for d in book_dirs}.issubset(existing)


def migrate(output_root: Path, dry_run: bool = False) -> int:
    """Move book dirs under output/content/. Returns count of dirs moved."""
    content_dir = output_root / "content"
    book_dirs = find_book_dirs(output_root)

    if not book_dirs:
        print("No book directories found to migrate.")
        return 0

    if _is_already_migrated(content_dir, book_dirs):
        print("Already migrated — all books exist in output/content/.")
        return 0

    print(f"Found {len(book_dirs)} book dir(s) to migrate:")
    for d in book_dirs:
        print(f"  {d.name}/ ({len(list(d.glob('*.json')))} files)")

    if dry_run:
        print("\n[DRY RUN] No changes made.")
        return len(book_dirs)

    content_dir.mkdir(exist_ok=True)
    moved = 0
    for book_dir in book_dirs:
        dest = content_dir / book_dir.name
        if dest.exists():
            print(f"  SKIP {book_dir.name}/ — already exists in content/")
            continue
        shutil.move(str(book_dir), str(dest))
        print(f"  MOVED {book_dir.name}/ → content/{book_dir.name}/")
        moved += 1

    print(f"\nMigrated {moved} book dir(s) to output/content/.")
    return moved


def main():
    parser = argparse.ArgumentParser(description="Migrate book dirs to output/content/")
    parser.add_argument("--dry-run", action="store_true", help="Preview without moving")
    parser.add_argument(
        "--output-root",
        type=Path,
        default=Path("output"),
        help="Output root directory (default: output)",
    )
    args = parser.parse_args()

    if not args.output_root.exists():
        print(f"Output root not found: {args.output_root}")
        sys.exit(1)

    migrate(args.output_root, dry_run=args.dry_run)


if __name__ == "__main__":
    main()

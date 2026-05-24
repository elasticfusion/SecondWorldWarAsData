#!/usr/bin/env python3
"""
Command-line tool to review API response caches.
"""

import argparse
import json
from pathlib import Path
from datetime import datetime


def list_caches(cache_dir: Path):
    """List all cache types and counts."""
    print(f"\nCache Directory: {cache_dir}\n")
    print(f"{'Type':<15} {'Count':<10} {'Size':<15}")
    print("-" * 40)

    total_files = 0
    total_size = 0

    for cache_type in sorted(cache_dir.iterdir()):
        if cache_type.is_dir():
            files = list(cache_type.glob("*.json"))
            count = len(files)
            size = sum(f.stat().st_size for f in files)
            total_files += count
            total_size += size

            size_str = f"{size / 1024:.1f} KB" if size > 0 else "0 KB"
            print(f"{cache_type.name:<15} {count:<10} {size_str:<15}")

    print("-" * 40)
    total_size_str = f"{total_size / 1024:.1f} KB"
    print(f"{'TOTAL':<15} {total_files:<10} {total_size_str:<15}\n")


def show_cache(cache_dir: Path, cache_type: str, limit: int = 10):
    """Show cache entries for a specific type."""
    type_dir = cache_dir / cache_type

    if not type_dir.exists():
        print(f"Error: Cache type '{cache_type}' not found")
        return

    files = sorted(
        type_dir.glob("*.json"), key=lambda f: f.stat().st_mtime, reverse=True
    )

    if not files:
        print(f"No cache entries for type: {cache_type}")
        return

    print(f"\nCache Type: {cache_type}")
    print(f"Total Entries: {len(files)}\n")

    for i, cache_file in enumerate(files[:limit], 1):
        mtime = datetime.fromtimestamp(cache_file.stat().st_mtime)
        size = cache_file.stat().st_size

        print(f"{i}. {cache_file.name}")
        print(f"   Modified: {mtime.strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"   Size: {size / 1024:.1f} KB")
        print()


def view_entry(cache_dir: Path, cache_type: str, cache_key: str):
    """View a specific cache entry."""
    cache_file = cache_dir / cache_type / f"{cache_key}.json"

    if not cache_file.exists():
        print(f"Error: Cache entry not found: {cache_file}")
        return

    with open(cache_file, "r", encoding="utf-8") as f:
        data = json.load(f)

    print(f"\nCache Entry: {cache_type}/{cache_key}")
    print(f"File: {cache_file}")
    print(f"Size: {cache_file.stat().st_size / 1024:.1f} KB")
    print("\nContent:")
    print(json.dumps(data, indent=2, ensure_ascii=False))


def clear_cache(cache_dir: Path, cache_type: str = None):
    """Clear cache entries."""
    if cache_type:
        type_dir = cache_dir / cache_type
        if not type_dir.exists():
            print(f"Error: Cache type '{cache_type}' not found")
            return

        files = list(type_dir.glob("*.json"))
        for f in files:
            f.unlink()
        print(f"Cleared {len(files)} entries from {cache_type}")
    else:
        total = 0
        for type_dir in cache_dir.iterdir():
            if type_dir.is_dir():
                files = list(type_dir.glob("*.json"))
                for f in files:
                    f.unlink()
                total += len(files)
        print(f"Cleared {total} total cache entries")


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Review and manage API response caches",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s list                          # List all cache types
  %(prog)s show events                   # Show recent events cache entries
  %(prog)s show events --limit 20        # Show 20 most recent entries
  %(prog)s view events <cache-key>       # View specific cache entry
  %(prog)s clear events                  # Clear events cache
  %(prog)s clear --all                   # Clear all caches
        """,
    )

    parser.add_argument(
        "command", choices=["list", "show", "view", "clear"], help="Command to execute"
    )
    parser.add_argument(
        "cache_type", nargs="?", help="Cache type (events, dates, places, people, etc.)"
    )
    parser.add_argument("cache_key", nargs="?", help="Cache key (for view command)")
    parser.add_argument(
        "--cache-dir",
        type=Path,
        default=Path("cache/api"),
        help="Cache directory (default: cache/api)",
    )
    parser.add_argument(
        "--limit", type=int, default=10, help="Number of entries to show (default: 10)"
    )
    parser.add_argument(
        "--all", action="store_true", help="Clear all caches (for clear command)"
    )

    args = parser.parse_args()

    if not args.cache_dir.exists():
        print(f"Error: Cache directory not found: {args.cache_dir}")
        return 1

    if args.command == "list":
        list_caches(args.cache_dir)

    elif args.command == "show":
        if not args.cache_type:
            parser.error("show command requires cache_type")
        show_cache(args.cache_dir, args.cache_type, args.limit)

    elif args.command == "view":
        if not args.cache_type or not args.cache_key:
            parser.error("view command requires cache_type and cache_key")
        view_entry(args.cache_dir, args.cache_type, args.cache_key)

    elif args.command == "clear":
        if args.all:
            confirm = input("Clear ALL caches? (yes/no): ")
            if confirm.lower() == "yes":
                clear_cache(args.cache_dir)
        elif args.cache_type:
            confirm = input(f"Clear {args.cache_type} cache? (yes/no): ")
            if confirm.lower() == "yes":
                clear_cache(args.cache_dir, args.cache_type)
        else:
            parser.error("clear command requires --all or cache_type")

    return 0


if __name__ == "__main__":
    exit(main())

#!/usr/bin/env python3
"""Test NARA bibliography resolution with limited requests.

Usage:
    python3 scripts/test_nara_resolve.py [--max-items 30]
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.enrichment.bibliography_resolver import resolve_bibliography_dir
from src.grok_client import GrokClient
from src.utils.config import load_config


def main():
    parser = argparse.ArgumentParser(description="Test NARA bibliography resolution")
    parser.add_argument(
        "--max-items", type=int, default=30, help="Max items to process"
    )
    args = parser.parse_args()

    config = load_config()
    nara_key = config.get("api", {}).get("nara_api_key", "")
    if not nara_key:
        print("❌ No nara_api_key in config.yaml")
        return 1

    print(f"NARA API key: {nara_key[:8]}...")
    print(f"Max items: {args.max_items}")
    print()

    import logging

    logging.basicConfig(level=logging.INFO, format="%(name)s - %(message)s")
    # Show what's being searched
    logging.getLogger("src.enrichment.bibliography_resolver").setLevel(logging.DEBUG)
    logging.getLogger("src.grok_client").setLevel(logging.WARNING)

    grok_client = GrokClient(Path("cache/api"))
    resolve_config = {
        "nara_api_key": nara_key,
        "search_gutenberg": False,
        "search_archive_org": False,
        "use_openserp": False,
    }

    stats = resolve_bibliography_dir(
        Path("output/bibliography"),
        grok_client,
        resolve_config,
        max_items=args.max_items,
    )

    print(f"\nResults:")
    print(f"  Resolved: {stats['resolved']}")
    print(f"  Not found: {stats['not_found']}")
    print(f"  Skipped (already done): {stats['skipped']}")
    print(f"  Errors: {stats['errors']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

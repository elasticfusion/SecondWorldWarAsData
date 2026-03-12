#!/usr/bin/env python3
"""Test batch+parallel extraction on single chapter."""

import asyncio
import time
from pathlib import Path

from dotenv import load_dotenv

from src.utils.config import load_config, get_paths
from src.grok_client import GrokClient
from src.extraction.batch_parallel import extract_all_async

load_dotenv()


def test_batch_parallel():
    """Test batch+parallel on one chapter."""
    base_dir = Path(__file__).parent
    config = load_config(base_dir / "config.yaml")
    paths = get_paths(config, base_dir)

    # Find first parsed file
    parsed_files = list(paths["output_root"].rglob("*-parsed.json"))
    if not parsed_files:
        print("No parsed files found")
        return

    parsed_file = parsed_files[0]
    event_file = parsed_file.parent / parsed_file.name.replace(
        "-parsed.json", "-event.json"
    )

    if not event_file.exists():
        print(f"Event file not found: {event_file}")
        return

    print(f"Testing: {parsed_file.name}")
    print(f"Event file: {event_file.name}\n")

    # Initialize client
    grok_client = GrokClient(paths["api_cache"])

    # Time the extraction
    start = time.time()

    results = asyncio.run(
        extract_all_async(
            event_file=event_file,
            parsed_file=parsed_file,
            grok_client=grok_client,
            output_root=paths["output_root"],
            config=config,
        )
    )

    elapsed = time.time() - start

    print(f"\n{'=' * 60}")
    print("Results:")
    print(f"  Dates: {results['dates']}")
    print(f"  Places: {results['places']}")
    print(f"  Groups: {results['groups']}")
    print(f"\nTime: {elapsed:.1f}s")
    print("=" * 60)


if __name__ == "__main__":
    test_batch_parallel()

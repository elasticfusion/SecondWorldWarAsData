#!/usr/bin/env python3
"""Test batch events extraction."""

import asyncio
import time
from pathlib import Path

from dotenv import load_dotenv

from src.utils.config import load_config, get_paths
from src.grok_client import GrokClient
from src.extraction.batch_parallel import extract_events_batch_async

load_dotenv()


def test_batch_events():
    """Test batch events extraction on multiple chapters."""
    base_dir = Path(__file__).parent
    config = load_config(base_dir / "config.yaml")
    paths = get_paths(config, base_dir)

    # Find parsed files without events
    parsed_files = list(paths["output_root"].rglob("*-parsed.json"))
    files_needing_events = [
        pf
        for pf in parsed_files[:3]  # Test with 3 chapters
        if not (pf.parent / (pf.stem.replace("-parsed", "") + "-event.json")).exists()
    ]

    if not files_needing_events:
        print("No files need event extraction (all exist)")
        # Use first 3 for testing anyway
        files_needing_events = parsed_files[:3]

    print(f"Testing batch events extraction on {len(files_needing_events)} chapters:\n")
    for pf in files_needing_events:
        print(f"  - {pf.name}")

    # Initialize client
    grok_client = GrokClient(paths["api_cache"])

    # Time the extraction
    start = time.time()

    event_files = asyncio.run(
        extract_events_batch_async(
            parsed_files=files_needing_events,
            grok_client=grok_client,
            output_dir=paths["output_root"],
        )
    )

    elapsed = time.time() - start

    print(f"\n{'=' * 60}")
    print("Results:")
    print(f"  Event files created: {len(event_files)}")
    for ef in event_files:
        print(f"    ✓ {ef.name}")
    print(f"\nTime: {elapsed:.1f}s")
    print(f"Average: {elapsed/len(files_needing_events):.1f}s per chapter")
    print("=" * 60)


if __name__ == "__main__":
    test_batch_events()

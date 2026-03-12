#!/usr/bin/env python3
"""Test parallel chapter processing."""

import asyncio
import time
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

from src.utils.config import load_config, get_paths
from src.grok_client import GrokClient
from src.extraction.batch_parallel import process_chapters_parallel


def test_parallel_chapters():
    """Test parallel processing of multiple chapters."""
    base_dir = Path(__file__).parent
    config = load_config(base_dir / "config.yaml")
    paths = get_paths(config, base_dir)

    # Test with 3 chapters
    parsed_files = list(paths["output_root"].rglob("*-parsed.json"))[:3]

    print(f"Testing parallel processing on {len(parsed_files)} chapters:\n")
    for pf in parsed_files:
        print(f"  - {pf.name}")

    # Initialize client
    grok_client = GrokClient(paths["api_cache"])

    # Time the processing
    start = time.time()

    results = asyncio.run(
        process_chapters_parallel(
            parsed_files=parsed_files,
            grok_client=grok_client,
            output_root=paths["output_root"],
            config=config,
            max_parallel=3,
        )
    )

    elapsed = time.time() - start

    print(f"\n{'='*60}")
    print(f"Results:")
    print(f"  Processed: {results['processed']}")
    print(f"  Failed: {results['failed']}")
    print(f"\nTime: {elapsed:.1f}s")
    print(f"Average: {elapsed/len(parsed_files):.1f}s per chapter")
    print(f"\nComparison:")
    print(f"  Sequential: {39 * len(parsed_files):.1f}s (39s per chapter)")
    print(f"  Parallel:   {elapsed:.1f}s")
    print(f"  Speedup:    {(39 * len(parsed_files)) / elapsed:.1f}x")
    print(f"{'='*60}")


if __name__ == "__main__":
    test_parallel_chapters()

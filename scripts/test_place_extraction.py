#!/usr/bin/env python3
"""Test place extraction with simplified prompt."""

from pathlib import Path
from src.grok_client import GrokClient
from src.extraction.places import extract_places

# Initialize client
cache_dir = Path("cache/api")
grok_client = GrokClient(cache_dir)

# Test with one file
test_file = Path("output/BreakoutAndPursuit/chapter1a-event.json")

print(f"Testing place extraction on: {test_file.name}")
print("=" * 60)

try:
    result = extract_places(
        event_file=test_file,
        grok_client=grok_client,
        output_dir=test_file.parent,
    )

    if result:
        print(f"✓ Success: {result.name}")
    else:
        print("⏭ Skipped (no content)")

except Exception as e:
    print(f"✗ Error: {e}")

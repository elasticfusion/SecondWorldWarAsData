#!/usr/bin/env python3
"""Test supplemental material extraction - Phase 1."""

import json
from pathlib import Path

from src.grok_client import GrokClient
from src.extraction.supplemental import extract_supplemental
from src.utils.config import load_config, get_paths


def test_supplemental_extraction():
    """Test supplemental material extraction on a sample event file."""
    base_dir = Path(__file__).parent.parent
    config = load_config(base_dir / "config.yaml")
    paths = get_paths(config, base_dir)

    # Initialize Grok client
    grok_client = GrokClient(paths["api_cache"])

    # Find first event file
    output_root = paths["output_root"]
    event_files = list(output_root.glob("**/*-event.json"))

    if not event_files:
        print("No event files found. Run phase2_extract.py first.")
        return

    test_file = event_files[0]
    print(f"Testing with: {test_file.name}")

    # Create output directory
    supplemental_dir = output_root / "supplemental"
    supplemental_dir.mkdir(exist_ok=True)

    # Extract supplemental material
    result = extract_supplemental(
        event_file=test_file,
        grok_client=grok_client,
        output_dir=supplemental_dir,
    )

    if result:
        print(f"\n✓ Created: {result}")

        # Display sample
        with open(result, "r", encoding="utf-8") as f:
            data = json.load(f)

        print(f"\nExtracted {len(data)} sub-event(s)")

        for item in data[:1]:  # Show first sub-event
            materials = item.get("Supplemental_Material", [])
            print(f"\nSub-event: {item.get('Sub-event_Name', 'Unknown')}")
            print(f"Materials found: {len(materials)}")

            for mat in materials[:2]:  # Show first 2 materials
                print(f"\n  Type: {mat.get('reference_type')}")
                print(f"  Number: {mat.get('reference_number')}")
                print(f"  MaterialID: {mat.get('MaterialID')}")
                print(f"  Availability: {mat.get('availability')}")
                print(f"  License: {mat.get('license')}")
                citation = mat.get("citation", {})
                print(f"  Title: {citation.get('title', 'N/A')}")
                print(f"  Author: {', '.join(citation.get('author', []))}")
    else:
        print("No supplemental material extracted")


if __name__ == "__main__":
    test_supplemental_extraction()

#!/usr/bin/env python3
"""
Use OpenSERP search results and verify with Grok.

Usage:
    ./search_maps -place "Brest" -date "1944-08-25" | python3 verify_and_import.py
"""

import json
import sys
from pathlib import Path
from datetime import datetime
import ulid

# Add src to path
sys.path.insert(0, str(Path(__file__).parent))

from src.grok_client import GrokClient
from src.extraction.search_external_maps import _verify_map_relevance


def main():
    # Read search results from stdin
    search_results = json.load(sys.stdin)

    if not search_results:
        print("No search results provided", file=sys.stderr)
        sys.exit(1)

    # Initialize Grok
    grok_client = GrokClient(cache_dir=Path("cache/api"))

    # Process each result
    imported = 0
    for result in search_results:
        url = result["url"]
        title = result["title"]

        print(f"\n🔍 Checking: {title}")
        print(f"   URL: {url}")

        # Extract place and date from command line args (passed via env)
        place_name = sys.argv[1] if len(sys.argv) > 1 else "Unknown"
        date = sys.argv[2] if len(sys.argv) > 2 else None
        event_context = sys.argv[3] if len(sys.argv) > 3 else "WWII operations"

        # Verify with Grok
        is_relevant = _verify_map_relevance(
            map_url=url,
            map_title=title,
            place_name=place_name,
            date=date,
            event_context=event_context,
            grok_client=grok_client,
        )

        if is_relevant:
            print(f"   ✅ Verified - importing")

            # Create map record
            map_record = {
                "MapID": str(ulid.new()),
                "map_title": title,
                "external_source": result["engine"].title(),
                "external_source_url": url,
                "description": result["description"],
                "license": "Unknown",
                "place_name": place_name,
                "date": date,
                "found_via": f"OpenSERP {result['engine']} search",
                "found_date": datetime.utcnow().strftime("%Y-%m-%d"),
                "extracted_date": datetime.utcnow().isoformat() + "Z",
                "storage_backend": "filesystem",
            }

            # Save to output/external_maps/
            output_dir = Path("output/external_maps")
            output_dir.mkdir(parents=True, exist_ok=True)

            output_file = output_dir / f"{map_record['MapID']}.json"
            with open(output_file, "w") as f:
                json.dump(map_record, f, indent=2)

            print(f"   💾 Saved: {output_file.name}")
            imported += 1
        else:
            print(f"   ⚠️  Rejected by Grok")

    print(f"\n✅ Imported {imported}/{len(search_results)} maps")


if __name__ == "__main__":
    main()

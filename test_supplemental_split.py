#!/usr/bin/env python3
"""Test supplemental split on a single chapter."""

import json
import logging
import sys
from pathlib import Path

logging.basicConfig(level=logging.DEBUG, format="%(levelname)s - %(name)s - %(message)s")

sys.path.insert(0, str(Path(__file__).parent))

from src.grok_client import GrokClient
from src.extraction.supplemental import extract_supplemental

# Chapter 7b has known mixed content (factual + document references)
EVENT_FILE = Path("output/BreakoutAndPursuit/chapter7b-event.json")
OUTPUT_ROOT = Path("output")

if not EVENT_FILE.exists():
    print(f"Event file not found: {EVENT_FILE}")
    sys.exit(1)

grok = GrokClient(cache_dir=Path("cache/api"))
result = extract_supplemental(EVENT_FILE, grok, OUTPUT_ROOT)

print("\n" + "=" * 60)
print("RESULTS")
print("=" * 60)

# Check bibliography
bib_dir = OUTPUT_ROOT / "bibliography"
if bib_dir.exists():
    bib_files = [f for f in bib_dir.glob("*.json") if f.name not in ("index.json", "review_queue.json")]
    print(f"\nBibliography entries: {len(bib_files)}")
    for f in sorted(bib_files)[:5]:
        data = json.load(open(f))
        print(f"  {f.name}: {data.get('title', '?')} [{len(data.get('mentions', []))} mentions]")
    if len(bib_files) > 5:
        print(f"  ... and {len(bib_files) - 5} more")

# Check notes-event
notes_file = EVENT_FILE.with_name("chapter7b-notes-event.json")
if notes_file.exists():
    data = json.load(open(notes_file))
    subs = data.get("Event", {}).get("Sub-events", [])
    print(f"\nFactual notes-event sub-events: {len(subs)}")
    for s in subs[:3]:
        print(f"  ref {s.get('source_reference', {}).get('reference_number', '?')}: {s['Sub-event_summary'][:100]}")
else:
    print("\nNo notes-event file created")

# Check review queue
queue_file = bib_dir / "review_queue.json"
if queue_file.exists():
    queue = json.load(open(queue_file))
    print(f"\nAmbiguous (review queue): {len(queue)}")
    for item in queue[:3]:
        print(f"  ref {item.get('reference_number', '?')}: {item.get('verbatim_reference', '')[:100]}")
else:
    print("\nNo ambiguous items")

print()

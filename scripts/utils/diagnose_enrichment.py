#!/usr/bin/env python3
"""Diagnose enrichment state — check schema versions and enrichment status."""

import json
from pathlib import Path

skip = {
    "index.json",
    "duplicate_report.json",
    "not_duplicates.json",
    "not_related.json",
    "review_queue.json",
}

print("=== Sample Files ===\n")
for name, path in [
    ("People", "output/people"),
    ("Bibliography", "output/bibliography"),
    ("Places", "output/places"),
]:
    p = Path(path)
    if not p.exists():
        continue
    print(f"--- {name} ---")
    count = 0
    for f in sorted(p.glob("*.json")):
        if f.name in skip:
            continue
        try:
            d = json.loads(f.read_text())
        except:
            continue
        print(
            f"  {f.name[:50]:50s} schema={str(d.get('_schema_version')):5s} status={str(d.get('enrichment_status', d.get('search_status'))):10s}"
        )
        count += 1
        if count >= 5:
            break
    print()

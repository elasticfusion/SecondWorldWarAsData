#!/usr/bin/env python3
"""Show enrichment status across all entity types."""

import json
from pathlib import Path

skip = {
    "index.json",
    "duplicate_report.json",
    "not_duplicates.json",
    "not_related.json",
    "review_queue.json",
}

print("=== Enrichment Status ===\n")
for name, path, field in [
    ("People", Path("output/people"), "enrichment_status"),
    ("Groups", Path("output/people_groups"), "enrichment_status"),
    ("Places", Path("output/places"), "enrichment_status"),
    ("Equipment", Path("output/equipment"), "enrichment_status"),
    ("Bibliography", Path("output/bibliography"), "search_status"),
]:
    if not path.exists():
        continue
    total = enriched = not_found = pending = 0
    for f in path.glob("*.json"):
        if f.name in skip:
            continue
        try:
            data = json.loads(f.read_text())
        except:
            continue
        total += 1
        status = data.get(field, "")
        if status in ("enriched", "resolved"):
            enriched += 1
        elif status == "not_found":
            not_found += 1
        else:
            pending += 1
    pct = f"{enriched/total*100:.0f}%" if total else "0%"
    print(
        f"{name:15s} {total:5d} total | {enriched:5d} enriched ({pct}) | {not_found:5d} not found | {pending:5d} pending"
    )

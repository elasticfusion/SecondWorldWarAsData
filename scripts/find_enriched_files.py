#!/usr/bin/env python3
"""Find files that have enrichment data regardless of status field."""

import json
from pathlib import Path

skip = {
    "index.json",
    "duplicate_report.json",
    "not_duplicates.json",
    "not_related.json",
}

print("=== Looking for enriched files ===\n")

# Groups - check for enrichment_data field
print("--- Groups with enrichment_data ---")
count = 0
for f in sorted(Path("output/people_groups").glob("*.json")):
    if f.name in skip:
        continue
    d = json.loads(f.read_text())
    if (
        d.get("enrichment_data")
        or d.get("_schema_version")
        or d.get("enrichment_status")
    ):
        print(
            f"  {f.name[:50]} schema={d.get('_schema_version')} status={d.get('enrichment_status')} has_data={bool(d.get('enrichment_data'))}"
        )
        count += 1
        if count >= 5:
            break
if count == 0:
    print("  None found")

# People - check for biographical_profile
print("\n--- People with biographical_profile ---")
count = 0
for f in sorted(Path("output/people").glob("*.json")):
    if f.name in skip:
        continue
    d = json.loads(f.read_text())
    bio = d.get("biographical_profile", {})
    if (
        bio.get("birth_date")
        or bio.get("biographical_details")
        or d.get("_schema_version")
    ):
        print(
            f"  {f.name[:50]} schema={d.get('_schema_version')} status={d.get('enrichment_status')} bio={bool(bio.get('biographical_details'))}"
        )
        count += 1
        if count >= 5:
            break
if count == 0:
    print("  None found")

# Check raw file sizes to see if anything changed
print("\n--- File size check (largest people files) ---")
files = [
    (f.stat().st_size, f.name)
    for f in Path("output/people").glob("*.json")
    if f.name not in skip
]
files.sort(reverse=True)
for size, name in files[:5]:
    print(f"  {size:8d} bytes  {name}")

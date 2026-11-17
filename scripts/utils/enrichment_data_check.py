#!/usr/bin/env python3
"""Check actual enrichment data presence (regardless of status field)."""

import json
from pathlib import Path

skip = {
    "index.json",
    "duplicate_report.json",
    "not_duplicates.json",
    "not_related.json",
    "review_queue.json",
}

print("=== Actual Data Presence ===\n")

# People
has = no = 0
for f in Path("output/people").glob("*.json"):
    if f.name in skip:
        continue
    d = json.loads(f.read_text())
    bio = d.get("biographical_profile", {})
    if (
        bio.get("birth_date")
        or bio.get("biographical_details")
        or bio.get("wikipedia_url")
    ):
        has += 1
    else:
        no += 1
print(f"People:       {has:4d} with bio data, {no:4d} without")

# Bibliography
resolved = has_ref = no_ref = 0
for f in Path("output/bibliography").glob("*.json"):
    if f.name in skip:
        continue
    try:
        d = json.loads(f.read_text())
    except:
        continue
    ref = d.get("archive_reference_number")
    urls = d.get("resource_urls", [])
    if urls:
        resolved += 1
    elif ref and ref != "None":
        has_ref += 1
    else:
        no_ref += 1
print(
    f"Bibliography: {resolved:4d} with URLs, {has_ref:4d} with archive ref only, {no_ref:4d} unresolved"
)

# Places
has = no = 0
for f in Path("output/places").glob("*.json"):
    if f.name in skip:
        continue
    try:
        d = json.loads(f.read_text())
    except:
        continue
    coords = d.get("coordinates", {})
    if coords.get("latitude") or d.get("gps_coordinate"):
        has += 1
    else:
        no += 1
print(f"Places:       {has:4d} with coordinates, {no:4d} without")

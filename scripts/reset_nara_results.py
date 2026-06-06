#!/usr/bin/env python3
"""Reset all NARA-resolved bibliography entries for re-verification.

Removes catalog.archives.gov URLs and sets search_status back to unresolved
so the resolver will re-process them with the new verification logic.
"""

import json
import glob
import os

BIB_DIR = os.path.join(os.path.dirname(__file__), "..", "output", "bibliography")

reset = 0
for f in sorted(glob.glob(os.path.join(BIB_DIR, "*.json"))):
    try:
        d = json.load(open(f))
    except (json.JSONDecodeError, OSError):
        continue
    if not isinstance(d, dict):
        continue

    urls = d.get("resource_urls") or []
    nara_urls = [u for u in urls if "catalog.archives.gov" in u]
    if not nara_urls:
        continue

    # Remove NARA URLs
    d["resource_urls"] = [u for u in urls if "catalog.archives.gov" not in u]
    if not d["resource_urls"]:
        d["resource_urls"] = []

    # Reset status
    d["search_status"] = "unresolved"
    d.pop("search_source", None)

    # Keep archive_reference_number (RG info is still valid)

    with open(f, "w") as out:
        json.dump(d, out, indent=2, ensure_ascii=False)
    reset += 1

print(f"Reset {reset} NARA-resolved entries to unresolved")

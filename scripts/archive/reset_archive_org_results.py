#!/usr/bin/env python3
"""Reset all archive.org-resolved bibliography entries for re-verification."""

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
    archive_urls = [u for u in urls if "archive.org" in u]
    if not archive_urls:
        continue

    d["resource_urls"] = [u for u in urls if "archive.org" not in u]
    d["search_status"] = "unresolved"
    d.pop("search_source", None)

    with open(f, "w") as out:
        json.dump(d, out, indent=2, ensure_ascii=False)
    reset += 1

print(f"Reset {reset} archive.org-resolved entries to unresolved")

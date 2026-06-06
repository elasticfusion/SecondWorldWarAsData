#!/usr/bin/env python3
"""Group bibliography files by URL source domain and write markdown report."""

import json
import glob
import os
from collections import defaultdict
from urllib.parse import urlparse

OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "..", "output", "bibliography")
REPORT_PATH = os.path.join(os.path.dirname(__file__), "..", "docs", "current", "dataquality", "bibliography_by_source.md")

groups = defaultdict(list)

for f in sorted(glob.glob(os.path.join(OUTPUT_DIR, "*.json"))):
    try:
        d = json.load(open(f))
    except (json.JSONDecodeError, OSError):
        continue

    if isinstance(d, list):
        d = d[0] if d else {}
    if not isinstance(d, dict):
        continue

    urls = d.get("resource_urls") or []
    title = d.get("title") or "Unknown"
    bid = d.get("BibliographyID", "")
    fname = os.path.basename(f)
    status = d.get("search_status", "unknown")

    if not urls:
        groups["(no URL)"].append({"title": title, "id": bid, "file": fname, "url": None, "status": status})
    else:
        for url in urls:
            try:
                domain = urlparse(url).netloc or "(invalid URL)"
            except Exception:
                domain = "(invalid URL)"
            groups[domain].append({"title": title, "id": bid, "file": fname, "url": url, "status": status})

# Write markdown
with open(REPORT_PATH, "w") as out:
    total = sum(len(v) for v in groups.values())
    out.write("# Bibliography Files by URL Source\n\n")
    out.write(f"**Generated:** 2026-06-05  \n")
    out.write(f"**Total entries:** {total}  \n")
    out.write(f"**Unique sources:** {len(groups)}\n\n")
    out.write("| Source | Count | % |\n|---|---|---|\n")
    for domain, items in sorted(groups.items(), key=lambda x: -len(x[1])):
        out.write(f"| {domain} | {len(items)} | {len(items)/total*100:.1f}% |\n")
    out.write("\n---\n\n")

    for domain, items in sorted(groups.items(), key=lambda x: -len(x[1])):
        out.write(f"## {domain} ({len(items)} files)\n\n")
        out.write("| Title | Status | URL |\n|---|---|---|\n")
        for item in sorted(items, key=lambda x: x["title"]):
            url_display = item["url"] or "—"
            if len(url_display) > 80:
                url_display = url_display[:77] + "..."
            out.write(f"| {item['title'][:60]} | {item['status']} | {url_display} |\n")
        out.write("\n")

print(f"Report written: {REPORT_PATH}")
print(f"  {total} entries across {len(groups)} sources")

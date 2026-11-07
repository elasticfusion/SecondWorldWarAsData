#!/usr/bin/env python3
"""Reset openserp_searched flag on people and equipment files so OpenSERP re-searches them."""

import json
from pathlib import Path

skip = {"index.json", "duplicate_report.json", "not_duplicates.json"}
reset = 0

for d in [Path("output/people"), Path("output/equipment")]:
    if not d.exists():
        continue
    for f in d.glob("*.json"):
        if f.name in skip:
            continue
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except:
            continue
        if data.get("openserp_searched"):
            del data["openserp_searched"]
            f.write_text(
                json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
            )
            reset += 1

print(f"Reset openserp_searched on {reset} files")

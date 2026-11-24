#!/usr/bin/env python3
"""Clear a link that Google Safe Browsing flagged by mistake (src/utils/link_safety).

The operator's decision is kept 10 years and wins over every later flag; the flag it overrides is
kept inside it (provenance). Links already removed from kept pages are not put back: pages are
write-once, so the page is kept again (with the link) the next time its find is fetched.

    python scripts/allow_link.py --url https://example.org/page --reason "site cleaned, checked"
    python scripts/allow_link.py --url https://example.org/page --show
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def main(argv=None) -> int:
    from src.utils import link_safety

    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--url", required=True)
    ap.add_argument("--reason", default="")
    ap.add_argument("--who", default="operator")
    ap.add_argument(
        "--show", action="store_true", help="print the flag record and stop"
    )
    args = ap.parse_args(argv)
    record = link_safety.flagged_record(args.url)
    if args.show:
        print(
            json.dumps(record, indent=2)
            if record
            else "not flagged (or already allowed)"
        )
        return 0
    if not args.reason.strip():
        print("--reason is required: say why the flag is wrong", file=sys.stderr)
        return 2
    if not link_safety.allow(args.url, args.reason, args.who):
        print("not a clean http(s) URL", file=sys.stderr)
        return 2
    print(
        f"allowed: {args.url} (was: {', '.join((record or {}).get('threats') or []) or 'not flagged'})"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())

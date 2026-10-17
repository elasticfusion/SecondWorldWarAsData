#!/usr/bin/env python3
"""Build the HyperWar green-book bibliography index (build-time, refreshable).

For each US Army green-book volume, DISCOVER its Bibliographical Note page by following the
index's "Bibliograph…" link (the filename varies: ``-Biblio.html``, ``-Bib.html``, or an
appendix), fetch it, parse the Published-Works book list, and write
``src/enrichment/data/hyperwar_biblio.json`` mapping ``volume -> {biblio_url, published_works}``.

The runtime resolver (``src/enrichment/hyperwar_biblio.py``) loads that data file for the
per-volume published-works and keeps a GENERIC archival-category layer that needs no per-volume
data. Re-run this script to add/refresh volumes.

Usage:
    python scripts/build_hyperwar_biblio_index.py            # all known volumes
    python scripts/build_hyperwar_biblio_index.py --volume USA-E-Siegfried
"""

import argparse
import json
import re
import sys
import urllib.request
from pathlib import Path
from typing import Dict, List, Optional

BASE = "https://www.ibiblio.org/hyperwar/USA"
OUT = Path(__file__).resolve().parents[1] / "src/enrichment/data/hyperwar_biblio.json"

# Full green-book volume set (index pages). Keyed by volume id.
VOLUMES = [
    "USA-P-Strategy",
    "USA-P-PI",
    "USA-P-Guadalcanal",
    "USA-P-Papua",
    "USA-P-Rabaul",
    "USA-P-Gilberts",
    "USA-P-Marianas",
    "USA-P-Approach",
    "USA-P-Return",
    "USA-P-Triumph",
    "USA-P-Okinawa",
    "USA-MTO-NWA",
    "USA-MTO-Sicily",
    "USA-MTO-Salerno",
    "USA-MTO-Cassino",
    "USA-E-Supreme",
    "USA-E-XChannel",
    "USA-E-Breakout",
    "USA-E-Riviera",
    "USA-E-Ardennes",
    "USA-E-Lorraine",
    "USA-E-Siegfried",
    "USA-E-Last",
    "USA-E-Logistics1",
    "USA-E-Logistics2",
    "USA-CBI-Mission",
    "USA-CBI-Command",
    "USA-CBI-Time",
]

_HREF = re.compile(r'href\s*=\s*["\']?([^"\'>\s]+)["\']?[^>]*>\s*([^<]*)', re.I)
_BIB_TEXT = re.compile(r"bibliograph", re.I)


def _get(url: str, timeout: int = 30) -> Optional[str]:
    try:
        req = urllib.request.Request(
            url, headers={"User-Agent": "wwii-pipeline/biblio-index"}
        )
        with urllib.request.urlopen(
            req, timeout=timeout
        ) as r:  # noqa: S310 - fixed host
            return r.read().decode("utf-8", "replace")
    except Exception as e:  # noqa: BLE001
        print(f"  ! fetch failed {url}: {e}", file=sys.stderr)
        return None


def discover_biblio_url(volume: str) -> Optional[str]:
    """Follow the volume index's 'Bibliograph…' link. Returns absolute URL or None."""
    idx = f"{BASE}/{volume}/index.html"
    html = _get(idx)
    if not html:
        return None
    for href, text in _HREF.findall(html):
        if (
            _BIB_TEXT.search(text)
            or _BIB_TEXT.search(href)
            or re.search(r"-Bib\b", href)
        ):
            if href.startswith("http"):
                return href
            return f"{BASE}/{volume}/{href.lstrip('./')}"
    return None


def build(volumes: List[str]) -> Dict[str, Dict]:
    index: Dict[str, Dict] = {}
    for vol in volumes:
        print(f"[{vol}]")
        bib_url = discover_biblio_url(vol)
        if not bib_url:
            print("  no bibliography link found")
            index[vol] = {"biblio_url": None}
            continue
        print(f"  biblio: {bib_url}")
        # NOTE: only the biblio_url is emitted. Auto-parsing the Published-Works book list from
        # the free-text note proved unreliable (publisher/year fragments misread as author/title),
        # so per-volume published works are hand-curated in src/enrichment/hyperwar_biblio.py for
        # volumes that have been verified. The generic archival-category layer + this correct
        # per-volume provenance URL cover every volume regardless.
        index[vol] = {"biblio_url": bib_url}
    return index


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--volume", help="single volume id (default: all)")
    args = ap.parse_args()
    vols = [args.volume] if args.volume else VOLUMES
    index = build(vols)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(index, indent=2, sort_keys=True), encoding="utf-8")
    covered = sum(1 for v in index.values() if v.get("biblio_url"))
    print(f"\nWrote {OUT} — {covered}/{len(index)} volumes with a biblio URL")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

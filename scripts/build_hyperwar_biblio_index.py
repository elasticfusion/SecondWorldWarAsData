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
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Dict, List, Optional

BASE = "https://www.ibiblio.org/hyperwar/USA"
OUT = Path(__file__).resolve().parents[1] / "src/enrichment/data/hyperwar_biblio.json"

# SSRF guard: this build-time generator scrapes hrefs out of fetched (untrusted) HTML and would
# otherwise follow/store ANY absolute URL it finds. Constrain every fetched/stored URL to HTTPS on
# the known HyperWar host, so a tampered/MITM'd source page cannot redirect the generator at an
# internal address (cloud metadata 169.254.169.254, localhost, private ranges) or an arbitrary site.
_SAFE_HOSTS = frozenset({"ibiblio.org", "www.ibiblio.org"})

# Count of URLs rejected by the SSRF guard this run (observability: lets the build summary tell
# "no bibliography link" apart from "guard blocked a non-allowlisted link").
_guard_rejections = 0


def _is_safe_biblio_url(url: str) -> bool:
    """True only for an https:// URL whose host is in the HyperWar allowlist (no credentials, no
    alternate scheme). Used to gate both outbound fetches and stored biblio_urls."""
    try:
        p = urllib.parse.urlparse(url)
    except (ValueError, TypeError):
        return False
    if p.scheme != "https":
        return False
    # urlparse puts any user:pass@ in netloc; .hostname strips it (and the port) for the check.
    host = (p.hostname or "").lower()
    if host not in _SAFE_HOSTS:
        return False
    # Reject embedded credentials / explicit alternate port shenanigans.
    if "@" in p.netloc:
        return False
    return True


class _AllowlistRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Re-validate EVERY redirect target against the allowlist. Closes the SSRF residual where a
    guard-approved ibiblio URL 302-redirects to an internal/off-host address — urlopen would
    otherwise follow it blindly. A disallowed target raises, aborting the fetch."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[override]
        if not _is_safe_biblio_url(newurl):
            print(
                f"  ! refusing redirect to non-allowlisted URL: {newurl}",
                file=sys.stderr,
            )
            raise urllib.error.HTTPError(
                newurl, code, "redirect to non-allowlisted URL blocked", headers, fp
            )
        return super().redirect_request(req, fp, code, msg, headers, newurl)


# Opener whose ONLY redirect handler re-checks the allowlist (no proxy/auth handlers added).
_OPENER = urllib.request.build_opener(_AllowlistRedirectHandler())


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
    # Defense-in-depth: never fetch a URL outside the HTTPS HyperWar allowlist.
    if not _is_safe_biblio_url(url):
        print(f"  ! refusing to fetch non-allowlisted URL: {url}", file=sys.stderr)
        return None
    try:
        req = urllib.request.Request(
            url, headers={"User-Agent": "wwii-pipeline/biblio-index"}
        )
        with _OPENER.open(
            req, timeout=timeout
        ) as r:  # noqa: S310 - allowlisted https host + redirect targets re-validated
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
                # Absolute href scraped from untrusted HTML — only follow/store it if it is on the
                # HTTPS HyperWar allowlist; otherwise ignore it (SSRF guard) and keep scanning.
                if _is_safe_biblio_url(href):
                    return href
                global _guard_rejections
                _guard_rejections += 1
                print(
                    f"  ! skipping non-allowlisted bibliography href: {href}",
                    file=sys.stderr,
                )
                continue
            # Relative href — pinned under the fixed BASE host (safe by construction).
            return f"{BASE}/{volume}/{href.lstrip('./')}"
    return None


def build(volumes: List[str]) -> Dict[str, Dict]:
    index: Dict[str, Dict] = {}
    guard_blocked: List[str] = (
        []
    )  # volumes whose only biblio href was rejected by the SSRF guard
    for vol in volumes:
        print(f"[{vol}]")
        before = _guard_rejections
        bib_url = discover_biblio_url(vol)
        if not bib_url:
            if _guard_rejections > before:
                # A candidate href WAS found but the SSRF allowlist rejected it — distinct from
                # "no bibliography exists". Surface it so a tampered/changed source page is visible.
                print(
                    "  ! bibliography link REJECTED by SSRF guard (non-allowlisted host/scheme)"
                )
                guard_blocked.append(vol)
            else:
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
    if guard_blocked:
        print(
            f"\n  ! SSRF guard blocked a bibliography href for {len(guard_blocked)} volume(s): "
            f"{', '.join(guard_blocked)} — verify the source pages are not tampered/changed",
            file=sys.stderr,
        )
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

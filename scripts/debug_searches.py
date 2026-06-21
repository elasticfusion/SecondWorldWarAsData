#!/usr/bin/env python3
"""Debug all external search integrations — uses the EXACT same code paths as the container.

Usage:
    source .venv/bin/activate
    python scripts/debug_searches.py [--section all|nara|archive|gutenberg|openserp|wikipedia]

Requires: GROK_API_KEY env var (or in .env), config.yaml with API keys
"""

import argparse
import json
import logging
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv

load_dotenv()

from src.utils.config import load_config
from src.grok_client import GrokClient

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)

LOG_FILE = Path("logs/debug_searches.log")
OUTPUT_DIR = Path("output")

# Collect all confirmed URLs for end-of-run summary
_confirmed_urls: list = []


def _sample_entities(entity_dir: str, count: int = 3, filter_fn=None) -> list:
    """Sample real entities from output/ directory."""
    d = OUTPUT_DIR / entity_dir
    if not d.exists():
        return []
    results = []
    for f in sorted(d.glob("*.json")):
        if f.name in ("index.json", "duplicate_report.json", "not_duplicates.json"):
            continue
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                continue
            if filter_fn and not filter_fn(data):
                continue
            results.append(data)
            if len(results) >= count:
                break
        except (json.JSONDecodeError, OSError):
            continue
    return results


def _dedup_by_title(entries: list, max_count: int) -> list:
    """Deduplicate bibliography entries by title."""
    seen = set()
    unique = []
    for e in entries:
        t = e.get("citation", {}).get("title", "")
        if t not in seen:
            seen.add(t)
            unique.append(e)
        if len(unique) >= max_count:
            break
    return unique


class Tee:
    def __init__(self, *streams):
        self.streams = streams

    def write(self, data):
        for s in self.streams:
            s.write(data)

    def flush(self):
        for s in self.streams:
            s.flush()


# =============================================================================
# NARA — uses _search_nara from bibliography_resolver.py
# =============================================================================


def section_nara(config, grok_client):
    """Test NARA search using production code path."""
    print("\n" + "=" * 70)
    print("NARA (National Archives)")
    print("=" * 70)

    nara_key = config.get("api", {}).get("nara_api_key", "")
    print(f"  API key: {'set' if nara_key else 'MISSING'}")
    if not nara_key:
        print("  SKIPPED — no API key")
        return

    from src.enrichment.bibliography_resolver import (
        _identify_nara_record,
        _search_nara,
    )

    # Sample real military record citations
    entries = _sample_entities(
        "bibliography", count=9999,
        filter_fn=lambda d: d.get("search_status") != "resolved"
        and any(kw in (d.get("mentions", [{}])[0].get("verbatim_reference", "") if d.get("mentions") else "")
                for kw in ["Jnl", "AAR", "Rpt", "Ltr", "Div", "Corps", "SHAEF"]),
    )
    entries = _dedup_by_title(entries, 9999)

    if not entries:
        entries = [
            {"citation": {"title": "101st Inf Jnl, 14 Jul 44"}, "mentions": [{"verbatim_reference": "101st Inf Jnl, 14 Jul 44"}]},
            {"citation": {"title": "104th Div AAR"}, "mentions": [{"verbatim_reference": "104th Div AAR, 23-31 Oct 44"}]},
        ]

    print(f"\n  Searching {len(entries)} entries (Grok RG identify → NARA catalog, until first success):\n")
    found = False
    for entry in entries:
        verbatim = ""
        for m in entry.get("mentions", []):
            v = m.get("verbatim_reference", "")
            if v:
                verbatim = v[:100]
                break
        title = entry.get("citation", {}).get("title", "Unknown")
        search_text = verbatim or title
        print(f"  Citation: {search_text[:70]}")

        # Step 1: Grok identifies Record Group (same as production)
        rg = entry.get("archive_reference_number") or ""
        if "RG" not in rg.upper():
            rg = _identify_nara_record(search_text, grok_client)
            if rg:
                print(f"    Grok RG: {rg}")
            else:
                print(f"    Grok RG: UNKNOWN — skipping")
                print()
                continue

        # Step 2: Build query with RG + verbatim (same as production)
        rg_short = rg.split("\n")[0].strip()[:50]
        query = f"{verbatim[:60]} {rg_short}" if verbatim else rg_short
        print(f"    NARA query: {query[:70]}")

        # Step 3: Search NARA catalog with Grok verification
        url = _search_nara(query, nara_key, grok_client, verbatim)
        if url:
            print(f"    ✅ {url}")
            _confirmed_urls.append({"section": "NARA", "name": search_text[:50], "url": url})
            found = True
            break
        else:
            print(f"    ❌ Not found")
            print(f"    ❌ Not found")
        print()
    if not found:
        print("  ⚠ No positive results in any entry")


# =============================================================================
# Archive.org — uses _search_archive_org_api + fetch_archive_org_metadata
# =============================================================================


def section_archive_org(config, grok_client):
    """Test Archive.org search using production code path."""
    print("\n" + "=" * 70)
    print("Archive.org")
    print("=" * 70)

    from src.extraction.supplemental_search import (
        _search_archive_org_api,
        fetch_archive_org_metadata,
    )

    # Sample published books (not military records)
    def _is_book(d):
        title = d.get("citation", {}).get("title", "")
        if len(title) < 10 or title == "Unknown":
            return False
        verbatim = d.get("mentions", [{}])[0].get("verbatim_reference", "") if d.get("mentions") else ""
        text = verbatim or title
        has_book_signal = any(kw in text for kw in [
            "(Washington", "(London", "(New York", "(Paris", "(Berlin",
            "University Press", "ed.", "Vol.", "Houghton", "Macmillan",
        ])
        has_military = any(kw in text for kw in ["Jnl", "AAR", "Rpt", "Ltr,", " Div ", "Corps", "Mtg", "Telecon", "FO ", "Cable"])
        return has_book_signal and not has_military

    entries = _sample_entities("bibliography", count=200, filter_fn=_is_book)
    entries = _dedup_by_title(entries, 9999)

    if not entries:
        entries = [
            {"citation": {"title": "Crusade in Europe", "author": ["Dwight D. Eisenhower"]}},
            {"citation": {"title": "A Soldier's Story", "author": ["Omar N. Bradley"]}},
        ]

    print(f"\n  Searching {len(entries)} books via _search_archive_org_api() (until first success):\n")
    found = False
    for entry in entries:
        citation = entry.get("citation", {})
        title = citation.get("title", "Unknown")
        author = citation.get("author", [])
        author_str = author[0] if isinstance(author, list) and author else str(author) if author else ""
        time.sleep(2)
        print(f"  Title: {title[:60]}")
        print(f"  Author: {author_str}")
        url = _search_archive_org_api(title, author_str)
        if url:
            found = True
            print(f"    ✅ {url}")
            _confirmed_urls.append({"section": "Archive.org", "name": title[:50], "url": url})
            # Fetch metadata (same as production)
            identifier = url.rstrip("/").split("/")[-1]
            meta = fetch_archive_org_metadata(identifier)
            if meta:
                if meta.get("pdf_url"):
                    print(f"    📄 PDF: {meta['pdf_url']}")
                if meta.get("archive_org_pages"):
                    print(f"    📖 Pages: {meta['archive_org_pages']}")
            break
        else:
            print(f"    ❌ Not found")
        print()

    if not found:
        print("  ⚠ No positive results in any entry")


# =============================================================================
# Gutenberg — uses search_gutenberg_openserp from supplemental_search.py
# =============================================================================


def section_gutenberg(config, grok_client):
    """Test Gutenberg search using production code path (requires OpenSERP)."""
    print("\n" + "=" * 70)
    print("Project Gutenberg (via OpenSERP)")
    print("=" * 70)

    from src.extraction.supplemental_search import search_gutenberg_openserp

    openserp_url = config.get("supplemental_material", {}).get(
        "openserp_url", config.get("openserp", {}).get("url", "http://localhost:7001")
    )

    # Health check
    import requests
    try:
        requests.get(f"{openserp_url}/health", timeout=3)
    except Exception:
        print(f"  OpenSERP not available at {openserp_url} — SKIPPING")
        return

    # Sample published books (same filter as Archive.org section)
    def _is_book(d):
        title = d.get("citation", {}).get("title", "")
        if len(title) < 10 or title == "Unknown":
            return False
        verbatim = d.get("mentions", [{}])[0].get("verbatim_reference", "") if d.get("mentions") else ""
        text = verbatim or title
        has_book_signal = any(kw in text for kw in [
            "(Washington", "(London", "(New York", "(Paris", "(Berlin",
            "University Press", "ed.", "Vol.", "Houghton", "Macmillan",
        ])
        has_military = any(kw in text for kw in ["Jnl", "AAR", "Rpt", "Ltr,", " Div ", "Corps", "Mtg", "Telecon", "FO ", "Cable"])
        return has_book_signal and not has_military

    entries = _sample_entities("bibliography", count=200, filter_fn=_is_book)
    entries = _dedup_by_title(entries, 50)

    if not entries:
        entries = [
            {"citation": {"title": "The Art of War", "author": ["Sun Tzu"]}},
            {"citation": {"title": "On War", "author": ["Carl von Clausewitz"]}},
        ]

    print(f"\n  Searching up to 50 entries via search_gutenberg_openserp() (until first success):\n")
    found = False
    for entry in entries:
        citation = entry.get("citation", {})
        title = citation.get("title", "Unknown")
        author = citation.get("author", [])
        author_str = author[0] if isinstance(author, list) and author else ""
        print(f"  Title: {title[:60]}")
        url = search_gutenberg_openserp(title, author_str, openserp_url)
        if url:
            print(f"    ✅ {url}")
            _confirmed_urls.append({"section": "Gutenberg", "name": title[:50], "url": url})
            found = True
            break
        else:
            print(f"    ❌ Not found")
        print()
    if not found:
        print("  ⚠ No Gutenberg results after all attempts (expected for WWII military texts)")
        print()


# =============================================================================
# OpenSERP — uses search_portrait_images, search_military_awards from openserp_enrichment.py
# =============================================================================


def section_openserp(config, grok_client):
    """Test OpenSERP using production code path."""
    print("\n" + "=" * 70)
    print("OpenSERP (Portraits + Awards + Equipment)")
    print("=" * 70)

    import requests

    openserp_url = config.get("supplemental_material", {}).get(
        "openserp_url", config.get("openserp", {}).get("url", "http://localhost:7001")
    )

    try:
        requests.get(f"{openserp_url}/health", timeout=3)
        print(f"  OpenSERP at {openserp_url}: ✅ online")
        print("  Waiting 20s for headless Chrome warm-up...")
        time.sleep(20)
    except Exception:
        print(f"  OpenSERP not available at {openserp_url} — SKIPPING")
        return

    from src.enrichment.openserp_enrichment import (
        search_person_images,
        search_military_awards,
        search_equipment_images,
    )

    # Portrait images — real people (no limit, search until Grok-confirmed)
    people = _sample_entities(
        "people", count=9999,
        filter_fn=lambda d: len(d.get("name", "")) > 5,
    )
    if not people:
        people = [{"name": "Dwight D. Eisenhower"}, {"name": "George S. Patton"}]

    print("\n  Portrait Images (search_person_images, until Grok-confirmed):")
    found_portrait = False
    for p in people:
        if found_portrait:
            break
        name = p.get("name", "Unknown")
        from src.utils.search_query_loader import render_search_queries
        queries = render_search_queries("people", "portrait_images", name=name)
        print(f"    {name} (query: {queries[0] if queries else 'N/A'})")
        results = search_person_images(name, openserp_url, grok_client)
        print(f"      {len(results)} Grok-confirmed results")
        for r in results[:2]:
            print(f"      → {r.get('title', '')[:50]} | {r.get('url', '')[:50]}")
        if results:
            found_portrait = True
            _confirmed_urls.append({"section": "OpenSERP Portrait", "name": name, "url": results[0].get("url", "")})
        time.sleep(2)
    if not found_portrait:
        print("    ⚠ No Grok-confirmed portrait found")

    # Military awards (keep searching until Grok-confirmed find)
    print("\n  Military Awards (search_military_awards, until Grok-confirmed):")
    found_awards = False
    for p in people:
        if found_awards:
            break
        name = p.get("name", "Unknown")
        print(f"    {name} (query: {name} WWII)")
        results = search_military_awards(name, openserp_url, grok_client)
        print(f"      {len(results)} Grok-confirmed results")
        for r in results[:2]:
            print(f"      → {r.get('title', '')[:50]}")
        if results:
            found_awards = True
            _confirmed_urls.append({"section": "OpenSERP Awards", "name": name, "url": results[0].get("url", "")})
        time.sleep(2)
    if not found_awards:
        print("    ⚠ No Grok-confirmed awards found")

    # Valor searches (US personnel only, keep searching until find)
    from src.enrichment.openserp_enrichment import search_valor

    us_people = [p for p in people if p.get("nationality") == "USA"]
    if not us_people:
        us_people = people[:20]
    print("\n  Valor Databases (US personnel, search_valor, until first find):")
    found_valor = False
    for p in us_people:
        if found_valor:
            break
        name = p.get("name", "Unknown")
        print(f"    {name}")
        print(f"      queries: {name} valor militarytimes | {name} valor defense.gov")
        results = search_valor(name, openserp_url, grok_client)
        print(f"      {len(results)} results")
        for r in results[:3]:
            print(f"      → {r.get('url', '')[:70]}")
        if results:
            found_valor = True
            _confirmed_urls.append({"section": "Valor", "name": name, "url": results[0].get("url", "")})
        time.sleep(2)
    if not found_valor:
        print("    ⚠ No valor results found")

    # Equipment images (keep searching until Grok-confirmed find)
    equipment = _sample_entities(
        "equipment", count=9999,
        filter_fn=lambda d: len(d.get("name", d.get("equipment_name", ""))) > 3,
    )
    if not equipment:
        equipment = [{"name": "M4 Sherman"}, {"name": "Tiger I"}]

    print("\n  Equipment Images (search_equipment_images, until Grok-confirmed):")
    found_equip = False
    for e in equipment:
        if found_equip:
            break
        name = e.get("name", e.get("equipment_name", "Unknown"))
        print(f"    {name} (query: {name} WWII military equipment photo)")
        results = search_equipment_images(name, openserp_url, grok_client)
        print(f"      {len(results)} Grok-confirmed results")
        for r in results[:2]:
            print(f"      → {r.get('title', '')[:50]}")
        if results:
            found_equip = True
            _confirmed_urls.append({"section": "OpenSERP Equipment", "name": name, "url": results[0].get("url", "")})
        time.sleep(2)
    if not found_equip:
        print("    ⚠ No Grok-confirmed equipment images found")


# =============================================================================
# Wikipedia + Grokipedia — uses search_wikipedia, search_grokipedia from enrich_biographies.py
# =============================================================================


def section_wikipedia(config, grok_client):
    """Test Wikipedia/Grokipedia using production code path."""
    print("\n" + "=" * 70)
    print("Wikipedia + Grokipedia (People Enrichment)")
    print("=" * 70)

    from src.extraction.enrich_biographies import (
        search_grokipedia,
        search_wikipedia,
        _build_wikipedia_request,
        _extract_page_content,
        get_wikipedia_image,
    )

    # Sample real people — no limit, keep going until positive
    people = _sample_entities(
        "people", count=9999,
        filter_fn=lambda d: d.get("enrichment_status") == "not_found"
        and len(d.get("name", "")) > 3,
    )
    if not people:
        people = [{"name": "Dwight D. Eisenhower"}, {"name": "George S. Patton"}, {"name": "Walter Model"}]

    print(f"\n  Searching {len(people)} people via search_wikipedia() + search_grokipedia() (until first success):\n")
    print("  (Clearing wikipedia/grokipedia cache for test entries...)")
    for person_data in people:
        person = person_data.get("name", "Unknown")
        import hashlib
        for source in ("wikipedia", "grokipedia"):
            h = hashlib.sha256(f"{source}:{person}".encode()).hexdigest()[:16]
            cache_file = Path(f"cache/search_cache/search#{source}#{h}.json")
            if cache_file.exists():
                cache_file.unlink()
    print()
    found_wiki = False
    found_grok = False
    for person_data in people:
        if found_wiki and found_grok:
            break
        person = person_data.get("name", "Unknown")
        print(f"  {person}:")

        # Grokipedia first — no rate limit, provides full name hint for Wikipedia
        if not found_grok:
            time.sleep(3)
            content = search_grokipedia(person)
            if content:
                import re
                page_links = re.findall(r'href="/page/([^"]+)"', content)
                name_parts = [p for p in person.split() if len(p) > 2 and not p.endswith(".")]
                last_name = name_parts[-1].lower() if name_parts else ""
                first_initial = ""
                for p in person.split():
                    if p[0].isupper():
                        first_initial = p[0].lower()
                        break
                matched = []
                for p in page_links:
                    p_lower = p.lower().replace("%2c", ",").replace("_", " ")
                    if last_name in p_lower:
                        if first_initial and not p_lower.split()[0].startswith(first_initial):
                            continue
                        matched.append(f"https://grokipedia.com/page/{p}")
                if matched:
                    print(f"    Grokipedia: ✅ ({len(matched)} pages)")
                    for link in matched[:3]:
                        print(f"      → {link}")
                    found_grok = True
                    _confirmed_urls.append({"section": "Grokipedia", "name": person, "url": matched[0]})
                else:
                    print(f"    Grokipedia: ❌ no matching pages")
            else:
                print(f"    Grokipedia: ❌ no results")

        # Wikipedia — uses Grokipedia slug as hint if available
        if not found_wiki:
            content = search_wikipedia(person)
            if content:
                from urllib.parse import quote
                first_paren = content.find("(")
                real_name = content[:first_paren].strip() if first_paren > 0 else person
                wiki_url = f"https://en.wikipedia.org/wiki/{quote(real_name.replace(' ', '_'))}"
                print(f"    Wikipedia: ✅ ({len(content)} chars) {wiki_url}")
                print(f"      {content[:120]}...")
                wiki_img = get_wikipedia_image(person)
                if wiki_img:
                    print(f"      📷 Portrait: {wiki_img['url']}")
                    print(f"         License: {wiki_img['license']}")
                found_wiki = True
                _confirmed_urls.append({"section": "Wikipedia", "name": person, "url": wiki_url})
            else:
                # Check if it was rate-limited vs genuinely not found
                import requests as _req
                _resp = _req.get("https://en.wikipedia.org/w/api.php",
                    params={"action": "query", "format": "json", "meta": "siteinfo"},
                    headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"},
                    timeout=5)
                if _resp.status_code == 429:
                    print(f"    Wikipedia: ⚠ RATE LIMITED (429)")
                else:
                    print(f"    Wikipedia: ❌ searched, no match")

        print()
        time.sleep(10)  # Rate limit between people (Wikipedia throttles aggressively)

    if not found_wiki:
        print("  ⚠ No Wikipedia results found")
    if not found_grok:
        print("  ⚠ No Grokipedia results found")


# =============================================================================
# Main
# =============================================================================


def main():
    parser = argparse.ArgumentParser(description="Debug external search integrations")
    parser.add_argument("--section", default="all",
                        choices=["all", "nara", "archive", "gutenberg", "openserp", "wikipedia"],
                        help="Which section to run")
    args = parser.parse_args()

    config = load_config()
    cache_dir = Path("cache/api")
    cache_dir.mkdir(parents=True, exist_ok=True)
    grok_client = GrokClient(cache_dir)

    # Tee to log
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    log_fh = open(LOG_FILE, "w", encoding="utf-8")
    sys.stdout = Tee(sys.__stdout__, log_fh)

    print("=" * 70)
    print("Search Integration Debug")
    print(f"Time: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 70)

    sections = {
        "wikipedia": section_wikipedia,
        "nara": section_nara,
        "archive": section_archive_org,
        "gutenberg": section_gutenberg,
        "openserp": section_openserp,
    }

    if args.section == "all":
        for fn in sections.values():
            fn(config, grok_client)
    else:
        sections[args.section](config, grok_client)

    print("\n" + "=" * 70)
    print("CONFIRMED URLs (spot-check these)")
    print("=" * 70)
    if _confirmed_urls:
        for entry in _confirmed_urls:
            print(f"  [{entry['section']}] {entry['name']}")
            print(f"    {entry['url']}")
    else:
        print("  ⚠ No confirmed URLs in this run")
    print()
    print("=" * 70)
    print(f"Log saved to: {LOG_FILE}")
    print("=" * 70)


if __name__ == "__main__":
    main()

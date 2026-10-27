"""
OpenSERP integration for external maps search.

Uses real search engines (Google, Bing, DuckDuckGo) via OpenSERP
instead of asking Grok to search (which causes hallucinations).
"""

import json
import logging
import subprocess
from pathlib import Path
from typing import List, Dict, Any, Optional
from datetime import datetime
from urllib.parse import urlparse

import ulid

from src.grok_client import GrokClient
from src.extraction.search_external_maps import _verify_map_relevance

# User-Agent to avoid bot detection
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

logger = logging.getLogger(__name__)


def _is_trusted_domain(domain: str) -> bool:
    """Check if domain is trusted (.gov or .edu)."""
    return domain.endswith(".gov") or domain.endswith(".edu")


def _find_license_link(
    html: str, base_url: str, page_timeout: int, headers: dict
) -> tuple[str, str]:
    """Find and fetch license page from HTML links. Returns (license_text, license_url)."""
    import re

    license_link_patterns = [
        r'href=["\']([^"\']*(?:copyright|license|terms)[^"\']*)["\']',
    ]

    for pattern in license_link_patterns:
        matches = re.findall(pattern, html, re.IGNORECASE)
        if matches:
            link = matches[0]
            if not link.startswith("http"):
                link = f"{base_url}/{link.lstrip('/')}"
            try:
                from src.utils.url_hygiene import safe_get

                response = safe_get(link, timeout=page_timeout, headers=headers)
                if response is not None and response.status_code == 200:
                    logger.info(f"   📄 Found license page via link: {link}")
                    return response.text[:10000], link
            except Exception:  # nosec B112
                continue

    return "", ""


def _try_common_license_paths(
    base_url: str, page_timeout: int, headers: dict
) -> tuple[str, str]:
    """Try common license page paths. Returns (license_text, license_url)."""
    license_paths = [
        "/terms",
        "/terms-of-use",
        "/license",
        "/copyright",
        "/about/terms",
        "/legal",
        "/terms-and-conditions",
    ]

    for path in license_paths:
        try:
            from src.utils.url_hygiene import safe_get

            response = safe_get(
                f"{base_url}{path}", timeout=page_timeout, headers=headers
            )
            if response is not None and response.status_code == 200:
                logger.info(f"   📄 Found license page: {path}")
                return response.text[:10000], f"{base_url}{path}"
        except Exception:  # nosec B112
            continue

    return "", ""


def _extract_footer_license(html: str) -> str:
    """Extract license info from page footer/copyright sections."""
    import re

    patterns = [
        r"(?i)<footer.*?>(.*?)</footer>",
        r"(?i)copyright.*?(?=<|$).{0,500}",
        r"(?i)license.*?(?=<|$).{0,500}",
        r"(?i)terms.*?(?=<|$).{0,500}",
    ]

    license_text = ""
    for pattern in patterns:
        matches = re.findall(pattern, html, re.DOTALL)
        if matches:
            license_text += " ".join(matches[:3])
            if len(license_text) > 2000:
                break

    return license_text


def _analyze_license_with_grok(
    domain: str, url: str, license_text: str, grok_client: GrokClient
) -> tuple[bool, str]:
    """Analyze license terms with Grok. Returns (allowed, license_url)."""
    from src.utils.prompt_loader import render_prompt

    prompt = render_prompt("license_check", url=url, page_content=license_text)

    result = grok_client.extract_json(
        prompt=prompt, cache_type="license_check", temperature=0.0
    )

    if isinstance(result, dict):
        allowed = result.get("allowed", False)
        reason = result.get("reason", "Unknown")
        license_type = result.get("license_type", "Unknown")

        logger.info(f"   📋 License: {license_type} - {reason}")

        if not allowed:
            _add_to_blacklist(domain, url)
            logger.warning(f"   🚫 Added {domain} to blacklist (license prohibits use)")

        return allowed, url

    return False, url


def _license_from_page_links(
    url: str, base_url: str, page_timeout: int, headers: dict
) -> tuple:
    """Try license page via links on the page. Returns (text, license_url) or ("", "")."""
    try:
        from src.utils.url_hygiene import safe_get

        response = safe_get(url, timeout=page_timeout, headers=headers)
        if response is not None and response.status_code == 200:
            text, found_url = _find_license_link(
                response.text, base_url, page_timeout, headers
            )
            if text:
                return text, found_url
    except Exception:  # nosec B110
        pass
    return "", ""


def _license_from_footer(url: str, page_timeout: int, headers: dict) -> tuple:
    """Check main page footer for license text. Returns (text, url) or ("", "")."""
    try:
        from src.utils.url_hygiene import safe_get

        response = safe_get(url, timeout=page_timeout, headers=headers)
        if response is not None and response.status_code == 200:
            text = _extract_footer_license(response.text)
            if text:
                return text, url
    except Exception:  # nosec B110
        pass
    return "", ""


def _find_license_text(url: str, page_timeout: int, headers: dict) -> tuple:
    """Try to find license text from a URL. Returns (text, license_url)."""
    base_url = f"{urlparse(url).scheme}://{urlparse(url).netloc}"

    # Try license page via links on the page
    text, found_url = _license_from_page_links(url, base_url, page_timeout, headers)
    if text:
        return text, found_url

    # Try common paths
    text, found_url = _try_common_license_paths(base_url, page_timeout, headers)
    if text:
        return text, found_url or url

    # Check main page footer
    text, found_url = _license_from_footer(url, page_timeout, headers)
    if text:
        return text, found_url

    return "", url


def _check_license_terms(
    url: str, grok_client: GrokClient, page_timeout: int = 10
) -> tuple[bool, str]:
    """Check if website allows non-commercial use of images.

    Args:
        url: URL to check
        grok_client: Grok API client
        page_timeout: Timeout for page download

    Returns: (allowed, license_url) - True if non-commercial use allowed, and URL of license page
    """
    try:
        domain = urlparse(url).netloc

        if _is_trusted_domain(domain):
            logger.info(f"   ✅ Trusted domain ({domain}) - skipping license check")
            return True, url

        headers = {"User-Agent": USER_AGENT}
        license_text, license_url = _find_license_text(url, page_timeout, headers)

        if not license_text or len(license_text) < 50:
            logger.info(f"   ✅ No license found for {domain} - assuming public domain")
            return True, url

        return _analyze_license_with_grok(domain, url, license_text, grok_client)

    except Exception as e:
        logger.warning(f"   ⚠️  License check failed: {e}")
        return False, url


def _add_to_blacklist(domain: str, url: str = "") -> None:
    """Add domain to blacklist file with comment showing the URL."""
    blacklist_file = Path("config/domain_blacklist.yaml")

    try:
        # Read entire file
        if not blacklist_file.exists():
            return

        with open(blacklist_file) as f:
            lines = f.readlines()

        # Check if domain already exists
        if any(f"- {domain}\n" in line for line in lines):
            return  # Already blacklisted

        # Find the blacklist section and insert before source_material_paths
        insert_index = None
        for i, line in enumerate(lines):
            if line.startswith("source_material_paths:"):
                insert_index = i
                break

        if insert_index is None:
            # No source_material_paths, append to end
            insert_index = len(lines)

        # Insert domain and comment
        lines.insert(insert_index, f"- {domain}\n")
        if url:
            lines.insert(insert_index + 1, f"# Blacklisted: {url} (license rejected)\n")

        # Write back
        with open(blacklist_file, "w") as f:
            f.writelines(lines)

        logger.info(f"   ✅ Added {domain} to domain_blacklist.yaml")

    except Exception as e:
        logger.error(f"   ❌ Failed to update blacklist: {e}")


def search_with_openserp(
    place_name: str,
    date: Optional[str],
    limit: int = 50,
    openserp_url: str = "http://localhost:7001",
) -> List[Dict[str, Any]]:
    """Search for maps using OpenSERP (real search engines).

    Returns: List of search results with url, title, description, engine
    """
    try:
        # Build search query
        year = ""
        if date:
            year = date.split("-")[0]
        query = f'WWII map "{place_name}" {year}'.strip()

        # Log the search URL
        import urllib.parse

        encoded_query = urllib.parse.quote(query)
        search_url = f"{openserp_url}/mega/search?text={encoded_query}&engines=google,bing,duckduckgo&limit={limit}"
        logger.info(f"   OpenSERP URL: {search_url}")

        # Run Go search tool
        cmd = [
            "./tools/search_maps",
            "-place",
            place_name,
            "-limit",
            str(limit),
            "-openserp",
            openserp_url,
        ]

        if date:
            cmd.extend(["-date", date])

        result = subprocess.run(  # nosec B603 B404
            cmd,
            capture_output=True,
            text=True,
            timeout=120,  # OpenSERP can take 40-60 seconds per search
        )

        if result.returncode != 0:
            logger.warning(f"OpenSERP search failed: {result.stderr}")
            return []

        return json.loads(result.stdout)

    except subprocess.TimeoutExpired:
        logger.warning(f"OpenSERP search timed out for {place_name} (>120s)")
        return []
    except Exception as e:  # pylint: disable=broad-exception-caught
        logger.warning(f"OpenSERP search error: {e}")
        return []


def _get_event_context(place_data: dict) -> tuple[str, Optional[str]]:
    """Extract event context and date from place data. Returns (event_context, date)."""
    event_mentions = place_data.get("event_mentions", [])
    event_context = "WWII operations"
    date = None

    if event_mentions:
        first_mention = event_mentions[0]
        event_name = first_mention.get("Event_Name", "")
        sub_event_name = first_mention.get("Sub_event_Name", "")
        if event_name and sub_event_name:
            event_context = f"{event_name} - {sub_event_name}"

    return event_context, date


def _check_duplicate_map(url: str, output_dir: Path) -> bool:
    """Check if map URL already exists in output. Returns True if duplicate."""
    existing = list(output_dir.glob("*.json"))
    for existing_file in existing:
        if existing_file.name in ("index.json", "duplicate_report.json"):
            continue
        try:
            with open(existing_file) as f:
                existing_data = json.load(f)
        except (OSError, json.JSONDecodeError):
            continue  # malformed/non-record file -> skip (don't drop the whole place)
        if existing_data.get("external_source_url") == url:
            return True
    return False


def _download_map_image(
    url: str,
    map_id: str,
    image_storage_path: str,
    page_timeout: int,
    image_timeout: int,
    approved_image_url: Optional[str] = None,
) -> tuple[bool, Optional[str]]:
    """Download map image to storage. Returns (success, image_path).

    ``approved_image_url`` is the exact image the vision verifier approved — download THAT (not a
    re-extracted map_images[0], which could be a different/unverified image on the page). Only
    re-extract (and take the first) when no approved url is threaded (back-compat)."""
    try:
        from src.extraction.images import fetch_image_bytes

        storage_dir = Path(image_storage_path)
        storage_dir.mkdir(parents=True, exist_ok=True)

        if approved_image_url:
            img_url = approved_image_url
        else:
            from src.extraction.search_external_maps import _extract_map_images
            from src.utils.url_hygiene import safe_get

            headers = {"User-Agent": USER_AGENT}
            page_response = safe_get(url, timeout=page_timeout, headers=headers)
            if page_response is None or page_response.status_code != 200:
                logger.info(
                    "   ⚠ map page fetch yielded no content (blocked/non-200): %s", url
                )
                return False, None
            map_images = _extract_map_images(
                page_response.text, url, page_has_map_keyword=True
            )
            if not map_images:
                logger.info("   ⚠ no map images found on page: %s", url)
                return False, None
            img_url = map_images[0]["url"]

        # SSRF-safe image fetch (clean_url + resolved-host + no-redirect + size/content checks).
        img_bytes, ext = fetch_image_bytes(img_url)
        if not img_bytes:
            return False, None
        ext = (ext or "jpg").lstrip(".")
        if ext not in ["jpg", "jpeg", "png", "gif"]:
            ext = "jpg"

        image_filename = f"{map_id}.{ext}"
        image_file_path = storage_dir / image_filename
        image_file_path.write_bytes(img_bytes)

        logger.info(f"   💾 Saved image: {image_filename}")
        return True, str(image_file_path)

    except Exception as e:
        logger.warning(f"   ⚠️  Failed to download image: {e}")
        return False, None


def _create_map_record(
    map_id: str,
    result: dict,
    place_data: dict,
    place_name: str,
    date: Optional[str],
    image_downloaded: bool,
    image_path: Optional[str],
    event_id: Optional[str] = None,
    event_name: Optional[str] = None,
    sub_event_id: Optional[str] = None,
    sub_event_name: Optional[str] = None,
) -> dict:
    """Create map record dictionary."""
    # Detect file format from image path
    file_format = None
    if image_path:
        ext = Path(image_path).suffix.lstrip(".")
        if ext in ("jpg", "jpeg", "png", "tif", "gif", "pdf"):
            file_format = "jpg" if ext == "jpeg" else ext

    return {
        "MapID": map_id,
        "map_title": result["title"],
        "external_source": result["engine"].title(),
        "external_source_url": result["url"],
        "archive_id": None,
        "license": "Unknown",
        "license_url": None,
        "date_created": None,
        "creator": None,
        "EventID": event_id,
        "Event_Name": event_name,
        "Sub_eventID": sub_event_id,
        "Sub_event_Name": sub_event_name,
        "place_name": place_name,
        "PlaceMentionID": None,
        "date": date,
        "DateMentionID": None,
        "local_path": f"output/external_maps/{map_id}.json",
        "local_image_path": image_path,
        "source_url": result.get("url"),
        "file_format": file_format,
        "extracted_date": datetime.utcnow().isoformat() + "Z",
        "description": result.get("description", ""),
        "map_type": None,
        "storage_backend": "filesystem",
        "found_via": f"OpenSERP {result['engine']} search",
        "found_date": datetime.utcnow().strftime("%Y-%m-%d"),
    }


def _process_search_result(
    result: dict,
    place_name: str,
    place_data: dict,
    date: Optional[str],
    event_context: str,
    grok_client: GrokClient,
    page_timeout: int,
    image_timeout: int,
    image_storage_path: Optional[str],
    output_dir: Path,
    downloaded_urls: set,
    event_id: Optional[str] = None,
    event_name: Optional[str] = None,
    sub_event_id: Optional[str] = None,
    sub_event_name: Optional[str] = None,
) -> tuple[bool, int]:
    """Process a single search result. Returns (imported, skipped_count)."""
    url = result["url"]
    title = result["title"]
    description = result.get("description", "")

    # Skip if already downloaded
    if url in downloaded_urls:
        logger.info(f"   ⏭️  Already downloaded: {title[:60]}...")
        return False, 1

    logger.info(f"   🔍 Verifying: {title[:60]}...")

    # Verify with Grok
    is_relevant, is_government_map, approved_image_url, depicted = (
        _verify_map_relevance(
            map_url=url,
            map_title=title,
            map_description=description,
            place_name=place_name,
            date=date,
            event_context=event_context,
            grok_client=grok_client,
            page_timeout=page_timeout,
            image_timeout=image_timeout,
        )
    )

    if not is_relevant:
        logger.info(f"   ⚠️  Rejected by verification")
        return False, 0

    # Check license (skip if government map)
    if not is_government_map:
        license_allowed, _ = _check_license_terms(
            url=url, grok_client=grok_client, page_timeout=page_timeout
        )
        if not license_allowed:
            logger.info(f"   ⚠️  Rejected by license check")
            return False, 0
    else:
        logger.info(f"   ✓ Government map - skipping license check")

    # Check for duplicates
    if _check_duplicate_map(url, output_dir):
        logger.info(f"   ⚠️  Already imported")
        return False, 0

    # Generate MapID
    map_id = str(ulid.new())

    # Download image if configured
    image_downloaded = False
    image_path = None
    if image_storage_path:
        image_downloaded, image_path = _download_map_image(
            url,
            map_id,
            image_storage_path,
            page_timeout,
            image_timeout,
            approved_image_url=approved_image_url,
        )

    # Create and save map record
    map_record = _create_map_record(
        map_id,
        result,
        place_data,
        place_name,
        date,
        image_downloaded,
        image_path,
        event_id,
        event_name,
        sub_event_id,
        sub_event_name,
    )

    output_file = output_dir / f"{map_record['MapID']}.json"
    from src.utils.file_lock import write_json_with_lock

    # external_maps records ARE maps records (MapID + MAPS_OUTPUT_SCHEMA). The guard resolves the
    # entity from the PATH and now ALIASES output/external_maps/ -> the 'maps' schema (see
    # _resolve_entity_type), so this write is schema-validated + counted by validation_stats under
    # 'maps'; entity="maps" additionally stamps the correct version. Honor the return: a blocked
    # write must not silently persist-nothing-while-reporting-success.
    if not write_json_with_lock(output_file, map_record, entity="maps"):
        logger.warning(
            "OBSERV external_maps.write_blocked map=%s — schema guard refused",
            map_record.get("MapID"),
        )
        return False, 0

    logger.info(f"   ✅ Imported: {title[:60]}")
    downloaded_urls.add(url)
    # Recycle the approved map as a SOURCE DOCUMENT (map-as-source): render the vision `depicted`
    # block into a parsed-source under contentrepository/ so the map re-enters the normal pipeline
    # (Phase 1->2->3) and an EVENT (+ people_groups/places/dates) is derived from it. Fail-safe +
    # gated on depicted being present (no depicted -> catalog record only, no event derivation).
    _recycle_map_as_source(
        map_id=map_record["MapID"],
        map_title=title,
        approved_image_url=approved_image_url or "",
        depicted=depicted,
        source_url=url,
    )
    return True, 0


def _recycle_map_as_source(
    *,
    map_id: str,
    map_title: str,
    approved_image_url: str,
    depicted: Any,
    source_url: str,
) -> bool:
    """Write an approved map's vision ``depicted`` as a RAW SOURCE (meta.yaml + content.md) under
    ``contentrepository/map-<id>/`` so it re-enters the normal pipeline via the content-uploaded
    front door (Phase 1 parse -> Phase 2 event extraction -> Phase 3 enrich), exactly like a book
    chapter (map-as-source P1, spec §3.1 Option B). Fail-safe: never raises; returns True iff a
    source was written. Gated on `depicted` being present (no depicted -> catalog record only).
    """
    try:
        if not isinstance(depicted, dict) or not depicted:
            logger.info(
                "OBSERV map_source.skipped map=%s reason=no_depicted "
                "(catalog record only, no event derivation)",
                map_id,
            )
            return False
        from src.extraction.map_source_adapter import (
            _safe_map_id,
            render_map_markdown,
            render_map_meta_yaml,
        )

        md = render_map_markdown(map_title, depicted, source_url=source_url)
        if not md:
            logger.info(
                "OBSERV map_source.skipped map=%s reason=empty_depicted", map_id
            )
            return False

        safe_id = _safe_map_id(map_id)
        base = Path("contentrepository") / f"map-{safe_id}"
        base.mkdir(parents=True, exist_ok=True)
        # Mirror a book chapter's source pair so Phase-1 parse consumes it unchanged.
        (base / f"map-{safe_id}-meta.yaml").write_text(
            render_map_meta_yaml(map_id, map_title, source_url=source_url),
            encoding="utf-8",
        )
        (base / f"map-{safe_id}a-content.md").write_text(md, encoding="utf-8")
        logger.info(
            "OBSERV map_source.recycled map=%s source=contentrepository/map-%s/ "
            "(re-enters Phase 1->2->3 for event derivation)",
            map_id,
            safe_id,
        )
        return True
    except Exception as e:  # noqa: BLE001 - recycle must never crash the map import
        logger.warning("OBSERV map_source.error map=%s reason=%s", map_id, str(e)[:120])
        return False


def _is_map_derived_only(event_mentions: Any) -> bool:
    """True iff a place's provenance is ENTIRELY map-derived (every event_mention's book is
    'map-*'). Such places must not seed new map searches (map-as-source loop guard). A place with
    any prose source (or no mentions) returns False and searches normally."""
    ems = event_mentions or []
    books = [m.get("book") for m in ems if isinstance(m, dict)]
    return bool(books) and all(
        isinstance(b, str) and b.startswith("map-") for b in books
    )


def _event_fields(place_data: dict) -> tuple:
    """First event_mention's (context, date, EventID, Event_Name, Sub_eventID, Sub_event_Name)."""
    ems = place_data.get("event_mentions", []) or []
    if not ems:
        return "WWII operations", None, None, None, None, None
    first = ems[0]
    en = first.get("Event_Name", "")
    sn = first.get("Sub_event_Name", "")
    ctx = f"{en} - {sn}" if en and sn else "WWII operations"
    return (
        ctx,
        None,
        first.get("EventID"),
        en,
        first.get("Sub_eventID"),
        sn,
    )


def _process_place_file(
    place_file: Path,
    idx: int,
    total: int,
    history,
    skip_searched: bool,
    search_limit: int,
    openserp_url: str,
    grok_client: GrokClient,
    page_timeout: int,
    image_timeout: int,
    image_storage_path: Optional[str],
    output_dir: Path,
    downloaded_urls: set,
) -> tuple:
    """Process a single place file for map search. Returns (imported, skipped_searched, skipped_downloaded)."""
    with open(place_file) as f:
        place_data = json.load(f)

    place_name = place_data.get("current_name", "")
    if not place_name:
        return 0, 0, 0

    # LOOP GUARD (map-as-source): a place whose provenance is ONLY a map source must NOT seed new
    # map searches (else map -> place -> map -> ... recursion). Prose-sourced places search normally.
    if _is_map_derived_only(place_data.get("event_mentions")):
        logger.info(
            "OBSERV map_source.search_skipped place=%s reason=map_derived_only "
            "(loop guard: a map-derived place does not seed new map searches)",
            place_name,
        )
        return 0, 1, 0

    if skip_searched and history.has_searched(place_name):
        logger.info(f"[{idx}/{total}] {place_name} - ⏭️  Previously searched")
        return 0, 1, 0

    logger.info(f"[{idx}/{total}] {place_name}")

    event_context, date, event_id, event_name, sub_event_id, sub_event_name = (
        _event_fields(place_data)
    )

    results = search_with_openserp(
        place_name, date, limit=search_limit, openserp_url=openserp_url
    )
    history.mark_searched(place_name)

    if not results:
        logger.info("   ⚠ No results from OpenSERP")
        return 0, 0, 0

    logger.info(f"   ✓ Found {len(results)} potential map(s) from OpenSERP")

    imported = 0
    skipped_downloaded = 0
    for result in results:
        was_imported, skipped = _process_search_result(
            result,
            place_name,
            place_data,
            date,
            event_context,
            grok_client,
            page_timeout,
            image_timeout,
            image_storage_path,
            output_dir,
            downloaded_urls,
            event_id,
            event_name,
            sub_event_id,
            sub_event_name,
        )
        if was_imported:
            imported += 1
        skipped_downloaded += skipped

    return imported, 0, skipped_downloaded


def import_openserp_maps(
    places_dir: Path,
    output_dir: Path,
    grok_client: GrokClient,
    max_places: Optional[int] = None,
    search_limit: int = 50,
    openserp_url: str = "http://localhost:7001",
    page_timeout: int = 10,
    image_timeout: int = 30,
    image_storage_path: Optional[str] = None,
    skip_searched: bool = True,
) -> int:
    """Search for maps using OpenSERP and import verified results.

    Args:
        places_dir: Directory containing place JSON files
        output_dir: Directory to save imported maps
        grok_client: Grok API client for verification
        max_places: Maximum number of places to search (None for all)
        search_limit: Maximum results per search
        openserp_url: OpenSERP service URL
        page_timeout: Timeout for page downloads (seconds)
        image_timeout: Timeout for image downloads (seconds)
        image_storage_path: Path to store downloaded images (if enabled)
        skip_searched: Skip places that have been searched before

    Returns: Number of maps imported
    """
    from src.extraction.search_history import SearchHistory

    output_dir.mkdir(parents=True, exist_ok=True)

    # Initialize search history
    history = SearchHistory()
    downloaded_urls = history.get_downloaded_urls(output_dir)

    if skip_searched:
        logger.info(
            f"Search history: {len(history.searched_places)} places previously searched"
        )
    if downloaded_urls:
        logger.info(
            f"Found {len(downloaded_urls)} previously downloaded URLs (will skip)"
        )

    # Get all places
    place_files = sorted(places_dir.glob("*.json"))
    total_places = len(place_files)

    if max_places:
        place_files = place_files[:max_places]
        logger.info(
            f"Searching maps for {len(place_files)} of {total_places} places via OpenSERP (limited for testing)..."
        )
    else:
        logger.info(f"Searching maps for {len(place_files)} places via OpenSERP...")

    imported = 0
    skipped_searched = 0
    skipped_downloaded = 0

    for idx, place_file in enumerate(place_files, 1):
        try:
            result_imported, result_skipped_s, result_skipped_d = _process_place_file(
                place_file,
                idx,
                len(place_files),
                history,
                skip_searched,
                search_limit,
                openserp_url,
                grok_client,
                page_timeout,
                image_timeout,
                image_storage_path,
                output_dir,
                downloaded_urls,
            )
            imported += result_imported
            skipped_searched += result_skipped_s
            skipped_downloaded += result_skipped_d
        except Exception as e:
            logger.warning(f"Error processing {place_file.name}: {e}")
            continue

    # Summary
    if skipped_searched > 0:
        logger.info(f"\n⏭️  Skipped {skipped_searched} previously searched place(s)")
    if skipped_downloaded > 0:
        logger.info(f"⏭️  Skipped {skipped_downloaded} previously downloaded URL(s)")

    return imported

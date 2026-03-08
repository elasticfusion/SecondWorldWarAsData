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
import requests

from src.grok_client import GrokClient
from src.extraction.search_external_maps import _verify_map_relevance

# User-Agent to avoid bot detection
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

logger = logging.getLogger(__name__)


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

        # Skip license check for .gov and .edu domains (public/educational)
        if domain.endswith(".gov") or domain.endswith(".edu"):
            logger.info(f"   ✅ Trusted domain ({domain}) - skipping license check")
            return True, url

        base_url = f"{urlparse(url).scheme}://{domain}"

        # Common license/terms pages
        license_paths = [
            "/terms",
            "/terms-of-use",
            "/license",
            "/copyright",
            "/about/terms",
            "/legal",
            "/terms-and-conditions",
        ]

        headers = {"User-Agent": USER_AGENT}
        license_text = ""
        license_url = url  # Default to map page URL

        # First, check main page for license links
        try:
            response = requests.get(
                url, timeout=page_timeout, headers=headers, allow_redirects=True
            )
            if response.status_code == 200:
                html = response.text
                import re

                # Look for links to copyright/license pages
                license_link_patterns = [
                    r'href=["\']([^"\']*(?:copyright|license|terms)[^"\']*)["\']',
                ]
                for pattern in license_link_patterns:
                    matches = re.findall(pattern, html, re.IGNORECASE)
                    if matches:
                        # Try first match
                        link = matches[0]
                        if not link.startswith("http"):
                            link = f"{base_url}/{link.lstrip('/')}"
                        try:
                            lic_response = requests.get(
                                link,
                                timeout=page_timeout,
                                headers=headers,
                                allow_redirects=True,
                            )
                            if lic_response.status_code == 200:
                                license_text = lic_response.text[:10000]
                                license_url = link  # Track the license page URL
                                logger.info(
                                    f"   📄 Found license page via link: {link}"
                                )
                                break
                        except Exception:  # nosec B112
                            continue  # Skip invalid links
        except Exception:  # nosec B110
            pass  # License extraction is optional

        # Try common paths if no link found
        if not license_text:
            for path in license_paths:
                try:
                    response = requests.get(
                        f"{base_url}{path}",
                        timeout=page_timeout,
                        headers=headers,
                        allow_redirects=True,
                    )
                    if response.status_code == 200:
                        license_text = response.text[:10000]  # First 10KB
                        license_url = f"{base_url}{path}"  # Track the license page URL
                        logger.info(f"   📄 Found license page: {path}")
                        break
                except Exception:  # nosec B112
                    continue  # Try next path

        # If no dedicated license page, check main page footer
        if not license_text:
            try:
                response = requests.get(
                    url, timeout=page_timeout, headers=headers, allow_redirects=True
                )
                if response.status_code == 200:
                    # Extract potential license info from page
                    html = response.text
                    import re

                    # Look for copyright/license sections
                    patterns = [
                        r"(?i)<footer.*?>(.*?)</footer>",
                        r"(?i)copyright.*?(?=<|$).{0,500}",
                        r"(?i)license.*?(?=<|$).{0,500}",
                        r"(?i)terms.*?(?=<|$).{0,500}",
                    ]
                    for pattern in patterns:
                        matches = re.findall(pattern, html, re.DOTALL)
                        if matches:
                            license_text += " ".join(matches[:3])
                            if len(license_text) > 2000:
                                break
            except Exception:  # nosec B110
                pass  # Footer extraction is optional

        if not license_text or len(license_text) < 50:
            logger.info(f"   ✅ No license found for {domain} - assuming public domain")
            return True, url

        # Ask Grok to analyze license terms
        prompt = f"""Analyze this website's terms/license to determine if non-commercial use of images is allowed.

Domain: {domain}
URL: {url}

License/Terms text:
{license_text[:5000]}

Determine:
1. Does the site explicitly allow non-commercial use of images?
2. Does it use Creative Commons, Public Domain, or similar permissive license?
3. Does it prohibit commercial use but allow educational/research use?
4. Does it have "All Rights Reserved" or prohibit reproduction?

Respond with ONLY a JSON object:
{{"allowed": true or false, "reason": "Brief explanation of license terms", "license_type": "CC-BY, Public Domain, All Rights Reserved, etc."}}
"""

        result = grok_client.extract_json(
            prompt=prompt, cache_type="license_check", temperature=0.0
        )

        if isinstance(result, dict):
            allowed = result.get("allowed", False)
            reason = result.get("reason", "Unknown")
            license_type = result.get("license_type", "Unknown")

            logger.info(f"   📋 License: {license_type} - {reason}")

            if not allowed:
                # Add to blacklist with license page URL
                _add_to_blacklist(domain, license_url)
                logger.warning(
                    f"   🚫 Added {domain} to blacklist (license prohibits use)"
                )

            return allowed, license_url

        return False, url

    except Exception as e:
        logger.warning(f"   ⚠️  License check failed: {e}")
        return False, url


def _add_to_blacklist(domain: str, url: str = "") -> None:
    """Add domain to blacklist file with comment showing the URL."""
    blacklist_file = Path("domain_blacklist.yaml")

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
            with open(place_file) as f:
                place_data = json.load(f)

            place_name = place_data.get("current_name", "")
            if not place_name:
                continue

            # Skip if already searched
            if skip_searched and history.has_searched(place_name):
                logger.info(
                    f"[{idx}/{len(place_files)}] {place_name} - ⏭️  Previously searched"
                )
                skipped_searched += 1
                continue

            logger.info(f"[{idx}/{len(place_files)}] {place_name}")

            # Get event context
            event_mentions = place_data.get("event_mentions", [])
            event_context = "WWII operations"
            date = None

            if event_mentions:
                first_mention = event_mentions[0]
                event_name = first_mention.get("Event_Name", "")
                sub_event_name = first_mention.get("Sub_event_Name", "")
                if event_name and sub_event_name:
                    event_context = f"{event_name} - {sub_event_name}"

            # Search with OpenSERP
            results = search_with_openserp(
                place_name, date, limit=search_limit, openserp_url=openserp_url
            )

            # Mark as searched
            history.mark_searched(place_name)

            if not results:
                logger.info(f"   ⚠ No results from OpenSERP")
                continue

            logger.info(f"   ✓ Found {len(results)} potential map(s) from OpenSERP")

            # Verify and import each result
            for result in results:
                url = result["url"]
                title = result["title"]
                description = result.get("description", "")

                # Skip if already downloaded
                if url in downloaded_urls:
                    logger.info(f"   ⏭️  Already downloaded: {title[:60]}...")
                    skipped_downloaded += 1
                    continue

                logger.info(f"   🔍 Verifying: {title[:60]}...")

                # Verify with Grok first to check if it's a government map
                is_relevant, is_government_map = _verify_map_relevance(
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

                if not is_relevant:
                    logger.info(f"   ⚠️  Rejected by verification")
                    continue

                # Check license terms (skip if government map)
                if not is_government_map:
                    license_allowed, license_url = _check_license_terms(
                        url=url, grok_client=grok_client, page_timeout=page_timeout
                    )

                    if not license_allowed:
                        logger.info(f"   ⚠️  Rejected by license check")
                        continue
                else:
                    logger.info(f"   ✓ Government map - skipping license check")

                # Check for duplicates
                existing = list(output_dir.glob("*.json"))
                is_duplicate = False
                for existing_file in existing:
                    with open(existing_file) as f:
                        existing_data = json.load(f)
                    if existing_data.get("external_source_url") == url:
                        is_duplicate = True
                        break

                if is_duplicate:
                    logger.info(f"   ⚠️  Already imported")
                    continue

                # Generate MapID
                map_id = str(ulid.new())

                # Download image if storage path configured
                image_downloaded = False
                image_path = None
                if image_storage_path:
                    try:
                        import requests

                        storage_dir = Path(image_storage_path)
                        storage_dir.mkdir(parents=True, exist_ok=True)

                        # Get first image from the page
                        from src.extraction.search_external_maps import (
                            _extract_map_images,
                        )

                        headers = {"User-Agent": USER_AGENT}
                        page_response = requests.get(
                            url,
                            timeout=page_timeout,
                            headers=headers,
                            allow_redirects=True,
                        )

                        if page_response.status_code == 200:
                            map_images = _extract_map_images(
                                page_response.text, url, page_has_map_keyword=True
                            )
                            if map_images:
                                img_url = map_images[0]["url"]
                                img_response = requests.get(
                                    img_url,
                                    timeout=image_timeout,
                                    headers=headers,
                                    allow_redirects=True,
                                )
                                img_response.raise_for_status()

                                # Determine extension from content-type
                                content_type = img_response.headers.get(
                                    "content-type", "image/jpeg"
                                )
                                ext = content_type.split("/")[-1].split(";")[0]
                                if ext not in ["jpg", "jpeg", "png", "gif"]:
                                    ext = "jpg"

                                image_filename = f"{map_id}.{ext}"
                                image_file_path = storage_dir / image_filename
                                image_file_path.write_bytes(img_response.content)

                                image_downloaded = True
                                image_path = str(image_file_path)
                                logger.info(f"   💾 Saved image: {image_filename}")
                    except Exception as e:
                        logger.warning(f"   ⚠️  Failed to download image: {e}")

                # Import map
                map_record = {
                    "MapID": map_id,
                    "map_title": title,
                    "external_source": result["engine"].title(),
                    "external_source_url": url,
                    "description": result.get("description", ""),
                    "license": "Unknown",
                    "place_name": place_name,
                    "PlaceID": place_data.get("PlaceID"),
                    "date": date,
                    "found_via": f"OpenSERP {result['engine']} search",
                    "found_date": datetime.utcnow().strftime("%Y-%m-%d"),
                    "extracted_date": datetime.utcnow().isoformat() + "Z",
                    "image_downloaded": image_downloaded,
                    "image_path": image_path,
                }

                output_file = output_dir / f"{map_record['MapID']}.json"
                with open(output_file, "w") as f:
                    json.dump(map_record, f, indent=2)

                logger.info(f"   ✅ Imported: {title[:60]}")
                imported += 1

                # Add to downloaded URLs
                downloaded_urls.add(url)

        except Exception as e:
            logger.warning(f"Error processing {place_file.name}: {e}")
            continue

    # Summary
    if skipped_searched > 0:
        logger.info(f"\n⏭️  Skipped {skipped_searched} previously searched place(s)")
    if skipped_downloaded > 0:
        logger.info(f"⏭️  Skipped {skipped_downloaded} previously downloaded URL(s)")

    return imported

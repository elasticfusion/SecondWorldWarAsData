"""Search for external maps using Grok AI.

Automated workflow:
1. Read places from output/places/*.json
2. Extract place name, date, event context
3. Use Grok to search online for maps
4. Import found maps directly
"""

import json
import logging
from pathlib import Path
from typing import Optional, Dict, Any, List
import requests
import re

from src.grok_client import GrokClient

logger = logging.getLogger(__name__)

# Rate limiting: 1 request per 2 seconds (30 requests/minute)
RATE_LIMIT_DELAY = 2.0
# Image validation timeout
IMAGE_VALIDATION_TIMEOUT = 5.0
# User-Agent to avoid bot detection
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"


def _validate_map_data(map_data: Dict[str, Any]) -> bool:
    """Validate required fields in map data.

    Returns: True if valid, False otherwise
    """
    required = ["title", "external_source", "external_source_url"]
    return all(map_data.get(field) for field in required)


def _check_relevance(
    map_data: Dict[str, Any], place_name: str, date: Optional[str]
) -> bool:
    """Check if map is relevant to the search.

    Returns: True if relevant, False if likely hallucination
    """
    title = map_data.get("title", "").lower()
    description = map_data.get("description", "").lower()
    place_lower = place_name.lower()

    # STRICT: Require place name in title OR description
    place_mentioned = place_lower in title or place_lower in description

    # Check date range - WWII is 1939-1945 (with buffer)
    date_created = map_data.get("date_created", "")
    date_valid = True
    if date_created:
        try:
            year = int(date_created.split("-")[0])
            # Reject maps outside WWII era (1935-1950 buffer)
            date_valid = 1935 <= year <= 1950
        except (ValueError, IndexError):
            pass

    # BOTH must be true: place mentioned AND date valid
    return place_mentioned and date_valid


def _check_duplicate(output_dir: Path, external_source_url: str) -> bool:
    """Check if map already exists.

    Returns: True if duplicate found
    """
    if not external_source_url:
        return False

    for existing_file in output_dir.glob("*.json"):
        try:
            with open(existing_file, encoding="utf-8") as f:
                existing = json.load(f)
            if existing.get("external_source_url") == external_source_url:
                return True
        except Exception:  # nosec B112
            continue  # Skip invalid files
    return False


def _validate_image_url(url: Optional[str]) -> bool:
    """Validate that image URL is accessible and returns image content.

    Returns: True if URL returns 200 with image content, False otherwise
    """
    if not url:
        return False

    try:
        headers = {"User-Agent": USER_AGENT}

        # Try GET with range to check content
        response = requests.get(
            url,
            timeout=IMAGE_VALIDATION_TIMEOUT,
            allow_redirects=True,
            headers={**headers, "Range": "bytes=0-1023"},
        )

        # Check status
        if response.status_code not in (200, 206):
            return False

        # Check content-type - reject HTML error pages
        content_type = response.headers.get("content-type", "").lower()
        if "text/html" in content_type:
            return False

        # Check if we got actual content
        if len(response.content) > 0:
            return True

        return False

    except Exception as e:
        logger.debug(f"Image validation failed for {url}: {e}")
        return False


def _extract_loc_image_url(catalog_url: str) -> Optional[str]:
    """Extract actual image URL from LOC.gov catalog page.

    LOC.gov catalog URLs like /item/2007626644/ return 404.
    Need to fetch page and extract download URL from resources section.

    Returns: Direct image URL or None
    """
    if "loc.gov" not in catalog_url:
        return None

    try:
        headers = {"User-Agent": USER_AGENT}
        response = requests.get(
            catalog_url, timeout=10, headers=headers, allow_redirects=True
        )

        if response.status_code != 200:
            return None

        html = response.text

        # Look for tile.loc.gov image URLs (most common)
        match = re.search(r'https://tile\.loc\.gov/[^"\'>\s]+\.(?:jpg|tif)', html)
        if match:
            return match.group(0)

        return None

    except Exception as e:
        logger.debug(f"Failed to extract LOC image URL from {catalog_url}: {e}")
        return None


def _is_photograph(title: str, description: str) -> bool:
    """Check if title/description indicates this is a photograph, not a map.

    Returns: True if likely a photograph
    """
    text = f"{title} {description}".lower()
    photo_indicators = [
        "photograph",
        "photo of",
        "picture of",
        "image of",
        "showing",
        "looking at",
        "pointing at",
        "examining",
        "studying",
        "officers",
        "soldiers",
        "men",
        "general",
        "admiral",
    ]
    return any(indicator in text for indicator in photo_indicators)


def _extract_map_images(
    html_content: str, base_url: str, page_has_map_keyword: bool = False
) -> List[Dict[str, str]]:
    """Extract images that are likely maps from HTML.

    Args:
        html_content: HTML content to parse
        base_url: Base URL for resolving relative URLs
        page_has_map_keyword: If True, extract all images (page title/desc mentions maps)

    Returns: List of dicts with {url, alt, title, caption}
    """
    from html.parser import HTMLParser
    from urllib.parse import urljoin

    class MapImageParser(HTMLParser):
        def __init__(self, extract_all=False):
            super().__init__()
            self.images = []
            self.current_img = None
            self.in_figcaption = False
            self.in_figure_div = False
            self.caption_text = ""
            self.extract_all = extract_all

        def handle_starttag(self, tag, attrs):
            attrs_dict = dict(attrs)

            # Check for figure/map container divs
            if tag == "div":
                class_name = attrs_dict.get("class", "")
                if any(x in class_name.lower() for x in ["fig", "map", "image", "img"]):
                    self.in_figure_div = True

            if tag == "img":
                src = attrs_dict.get("src", "") or attrs_dict.get("data-original", "")
                alt = attrs_dict.get("alt", "")
                title = attrs_dict.get("title", "")
                class_name = attrs_dict.get("class", "")

                # Skip tiny images (icons, logos, artifacts)
                if any(
                    x in src.lower()
                    for x in [
                        "icon",
                        "logo",
                        "button",
                        "arrow",
                        "dot-gov",
                        "flag",
                        "email",
                        "banner",
                        "header",
                        "footer",
                        "nav",
                    ]
                ):
                    return

                # Skip images with width/height attributes indicating small size
                width = attrs_dict.get("width", "")
                height = attrs_dict.get("height", "")
                try:
                    if width and int(width) < 200:
                        return
                    if height and int(height) < 200:
                        return
                except (ValueError, TypeError):
                    pass

                # Check CSS classes for map/figure hints
                has_map_class = any(
                    x in class_name.lower() for x in ["map", "fig", "image", "img"]
                )

                # Check if likely a map
                text = f"{src} {alt} {title}".lower()
                has_map_keyword = "map" in text or "carte" in text or "karte" in text

                if (
                    self.extract_all
                    or has_map_keyword
                    or has_map_class
                    or self.in_figure_div
                ):
                    self.current_img = {
                        "url": urljoin(base_url, src),
                        "alt": alt,
                        "title": title,
                        "caption": "",
                    }

            elif tag == "figcaption":
                self.in_figcaption = True
                self.caption_text = ""
            elif tag == "p":
                # Check for caption-like paragraphs
                class_name = attrs_dict.get("class", "")
                if any(x in class_name.lower() for x in ["caption", "fig", "imgmark"]):
                    self.in_figcaption = True
                    self.caption_text = ""

        def handle_endtag(self, tag):
            if tag == "figcaption" or (tag == "p" and self.in_figcaption):
                self.in_figcaption = False
                if self.current_img:
                    self.current_img["caption"] = self.caption_text.strip()
                    self.images.append(self.current_img)
                    self.current_img = None
            elif tag == "div":
                self.in_figure_div = False

        def handle_data(self, data):
            if self.in_figcaption:
                self.caption_text += data
            elif self.current_img and not self.in_figcaption:
                # Image without figcaption - add it now
                self.images.append(self.current_img)
                self.current_img = None

    parser = MapImageParser(extract_all=page_has_map_keyword)
    try:
        parser.feed(html_content)
    except Exception as e:
        logger.debug(f"HTML parsing error: {e}")

    return parser.images


def _verify_map_relevance(
    map_url: str,
    map_title: str,
    map_description: str,
    place_name: str,
    date: Optional[str],
    event_context: str,
    grok_client: GrokClient,
    page_timeout: int = 10,
    image_timeout: int = 30,
) -> tuple[bool, bool]:
    """Download page, extract map images, and verify with Grok using vision.

    Args:
        map_url: URL of the page to check
        map_title: Title of the page
        map_description: Description/snippet from search results
        place_name: Place being searched for
        date: Date context
        event_context: Event context
        grok_client: Grok API client
        page_timeout: Timeout for page download
        image_timeout: Timeout for image download

    Returns: (is_relevant, is_government_map) - True if relevant, and True if government document
    """
    try:
        # Download the actual page content
        headers = {"User-Agent": USER_AGENT}
        response = requests.get(
            map_url, timeout=page_timeout, headers=headers, allow_redirects=True
        )

        if response.status_code != 200:
            logger.info(f"   ⚠ URL returned {response.status_code}")
            return False, False

        # Check if page title/description mentions maps
        page_text = f"{map_title} {map_description}".lower()
        page_has_map_keyword = (
            "map" in page_text or "carte" in page_text or "karte" in page_text
        )

        # Extract map images from HTML
        html_content = response.text
        map_images = _extract_map_images(
            html_content, map_url, page_has_map_keyword=page_has_map_keyword
        )

        if not map_images:
            logger.info(f"   ⚠ No map images found in HTML")
            return False, False

        logger.info(f"   Found {len(map_images)} potential map image(s)")

        # Check each image with Grok vision
        for img in map_images[
            :3
        ]:  # Limit to first 3 images to avoid excessive API calls
            img_url = img["url"]
            alt_text = img.get("alt", "")
            caption = img.get("caption", "")

            logger.info(f"   🔍 Analyzing image: {alt_text[:50] or img_url[-50:]}")

            # Ask Grok to analyze the image with context
            prompt = f"""Analyze this image VERY STRICTLY to determine if it's a WWII-era military/tactical map of {place_name}.

Context:
- Place: {place_name}
- Date: {date or 'WWII era (1939-1945)'}
- Event: {event_context}
- Page title: {map_title}
- Image alt text: {alt_text}
- Image caption: {caption}

CRITICAL REQUIREMENTS - ALL must be true:
1. Must be an actual MAP with geographic features, roads, terrain, or military positions
2. Must show {place_name} or immediate surrounding area (not just same country/region)
3. Must be from WWII era (1935-1950) - check for period-appropriate styling
4. Must be relevant to military operations/events (not just any historical map)

REJECT if ANY of these:
- Text document with place names but no map
- Photograph of people, equipment, or scenes (even if location is correct)
- Modern map (satellite, Google Maps, contemporary cartography)
- Website artifacts (logos, buttons, email addresses, navigation elements)
- Map of correct region but wrong time period (pre-1935 or post-1950)
- Map of correct region but too broad (e.g., all of Europe when place is specific city)
- Diagram, chart, or infographic (not a geographic map)
- Map that doesn't show the specific place mentioned

GOVERNMENT DOCUMENT OVERRIDE:
If the map appears to be an official government/military document (e.g., War Office, GSGS, AMS, US Army, British military, etc.), 
ACCEPT it regardless of the website's copyright terms, as government works are typically public domain.
Look for: official seals, "War Office", "GSGS", "AMS", military unit designations, official map series numbers.

BE VERY STRICT. When in doubt, REJECT.

Respond with ONLY a JSON object:
{{"is_relevant": true or false, "is_government_map": true or false, "reason": "Brief explanation of what you see and why accepted/rejected"}}
"""

            result = grok_client.extract_json_with_image(
                prompt=prompt,
                image_url=img_url,
                cache_type="external_maps_verification",
                image_timeout=image_timeout,
            )

            if isinstance(result, dict):
                is_relevant = result.get("is_relevant", False)
                is_government = result.get("is_government_map", False)
                reason = result.get("reason", "No reason provided")

                if is_relevant:
                    if is_government:
                        logger.info(f"   ✓ Government map confirmed: {reason}")
                    else:
                        logger.info(f"   ✓ Grok confirmed: {reason}")
                    return True, is_government
                else:
                    logger.info(f"   ⚠ Grok rejected: {reason}")

        logger.info(f"   ⚠ No relevant maps found in {len(map_images)} image(s)")
        return False, False

    except Exception as e:
        logger.warning(f"   ⚠ Verification failed: {e}")
        return False, False

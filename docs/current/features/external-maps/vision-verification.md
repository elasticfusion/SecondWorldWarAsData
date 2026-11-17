# External Maps Vision-Based Verification

**Date:** 2026-02-26 (updated 2026-11-17)  
**Status:** Implemented (`src/extraction/search_external_maps.py`, prompt `prompts/map_vision.yaml`). Reviewed 2026-11-17.

## Overview

Enhanced external maps verification to use Grok's vision API for analyzing actual map images instead of parsing HTML text.

## Changes Made

### 1. HTML Image Extraction (`search_external_maps.py`)

Added `_extract_map_images()` function that:
- Parses HTML to find `<img>` tags
- Filters for images likely to be maps (contains "map", "carte", "karte" in src/alt/title)
- Extracts image metadata:
  - `url` - Absolute image URL
  - `alt` - Alt text
  - `title` - Title attribute
  - `caption` - Associated `<figcaption>` text
- Returns list of candidate map images

### 2. Vision-Based Verification (`search_external_maps.py`)

Modified `_verify_map_relevance()` to:
- Download page HTML
- Extract map images using HTML parser
- Submit up to 3 images to Grok vision API
- Provide context with each image:
  - Place name
  - Date/era
  - Event context
  - Page title
  - Alt text and caption
- Ask Grok to analyze the actual image content
- Return `(is_relevant, is_government_map, approved_image_url, depicted)`; true if any image is confirmed. Each image's URL passes the SSRF gate
  `_ssrf_safe_image_url` first (public IPs only) and the Grok call is `extract_json_with_image` with `cache_type="external_maps_verification"`.
- The same prompt also extracts a `depicted` block (units, places, front/sector, date, operation, movements, description, `legibility_confidence`);
  the in-document map path (`map_link_source._vision_depicted`) and map-as-source recycling reuse it.

### 3. Grok Vision API Support (`grok_client.py`)

Added `extract_json_with_image()` method:
- Downloads image from URL
- Converts to base64 encoding
- Builds vision API message format:
  ```python
  {
    "role": "user",
    "content": [
      {"type": "text", "text": prompt},
      {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,..."}}
    ]
  }
  ```
- Caches results (keyed by prompt + image URL, per `cache_type`)
- Returns parsed JSON response
- Handles various image formats (JPEG, PNG, GIF, etc.)

### 4. OpenSERP Search Logging (`openserp_maps.py`)

Added search URL logging to help debug and verify searches:
- Logs the complete OpenSERP URL with encoded query parameters
- Shows which search engines are being queried
- Displays the search limit
- Example output:
  ```
     OpenSERP URL: http://localhost:7001/mega/search?text=WWII%20map%20%22Omaha%20Beach%22%201944&engines=google,bing,duckduckgo&limit=50
  (logged by `search_with_openserp`; the actual search runs through `./tools/search_maps`, which is not in the Docker image, see domain-blacklist.md)
  ```

## Workflow

**Before:**
1. Download HTML (8000 chars)
2. Send raw HTML to Grok
3. Grok tries to infer if page contains a map

**After:**
1. Download full HTML
2. Parse HTML to extract `<img>` tags with "map" keywords
3. For each candidate image (up to 3):
   - **Download image from URL**
   - **Convert to base64 encoding**
   - Send base64 image + context to Grok vision API
   - Grok analyzes actual image pixels
   - Returns relevance decision based on visual content
4. Accept if any image is confirmed relevant

## Benefits

- **Higher accuracy:** Grok sees actual map images, not HTML text
- **Better filtering:** Only analyzes images likely to be maps
- **Rich context:** Provides place/date/event context with each image
- **Efficient:** Limits to 3 images per page to control API costs
- **Multilingual:** Detects "map", "carte" (French), "karte" (German)
- **Reliable:** Downloads images and sends as base64 (works even if sites block hotlinking)
- **Format agnostic:** Handles JPEG, PNG, GIF, and other image formats automatically
- **Debuggable:** Search URLs logged for verification and testing

## API Usage

**Per place (with 5-15 search results):**
- Before: 5-15 text-based API calls
- After: 5-45 vision API calls (1-3 images per result)

**Note:** Vision API calls may have different pricing than text-only calls.

## Debugging

**View search queries:**
Check logs for lines like:
```
OpenSERP URL: http://localhost:7001/mega/search?text=...
```

You can:
- Copy the URL and test it directly in a browser
- Verify the search query format is correct
- Check which engines are being used
- Confirm the result limit

**Test OpenSERP manually:**
```bash
curl "http://localhost:7001/mega/search?text=WWII+map+%22Omaha+Beach%22&engines=google&limit=5"
```

## Prompt

The prompt is `prompts/map_vision.yaml` (rendered by `render_prompt("map_vision", place_name, date, event_context, title)`), shared by
`search_external_maps`, `grok_search_maps.verify_map_with_vision` and `map_link_source`. It has two steps: (1) verify that the image is a
genuine WWII-era map of the place (government-document override accepts official War Office / US Army maps regardless of host copyright);
(2) only when relevant, extract `depicted`. The answer schema is:

```json
{"is_relevant": true, "is_government_map": false, "reason": "...", "depicted": {"units": [], "places": [], "front_line_or_sector": null,
 "date_or_window": null, "operation": null, "movements": [], "description": "...", "legibility_confidence": 0.85}}
```

`depicted` is null when `is_relevant` is false. Unreadable fields are null or `[]`.

---

## Testing

To test with OpenSERP running:

```bash
# Start OpenSERP (tools/setup_openserp.sh builds it; on ECS Phase 3 scales the service up itself)
./openserp serve -p 7001 &

# Run Phase 3 enrichment (external_maps.enabled: true; the OpenSERP step uses vision verification)
python3 phase3_enrich_data.py
```

Check logs for:
- "OpenSERP URL: ..." - The search being performed
- "Found N potential map image(s)"
- "🔍 Analyzing image: ..."
- "✓ Grok confirmed: ..." or "⚠ Grok rejected: ..."

## Future Enhancements

- ~~Download and cache images locally for faster re-verification~~ ✓ Implemented (images downloaded and sent as base64)
- Extract image dimensions to prioritize larger images
- Cache downloaded images to disk to avoid re-downloading
- Batch multiple images in single API call (if supported)

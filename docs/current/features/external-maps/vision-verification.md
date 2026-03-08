# External Maps Vision-Based Verification

**Date:** 2026-02-26  
**Status:** Implemented

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
- Return true if any image is confirmed as relevant

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
- Caches results (keyed by prompt + image URL)
- Returns parsed JSON response
- Handles various image formats (JPEG, PNG, GIF, etc.)

### 4. OpenSERP Search Logging (`openserp_maps.py`)

Added search URL logging to help debug and verify searches:
- Logs the complete OpenSERP URL with encoded query parameters
- Shows which search engines are being queried
- Displays the search limit
- Example output:
  ```
  🔍 Searching: Omaha Beach
     OpenSERP URL: http://localhost:7001/mega/search?text=WWII+map+%22Omaha+Beach%22+1944&engines=google,bing,duckduckgo&limit=50
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

## Example Prompt to Grok

```
Analyze this image to determine if it's a WWII-era map of Omaha Beach.

Context:
- Place: Omaha Beach
- Date: 1944-06-06
- Event: Breakout and Pursuit - Allied Invasion of Normandy
- Page title: D-Day Landing Maps - Library of Congress
- Image alt text: Tactical map showing Omaha Beach defenses
- Image caption: Map prepared by Allied intelligence, June 1944

Questions:
1. Is this image a historical map (not a photograph, diagram, or modern map)?
2. Does it show Omaha Beach or the surrounding area?
3. Is it from the WWII era (1935-1950)?
4. Is it relevant to the event context?

REJECT if:
- Modern map (satellite imagery, Google Maps style)
- Photograph of people/equipment (not a map)
- Wrong geographic area
- Wrong time period

Respond with ONLY a JSON object:
{"is_relevant": true or false, "reason": "Brief explanation based on what you see in the image"}
```

## Testing

To test with OpenSERP running:

```bash
# Start OpenSERP
cd openserp && ./openserp serve -p 7001 &

# Run phase 2 extraction (will use vision verification)
python3 phase2_extract.py
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

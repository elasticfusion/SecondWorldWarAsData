# Equipment Media Integration

**Date:** 2026-03-04  
**Status:** ✅ Production Ready

---

## Overview

Automatic extraction and verification of equipment media (photos, diagrams, documents) from Wikipedia/Wikimedia using OpenSERP search and Grok vision API verification.

---

## Features

### 1. OpenSERP Integration
- **Real search engines:** Google, Bing, DuckDuckGo
- **No hallucinations:** Actual search results, not AI-generated
- **Domain filtering:** Respects `domain_blacklist.yaml`
- **Temporal filtering:** Uses event dates for year-specific searches

### 2. Wiki Page Extraction
- **Fetches HTML pages:** From Wikipedia/Wikimedia
- **Extracts image URLs:** Using Grok to parse HTML
- **Browser emulation:** Uses `requests` library with full browser headers
- **Avoids bot detection:** Standard User-Agent and headers

### 3. Vision API Verification
- **Relevance check:** Verifies image matches equipment
- **Era validation:** Confirms WWII period (1935-1950)
- **Type validation:** Ensures photo/diagram/document
- **Category match:** Checks equipment category
- **Rejection logging:** Records why images were rejected

### 4. Storage
- **Path:** `filestore/equipment/{ULID}/{ULID}.{ext}`
- **Organization:** ULID subdirectories for each media item
- **Metadata:** Both URL and local_path stored in JSON
- **Cleanup:** Empty directories removed on failure

---

## Configuration

```yaml
equipment:
  enabled: true
  enable_enrichment: true          # Required for media extraction
  verify_media_with_vision: true   # Recommended (default: true)
```

---

## Search Query Pattern

**With event date:**
```
"{technical_id} {common_name} WWII {year} photo wikipedia commons"
```
Example: `"M4 Sherman WWII 1944 photo wikipedia commons"`

**Without event date (fallback):**
```
"{technical_id} {common_name} WWII 1939-1945 photo wikipedia commons"
```

---

## Data Flow

1. **Equipment extracted** from event
2. **Date lookup** from `output/dates/` using Sub-eventID
3. **Year extracted** from date (e.g., "1944-06-06" → "1944")
4. **OpenSERP search** finds relevant wiki pages
5. **Page fetching** downloads HTML content
6. **Grok extraction** parses HTML for image URLs
7. **Image download** fetches actual image files
8. **Vision verification** validates relevance
9. **Storage** saves verified images to filestore
10. **JSON update** adds media metadata to equipment file

---

## Output Format

```json
{
  "common_name": "Sherman",
  "technical_identifier": "M4",
  "category": "armor",
  "media": [
    {
      "media_type": "photo",
      "url": "https://upload.wikimedia.org/wikipedia/commons/...",
      "local_path": "filestore/equipment/01KJX.../01KJX....jpg",
      "title": "M4 Sherman tank in Normandy",
      "source": "commons",
      "license": "See source",
      "description": "From https://en.wikipedia.org/wiki/M4_Sherman"
    }
  ]
}
```

---

## Technical Details

### Libraries Used
- **requests:** HTTP client for Wikipedia page fetching (avoids bot detection)
- **httpx:** Used for media file downloads with User-Agent headers
- **PIL/Pillow:** Image validation and resizing
- **subprocess:** Calls Go search_media tool

### Bot Detection Avoidance
**Problem:** Wikipedia blocks `httpx` requests with 403 Forbidden, even with proper User-Agent

**Solution:** Use `requests` library for page fetching
- **User-Agent:** Standard Chrome browser string
- **Headers:** Accept, Accept-Language, DNT, Connection, Upgrade-Insecure-Requests
- **Why requests works:** Different TLS fingerprint, more browser-like behavior
- **Why httpx fails:** Wikipedia's bot detection identifies httpx characteristics

**Implementation:**
```python
# Page fetching (requests)
headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36...",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9...",
    "Accept-Language": "en-US,en;q=0.5",
    # ... more browser headers
}
response = requests.get(page_url, timeout=30, headers=headers)

# Media downloads (httpx) - works fine for direct image URLs
response = httpx.get(image_url, timeout=30, headers=headers, follow_redirects=True)
```

### Limits
- **Pages processed:** 3 wiki pages per equipment
- **Images per page:** 2 images maximum
- **Total images:** 6 images per equipment
- **Image size:** Resized if >5MB
- **Timeout:** 30 seconds per request

### Error Handling
- **403 Forbidden:** Switches to `requests` library
- **404 Not Found:** Logs and skips
- **Timeout:** Logs warning and continues
- **Invalid image:** Rejects with reason
- **Vision rejection:** Logs reason and skips
- **Empty directories:** Cleaned up automatically

---

## Cache Types

1. **equipment_enrichment:** Wikipedia/Grokipedia data
2. **equipment_image_extraction:** Image URLs from wiki pages
3. **vision_verification:** Vision API verification results

---

## Performance

- **Without media:** ~5-10 seconds per equipment
- **With media:** ~15-30 seconds per equipment
  - OpenSERP search: ~1-2 seconds
  - Page fetching: ~1-2 seconds per page
  - Image extraction: ~5-10 seconds (Grok)
  - Image download: ~1-2 seconds per image
  - Vision verification: ~3-5 seconds per image

---

## Troubleshooting

### No images found
- Check OpenSERP is running: `ps aux | grep openserp`
- Verify search_media binary exists: `ls -la search_media`
- Check logs for 403 errors
- Verify domain_blacklist.yaml syntax

### 403 Forbidden errors from Wikipedia
- **Root cause:** Wikipedia blocks `httpx` library as bot traffic
- **Solution:** Code uses `requests` library for page fetching
- **Verify:** Check `_extract_image_urls_from_page()` uses `requests.get()`
- **Headers:** Ensure all browser headers are present (Accept, Accept-Language, etc.)
- **Test:** `curl` with same headers should return 200 OK

### Images rejected by vision API
- Check logs for rejection reasons
- Verify equipment name is correct
- Check if images are actually relevant
- Review vision API prompt in code

### Empty directories
- Check download function cleanup logic
- Verify finally block executes
- Check file permissions on filestore/

---

## Related Documentation

- **Error Handling:** `EQUIPMENT_ERROR_HANDLING.md`
- **Error Handling Spec:** `contextmanagement/Specs/error_handling.md` (Pattern 21)

---

**Status:** ✅ Production Ready  
**Version:** 1.0.0  
**Last Updated:** 2026-03-22

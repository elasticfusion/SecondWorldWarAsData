# Migration: httpx → requests for Image Downloads

**Date:** 2026-03-04  
**Status:** ✅ Complete

## Summary

Migrated image download functions from `httpx` to `requests` to avoid bot detection issues on external sites.

## Rationale

1. **Bot Detection**: Comment in `equipment.py` indicated httpx triggers bot detection
2. **Consistency**: Already using `requests` for Wikipedia page scraping
3. **Reliability**: `requests` has better compatibility with various web servers
4. **Simpler API**: More straightforward for basic HTTP operations

## Changes Made

### 1. requirements.txt
- Added: `requests>=2.32.0`

### 2. src/extraction/grok_search_maps.py
- **Function:** `download_image()`
- Changed from: `httpx.get()` with context manager
- Changed to: `requests.get()` with direct call
- Exception: `httpx.HTTPError` → generic `Exception`

### 3. src/extraction/maps.py
- **Function:** `_download_map_image()`
- Changed from: `httpx.Client()` context manager
- Changed to: `requests.get()` direct call
- Exception: `httpx.HTTPError` → `requests.RequestException`

- **Function:** `_download_image_to_s3()`
- Changed from: `httpx.Client()` context manager
- Changed to: `requests.get()` direct call
- Exception: `httpx.HTTPError` → `requests.RequestException`

### 4. src/extraction/equipment.py
- **Function:** `_download_media_file()`
- Changed from: `httpx.Client()` context manager
- Changed to: `requests.get()` direct call
- Exception: `httpx.HTTPError` → `requests.RequestException`

## API Differences

```python
# Before (httpx)
with httpx.Client(timeout=30, follow_redirects=True, headers=headers) as client:
    response = client.get(url)
    response.raise_for_status()
    content = response.content

# After (requests)
response = requests.get(url, timeout=30, headers=headers, allow_redirects=True)
response.raise_for_status()
content = response.content
```

## Key Differences

| Feature | httpx | requests |
|---------|-------|----------|
| Context Manager | Required for connection pooling | Optional |
| Redirects | `follow_redirects=True` | `allow_redirects=True` |
| Exceptions | `httpx.HTTPError` | `requests.RequestException` |
| Import | `import httpx` | `import requests` |

## Files NOT Changed

These files still use `httpx` for other purposes (non-image downloads):

- `src/extraction/openserp_maps.py` - HTML page fetching
- `src/extraction/search_external_maps.py` - HTML page fetching
- `src/extraction/enrich_biographies.py` - Wikipedia API calls
- `src/extraction/weather_central.py` - Open-Meteo API calls

## Testing

```bash
# Test imports
python3 -c "from src.extraction.grok_search_maps import download_image; print('✅')"
python3 -c "from src.extraction.maps import _download_map_image; print('✅')"

# Run full pipeline
python3 phase2_extract.py
```

## Benefits

1. ✅ Avoids bot detection on external sites
2. ✅ Consistent with existing Wikipedia scraping code
3. ✅ Simpler code (no context managers needed)
4. ✅ Better compatibility with various web servers
5. ✅ Maintains all existing functionality

## Notes

- `httpx` remains in requirements.txt (used for other API calls)
- Both libraries coexist in the project
- No breaking changes to external APIs
- All timeout and header configurations preserved

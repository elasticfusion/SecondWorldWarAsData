# LOC.gov URL Fix Summary

**Date:** 2026-02-25  
**Issue:** LOC.gov catalog URLs return 404 errors  
**Status:** ✅ Fixed

---

## Problem

Grok returns Library of Congress catalog page URLs like:
```
https://www.loc.gov/item/2007626644/
```

These are **catalog pages**, not direct image URLs. When accessed directly, they return 404 or HTML pages, not images.

**Example from `01KJAHC47QXEYM8104QCSZ4K76.json`:**
- `external_source_url`: `https://www.loc.gov/item/2007626644/`
- `source_url`: `` (empty - no image URL)
- Result: 404 error

---

## Root Cause

LOC.gov has two types of URLs:
1. **Catalog pages:** `/item/XXXXXXX/` - HTML pages with metadata
2. **Image URLs:** `https://tile.loc.gov/storage-services/...jpg` - Actual images

Grok provides catalog pages, but we need image URLs for validation and download.

---

## Solution

Added `_extract_loc_image_url()` function that:
1. Fetches the catalog page HTML
2. Searches for `tile.loc.gov` image URLs using regex
3. Returns the first valid image URL found
4. Returns `None` if no image found (likely hallucination)

**Regex pattern:**
```python
r'https://tile\.loc\.gov/[^"\'>\s]+\.(?:jpg|tif)'
```

Matches URLs like:
```
https://tile.loc.gov/storage-services/service/pnp/fsa/8b28000/8b28100/8b28106r.jpg
```

---

## Integration

Added to import flow in `search_external_maps.py`:

```python
# If no image URL but LOC.gov source, try to extract it
if not image_url:
    source_url = map_data.get("external_source_url", "")
    if "loc.gov" in source_url:
        image_url = _extract_loc_image_url(source_url)
        if image_url:
            map_data["file_url"] = image_url
        else:
            # Reject map - likely hallucination
            logger.warning("LOC.gov page has no downloadable image, skipping")
            continue
```

---

## Benefits

1. **Validates LOC.gov maps:** Only imports if actual image exists
2. **Filters hallucinations:** Rejects fake LOC.gov URLs
3. **Enables downloads:** Provides direct image URLs for future download feature
4. **Better logging:** Shows when LOC.gov extraction fails

---

## Testing

The existing map `01KJAHC47QXEYM8104QCSZ4K76.json` would now be rejected because:
- Catalog URL: `https://www.loc.gov/item/2007626644/`
- Extraction result: No `tile.loc.gov` image found
- Conclusion: Likely hallucination, reject

---

## Files Modified

1. `src/extraction/search_external_maps.py`
   - Added `_extract_loc_image_url()` function
   - Added `import re` for regex
   - Integrated extraction into import flow
   - Rejects maps if extraction fails

2. `docs/current/EXTERNAL_MAPS_CHANGELOG.md`
   - Documented fix

---

## Future Enhancements

- [ ] Support other LOC.gov URL patterns
- [ ] Cache extracted URLs to avoid re-fetching
- [ ] Add extraction for other archives (NARA, IWM)
- [ ] Fallback to API if HTML parsing fails

---

## Related Issues

- Hallucination prevention (2026-02-25)
- Image URL validation (2026-02-24)

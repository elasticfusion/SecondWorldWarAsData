# Image Processing Improvements

**Date:** 2026-03-02  
**Status:** Implemented for conversion and resizing (`_prepare_map_image`); User-Agent handling superseded (section 2). Used by the manual Grok map search only.  
**Impact:** Reliability, compatibility

---

## Overview

Enhanced image download and processing to handle format conversion, size optimization, and proper User-Agent identification for different domains.

---

## Changes Made

### 1. Image Validation and Conversion

**File:** `src/extraction/grok_search_maps.py`  
**Function:** `verify_map_with_vision()` (conversion/resizing lives in `_prepare_map_image()`)

#### Format Conversion
Automatically converts unsupported image formats to PNG:
- BMP → PNG
- TIFF → PNG
- WebP → PNG
- Any PIL-supported format → PNG

**Implementation:**
```python
if img.format not in ["PNG", "JPEG", "JPG", "GIF"]:
    logger.info(f"Converting {img.format} to PNG")
    img.convert("RGB").save(buffer, format="PNG")
    image_data = buffer.getvalue()
```

**Benefits:**
- No rejected images due to format
- Transparent conversion
- In-memory processing (no disk I/O)

---

#### Automatic Resizing

Resizes images exceeding 5MB limit:
- Iteratively reduces by 70%, 60%, 50%, etc.
- Uses high-quality LANCZOS resampling
- Optimizes PNG compression
- Stops when < 5MB or scale < 10%

**Implementation:**
```python
if size_mb > 5:
    logger.info(f"Resizing image ({size_mb:.1f}MB → target <5MB)")
    scale = 0.7
    while size_mb > 5 and scale > 0.1:
        new_size = (int(img.width * scale), int(img.height * scale))
        resized = img.resize(new_size, Image.Resampling.LANCZOS)
        # Save and check size
        scale -= 0.1
```

**Benefits:**
- No rejected images due to size
- Preserves image quality
- Automatic and transparent

---

### 2. Image download and User-Agent

**Status (2026-11-17):** the domain-specific User-Agent logic described in earlier versions of this doc no longer exists.

**File:** `src/extraction/grok_search_maps.py`, function `download_image()` calls `images.fetch_image_bytes` (`src/extraction/images.py:32`).
That function sends one User-Agent for every host (`images._USER_AGENT`, Chrome 131 string), resolves and vets the host
(`url_hygiene.resolved_host_is_safe`, DNS-rebinding safe, no redirects), caps the body at 25 MB (`_MAX_IMAGE_BYTES`), and accepts only an
image content type. The `timeout` argument of `download_image` is ignored. There is no Wikimedia bot User-Agent in this path.
`openserp_maps` fetches page and licence text with its own `USER_AGENT` (Chrome 120 string) and downloads images through the same
`fetch_image_bytes`; `search_external_maps` uses its own `USER_AGENT` for page and Range requests.

---

## Issues Resolved

### Issue 1: Grok API 400 Errors
**Problem:** Invalid arguments passed to vision API  
**Cause:** Unsupported image formats or oversized images  
**Solution:** Automatic format conversion and resizing  
**Status:** ✅ Resolved

### Issue 2: Wikimedia 403 Forbidden and "outdated browser" pages
**Status:** the Wikimedia bot User-Agent fix was removed when downloads moved to the shared SSRF-safe fetcher (section 2).
Whether Wikimedia hosts still return 403 to the single Chrome User-Agent is UNVERIFIED.

---

## Technical Details

### Image Processing Pipeline

1. **Download** - `images.fetch_image_bytes` (SSRF-safe, 25 MB cap, image content types only)
2. **Validate** - Verify it's a valid image file
3. **Convert** - Transform unsupported formats to PNG
4. **Resize** - Reduce size if > 5MB (the Pillow decode runs under a 50,000,000-pixel decompression-bomb cap; a bomb is rejected with `OBSERV maps.image_bomb_rejected`)
5. **Verify** - Send to Grok vision API
6. **Save** - Store if relevant

### Error Handling

**Invalid Images:**
```python
return False, f"Invalid image: {e}"
```

**Oversized After Resize:**
```python
return False, f"Image still too large after resize ({size_mb:.1f}MB)"
```

**Download Failures:** `download_image` returns `None` (logged at debug as blocked/non-image) and the caller skips the image.

### Caching Behavior

A failed download returns `None` before any Grok call, so no vision request is made and no cache entry exists. Vision calls use
`cache_type="vision_verification"` and are cached by prompt and image hash.

---

## Dependencies

**Required:**
- `Pillow` (PIL) - Image processing (pinned in `requirements.txt`)

**Already installed** - No new dependencies added

---

## Configuration

### Adjusting Size Limits

Change the 5MB threshold in `_prepare_map_image` (`grok_search_maps.py`, two comparisons of `size_mb > 5`) and the 25 MB download cap `_MAX_IMAGE_BYTES` in `images.py`.

The User-Agent is the `_USER_AGENT` constant in `src/extraction/images.py`.

---

## Testing

### Verify Format Conversion
```bash
# Look for conversion logs
grep "Converting" logs/grok_search.log
```

### Verify Resizing
```bash
# Look for resize logs
grep "Resizing image" logs/grok_search.log
```

---

## Performance Impact

**Format Conversion:**
- Minimal overhead (< 100ms per image)
- In-memory processing

**Resizing:**
- Depends on original size
- Typically < 500ms for large images
- Only runs when needed

---

## Quality Assurance

Quality figures from 2026-03 were removed (stale). The checks that run today are `scripts/gate.sh` and the CI pipelines.

---

## Future Enhancements

### Potential Improvements

1. **Configurable size limits** - Move to config.yaml
2. **More aggressive compression** - JPEG quality adjustment
3. **Format-specific optimization** - Different strategies per format
4. **Rate limiting** - Respect robots.txt and crawl delays
5. **Retry logic** - Exponential backoff for transient errors

---

## References

- **Wikimedia User-Agent Policy:** https://meta.wikimedia.org/wiki/User-Agent_policy
- **PIL Image Processing:** https://pillow.readthedocs.io/
- **HTTP User-Agent Strings:** https://developer.mozilla.org/en-US/docs/Web/HTTP/Headers/User-Agent

---

## Version History

- **2026-03-02**: Initial implementation
  - Format conversion (BMP, TIFF, WebP → PNG)
  - Automatic resizing (> 5MB)
  - Domain-specific User-Agents (removed later; downloads go through `images.fetch_image_bytes`)
  - Wikimedia bot compliance (removed with the above)

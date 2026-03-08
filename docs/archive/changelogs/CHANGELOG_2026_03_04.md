# Recent Changes - 2026-03-04

## Image Download Migration: httpx → requests

**Rationale:** Avoid bot detection on external sites, improve compatibility

**Changes:**
- Migrated all image download functions to use `requests` instead of `httpx`
- Added `requests>=2.32.0` to requirements.txt
- Updated: `grok_search_maps.py`, `maps.py`, `equipment.py`

**Files Modified:**
- `src/extraction/grok_search_maps.py` - `download_image()`
- `src/extraction/maps.py` - `_download_map_image()`, `_download_image_to_s3()`
- `src/extraction/equipment.py` - `_download_media_file()`

**Documentation:** [HTTPX_TO_REQUESTS_MIGRATION.md](HTTPX_TO_REQUESTS_MIGRATION.md)

---

## Equipment Image Deduplication

**Feature:** Automatic deduplication of equipment images using perceptual hashing

**Implementation:**
- Added `Pillow>=10.0.0` and `imagehash>=4.3.0` dependencies
- New function: `_compute_image_hash()` for perceptual hashing
- Updated: `_download_and_store_media()` with deduplication logic
- Scope: Within single equipment item only

**How It Works:**
1. Downloads image and computes perceptual hash
2. Compares with previously downloaded images for same equipment
3. Removes duplicate files automatically
4. Logs: `🗑️ Duplicate image removed: [title] (same as [existing])`

**Benefits:**
- Storage savings (eliminates duplicate files)
- Cleaner data (only unique images per equipment)
- Automatic (no manual intervention)
- Detects near-duplicates (crops, resizes, minor edits)

**Documentation:** [EQUIPMENT_IMAGE_DEDUPLICATION.md](features/equipment/EQUIPMENT_IMAGE_DEDUPLICATION.md)

**Testing:**
```bash
python3 tests/test_equipment_deduplication.py
```

---

## Summary

Both changes improve the reliability and efficiency of the equipment media extraction pipeline:

1. **requests migration** - Better compatibility with external sites
2. **Image deduplication** - Cleaner data and storage savings

No breaking changes. All existing functionality preserved.

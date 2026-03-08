# Equipment Image Deduplication

**Status:** ✅ Implemented  
**Date:** 2026-03-04

## Overview

Automatic deduplication of equipment images using perceptual hashing to prevent storing duplicate or near-duplicate images within a single equipment item.

## Scope

- **Within Equipment Only**: Deduplication is scoped to a single equipment JSON file
- **Automatic**: Runs during media download in Phase 2
- **Non-Destructive**: Only removes duplicates, preserves first occurrence

## How It Works

### Algorithm

1. Download image for equipment item
2. Compute perceptual hash (average hash)
3. Compare with previously downloaded images for same equipment
4. If duplicate detected:
   - Remove duplicate file
   - Clean up empty directory
   - Log which image it duplicates
5. If unique, keep and add to hash registry

### Perceptual Hashing

Uses `imagehash` library with **average hash** algorithm:
- Fast computation
- Detects identical images
- Detects near-identical images (minor edits, crops, resizes)
- 64-bit hash for efficient comparison

## Example

```
Equipment: Sherman Tank (01KJXACG4JN1GP1KCDAHW06688)

Downloaded:
  1. Sherman_front.jpg     → Hash: ffdf8f8eb30b0f8f ✅ Kept
  2. Sherman_side.jpg      → Hash: ff9580808101f7ff ✅ Kept (different)
  3. Sherman_front_2.jpg   → Hash: ffdf8f8eb30b0f8f 🗑️ Removed (duplicate of #1)

Result:
  filestore/equipment/01KJXACG4JN1GP1KCDAHW06688/
    ├── 01KJXACG4JN1GP1KCDAHW06688.jpg  (Sherman front)
    └── 01KJXACG4JN1GP1KCDAHW06689.jpg  (Sherman side)
```

## Log Output

```
Enriching equipment data for: Sherman Tank
  ✅ Verified: Shows Sherman tank from front
  ✅ Verified: Shows Sherman tank from side
  🗑️ Duplicate image removed: Sherman tank front view (same as Shows Sherman tank from front)
  Added 2 verified media items
```

## Configuration

No configuration needed - deduplication is automatic when equipment extraction is enabled:

```yaml
# config.yaml
equipment:
  enabled: true
  enable_enrichment: true
  verify_media_with_vision: true  # Recommended
```

## Dependencies

```txt
Pillow>=10.0.0      # Image processing
imagehash>=4.3.0    # Perceptual hashing
```

## Implementation

### Function: `_download_and_store_media()`

**Location:** `src/extraction/equipment.py`

```python
def _download_and_store_media(...) -> List[Dict[str, Any]]:
    """Download media files with deduplication."""
    downloaded_media = []
    image_hashes = {}  # hash -> (local_path, title)
    
    for media_item in media_list:
        local_path = _download_media_file(...)
        if local_path and media_item.get("media_type") == "photo":
            img_hash = _compute_image_hash(full_path)
            
            if img_hash in image_hashes:
                # Duplicate - remove file
                logger.info("🗑️ Duplicate image removed: ...")
                full_path.unlink()
                continue
            
            image_hashes[img_hash] = (local_path, title)
        
        downloaded_media.append(media_item)
    
    return downloaded_media
```

### Function: `_compute_image_hash()`

**Location:** `src/extraction/equipment.py`

```python
def _compute_image_hash(image_path: Path) -> Optional[str]:
    """Compute perceptual hash for deduplication."""
    from PIL import Image
    import imagehash
    
    with Image.open(image_path) as img:
        return str(imagehash.average_hash(img))
```

## Benefits

1. **Storage Savings**: Eliminates duplicate image files
2. **Cleaner Data**: Each equipment has only unique images
3. **Automatic**: No manual intervention required
4. **Fast**: Average hash is computationally efficient
5. **Robust**: Detects near-duplicates (crops, resizes, minor edits)

## Limitations

- **Scope**: Only deduplicates within single equipment item
- **Photos Only**: Only applies to `media_type: "photo"`
- **First Wins**: Keeps first occurrence, removes subsequent duplicates
- **No Cross-Equipment**: Same image in different equipment items is kept

## Testing

```bash
# Test deduplication logic
python3 tests/test_equipment_deduplication.py

# Run with equipment extraction
python3 phase2_extract.py
```

## Future Enhancements

Potential improvements (not currently implemented):

- Cross-equipment deduplication
- Similarity threshold configuration
- Different hash algorithms (phash, dhash)
- Duplicate reporting/statistics
- Manual review of duplicates

## Related Documentation

- [Equipment Implementation Summary](EQUIPMENT_IMPLEMENTATION_SUMMARY.md)
- [Equipment Media Integration](EQUIPMENT_MEDIA_INTEGRATION.md)
- [Equipment Error Handling](EQUIPMENT_ERROR_HANDLING.md)

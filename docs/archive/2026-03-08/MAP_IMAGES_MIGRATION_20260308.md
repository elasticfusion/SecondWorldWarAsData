# Map Images Path Migration - 2026-03-08

## Summary

Permanently moved map images from `cache/maps` to `output/maps_images` and updated all code and documentation references.

---

## Rationale

**Why move from cache to output?**
- Map images are **output artifacts**, not temporary cache
- Cache is for API responses and temporary data
- Output directory is for final extracted data
- Consistent with other media storage (equipment uses `filestore/`)
- Better separation of concerns

---

## Changes Made

### 1. Physical File Move
```bash
mv cache/maps/* output/maps_images/
rmdir cache/maps
```

**Files moved:** 9 map images (2.2 MB total)

---

### 2. Code Changes

#### `config.yaml`
```yaml
# BEFORE:
paths:
  map_cache: "cache/maps"  # Downloaded maps

# AFTER:
paths:
  maps_images: "output/maps_images"  # Map images from source material
```

#### `src/extraction/maps.py`
```python
# BEFORE:
image_storage = Path(maps_config.get("image_storage_path", "cache/maps"))

# AFTER:
image_storage = Path(maps_config.get("image_storage_path", "output/maps_images"))
```

#### `phase2_extract.py`
```python
# BEFORE:
for cache_type in ["image_cache", "map_cache"]:
    if cache_type in paths:
        paths[cache_type].mkdir(parents=True, exist_ok=True)

# AFTER:
for cache_type in ["image_cache"]:
    if cache_type in paths:
        paths[cache_type].mkdir(parents=True, exist_ok=True)

# Ensure output directories exist
if "maps_images" in paths:
    paths["maps_images"].mkdir(parents=True, exist_ok=True)
```

---

### 3. Documentation Updates

**Updated files:**
- `docs/current/features/maps/README.md`
- `docs/current/features/maps/S3_STORAGE.md`
- `docs/current/core/CONFIGURATION.md`
- `contextmanagement/Specs/maps.md`

**Changes:**
- All `cache/maps` → `output/maps_images`
- Updated example paths
- Updated directory structure diagrams
- Updated configuration examples

**Archived (not updated):**
- `docs/archive/core/CACHE_STRUCTURE.md` (historical reference)

---

## Directory Structure

### Before
```
cache/
├── api/           # API responses
├── images/        # Downloaded images
└── maps/          # Map images ❌ (wrong location)

output/
└── maps/          # Map metadata JSON
```

### After
```
cache/
├── api/           # API responses
└── images/        # Downloaded images

output/
├── maps/          # Map metadata JSON
└── maps_images/   # Map images ✅ (correct location)
```

---

## Testing

```python
from src.extraction.maps import _setup_image_storage

maps_config = {'download_images': True}
download, storage, timeout = _setup_image_storage(maps_config, 'filesystem')

# Results:
# Download images: True
# Storage path: output/maps_images
# Timeout: 30
# Path exists: True
```

✅ Code correctly uses new path
✅ Directory auto-created
✅ Default path updated

---

## Configuration

### Default (No Config Override)
```yaml
maps:
  download_images: true
  storage_backend: "filesystem"
  # Uses default: output/maps_images
```

### Explicit Path (Optional)
```yaml
maps:
  download_images: true
  storage_backend: "filesystem"
  image_storage_path: "output/maps_images/"  # Can override if needed
```

---

## Impact

### Positive
- ✅ Correct separation of cache vs output
- ✅ Consistent with project structure
- ✅ Easier to understand for new developers
- ✅ Better for backup/archival (output only)
- ✅ Clearer .gitignore rules

### Breaking Changes
- ⚠️ Existing scripts referencing `cache/maps` need update
- ⚠️ Manual cleanup of old `cache/maps` if it exists

### Migration for Existing Installations
```bash
# If cache/maps exists, move to new location
if [ -d "cache/maps" ]; then
    mkdir -p output/maps_images
    mv cache/maps/* output/maps_images/
    rmdir cache/maps
fi
```

---

## Related Files

**Code:**
- `src/extraction/maps.py` (default path)
- `phase2_extract.py` (directory creation)
- `config.yaml` (path configuration)

**Documentation:**
- `docs/current/features/maps/README.md`
- `docs/current/features/maps/S3_STORAGE.md`
- `docs/current/core/CONFIGURATION.md`
- `contextmanagement/Specs/maps.md`

---

## Future Considerations

1. **Equipment Media:** Consider moving `filestore/equipment` to `output/equipment_media` for consistency
2. **External Maps:** Already uses `filestore/external_maps/` (correct)
3. **Cache Cleanup:** Add script to clean old cache directories
4. **Documentation:** Update README.md project structure section

---

**Status:** ✅ Complete and Tested

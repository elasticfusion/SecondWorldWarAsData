# Map URLs Feature - Added to Place Schema v2.0.0

**Date:** 2026-02-23  
**Feature:** Automatic generation of modern map service URLs

---

## Overview

Added `map_urls` field to place schema containing direct links to Google Maps and OpenStreetMap for each location. URLs are automatically generated from latitude/longitude coordinates.

---

## Schema Changes

### New Field: `map_urls`

```json
{
  "map_urls": {
    "google_maps": "https://www.google.com/maps?q=52.2297,21.0122",
    "openstreetmap": "https://www.openstreetmap.org/?mlat=52.2297&mlon=21.0122&zoom=12"
  }
}
```

**Properties:**
- `google_maps` - Direct link to Google Maps at coordinates
- `openstreetmap` - Direct link to OpenStreetMap at coordinates (zoom level 12)

**Applies to:**
- Single place mentions
- Route stops (each stop gets its own URLs)

---

## URL Formats

### Google Maps
```
https://www.google.com/maps?q={latitude},{longitude}
```
**Example:** `https://www.google.com/maps?q=52.2297,21.0122`

### OpenStreetMap
```
https://www.openstreetmap.org/?mlat={latitude}&mlon={longitude}&zoom=12
```
**Example:** `https://www.openstreetmap.org/?mlat=52.2297&mlon=21.0122&zoom=12`

---

## Implementation

### 1. Schema Definition
Updated `contextmanagement/Specs/place_v2.json`:
- Added `map_urls` object to `SinglePlace` definition
- Added `map_urls` object to route stop definition
- Added URL pattern validation
- Updated examples

### 2. Code Changes

**`src/extraction/places.py`:**
```python
class MapUrls(BaseModel):
    google_maps: str
    openstreetmap: str

class PlaceMention(BaseModel):
    # ... existing fields ...
    map_urls: Optional[MapUrls] = None

def _generate_map_urls(lat: float, lon: float) -> Dict[str, str]:
    """Generate map service URLs for coordinates."""
    return {
        "google_maps": f"https://www.google.com/maps?q={lat},{lon}",
        "openstreetmap": f"https://www.openstreetmap.org/?mlat={lat}&mlon={lon}&zoom=12"
    }
```

**`scripts/migrate_place_schema.py`:**
- Automatically generates map URLs during migration
- Applies to both single places and route stops

---

## Usage Examples

### Single Place
```json
{
  "PlaceMentionID": "01H8XYZ8AB123CD456EF789GH",
  "current_name": "Warsaw",
  "latitude": 52.2297,
  "longitude": 21.0122,
  "geography_type": "city",
  "original_text": "Warsaw",
  "map_urls": {
    "google_maps": "https://www.google.com/maps?q=52.2297,21.0122",
    "openstreetmap": "https://www.openstreetmap.org/?mlat=52.2297&mlon=21.0122&zoom=12"
  }
}
```

### Route with Multiple Stops
```json
{
  "PlaceMentionID": "01H8XYZB4DE123FG456HI789JK",
  "source_language": "English",
  "route": [
    {
      "sequence": 1,
      "current_name": "Berlin",
      "latitude": 52.5200,
      "longitude": 13.4050,
      "geography_type": "city",
      "map_urls": {
        "google_maps": "https://www.google.com/maps?q=52.5200,13.4050",
        "openstreetmap": "https://www.openstreetmap.org/?mlat=52.5200&mlon=13.4050&zoom=12"
      }
    },
    {
      "sequence": 2,
      "current_name": "Warsaw",
      "latitude": 52.2297,
      "longitude": 21.0122,
      "geography_type": "city",
      "map_urls": {
        "google_maps": "https://www.google.com/maps?q=52.2297,21.0122",
        "openstreetmap": "https://www.openstreetmap.org/?mlat=52.2297&mlon=21.0122&zoom=12"
      }
    }
  ],
  "original_text": "from Berlin to Warsaw"
}
```

---

## Benefits

1. **Direct Navigation** - Click to view location on modern maps
2. **Verification** - Easy coordinate validation
3. **Visualization** - Quick geographic context
4. **Integration** - Ready for web interfaces and APIs
5. **No Extra API Calls** - Generated from existing coordinates

---

## Migration

The migration script automatically generates map URLs for all existing places:

```bash
# Dry run to preview
python3 scripts/migrate_place_schema.py --dry-run

# Apply migration (adds map URLs)
python3 scripts/migrate_place_schema.py
```

**Result:** All 18 place files will have map URLs added automatically.

---

## Future Enhancements

Potential additions:
- **Bing Maps** - `https://www.bing.com/maps?cp={lat}~{lon}&lvl=12`
- **Apple Maps** - `https://maps.apple.com/?ll={lat},{lon}&z=12`
- **Historical Maps** - Links to period-appropriate maps
- **Custom Zoom Levels** - Based on geography type (city=14, country=6)
- **Directions** - Multi-stop route URLs

---

## Testing

### Validate URL Format
```python
import re

def test_map_urls():
    google_pattern = r"^https://www\.google\.com/maps\?q=-?\d+\.\d+,-?\d+\.\d+$"
    osm_pattern = r"^https://www\.openstreetmap\.org/\?mlat=-?\d+\.\d+&mlon=-?\d+\.\d+&zoom=\d+$"
    
    google_url = "https://www.google.com/maps?q=52.2297,21.0122"
    osm_url = "https://www.openstreetmap.org/?mlat=52.2297&mlon=21.0122&zoom=12"
    
    assert re.match(google_pattern, google_url)
    assert re.match(osm_pattern, osm_url)
```

### Manual Testing
1. Run migration script
2. Open any place JSON file
3. Copy `google_maps` URL → paste in browser → should show location
4. Copy `openstreetmap` URL → paste in browser → should show location

---

## Notes

- URLs are optional (not required by schema)
- Generated automatically during extraction and migration
- Only created when valid coordinates exist
- Zoom level 12 is a reasonable default for most locations
- URLs are validated by regex pattern in schema

---

**Status:** ✅ Implemented  
**Schema Version:** 2.0.0  
**Files Modified:** 3 (schema, places.py, migration script)

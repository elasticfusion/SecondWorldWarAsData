# Places Extraction

**Module:** `src/extraction/places.py`  
**Status:** Production  
**Last Updated:** 2026-03-22

---

## Overview

Places extraction analyzes event files and extracts all geographic mentions with GPS coordinates into a **central repository**. Each unique place gets its own file, with event mentions linked via MentionID.

**Key Features:**
- GPS coordinates (latitude/longitude) with precision and confidence
- Automatic bounding box calculation (100km radius)
- Map service URL generation (Google Maps, OpenStreetMap)
- 22-type geography classification
- Historical names array and aliases
- Hierarchy data (continent/country/region) via enrichment
- DateMentionID cross-referencing to dates directory
- Central repository prevents duplication

---

## Architecture

### Data Flow

```
Event File (JSON)
    ↓
For each Sub-event
    ↓
Create Place Extraction Prompt
    ↓
Grok API (structured output)
    ↓
ULID Validation & Auto-fix
    ↓
Null Field Validation
    ↓
Add Bounding Boxes & Map URLs
    ↓
Find or Create Place File
    ↓
Add Event Mention
    ↓
Update Index
```

### Central Repository Structure

```
output/places/
├── index.json                    # Place lookup index
├── Normandy_01KHYP2M.json       # Region
├── Paris_01KHYP3N.json          # City
├── English_Channel_01KHYP4P.json # Sea
├── Rhine_River_01KHYP5Q.json    # River
└── ...
```

**Filename Format:** `{PlaceName}_{ULID_prefix}.json`

---

## Data Structure

### Place File Schema

```json
{
  "PlaceID": "01ULID...",
  "name": "Normandy",
  "current_name": "Normandy",
  "source_language": "English",
  "geography_type": "region",
  "historical_names": [
    { "name": "Normandie", "language": "French", "date_range": "1939-1945" }
  ],
  "aliases": [],
  "coordinates": {
    "latitude": 49.18,
    "longitude": -0.37,
    "precision": "approximate",
    "confidence": 0.8
  },
  "bounding_box_100km": {
    "north": 50.08,
    "south": 48.28,
    "east": 0.53,
    "west": -1.27
  },
  "map_urls": {
    "google_maps": "https://www.google.com/maps?q=49.18,-0.37",
    "openstreetmap": "https://www.openstreetmap.org/?mlat=49.18&mlon=-0.37&zoom=12"
  },
  "hierarchy": {
    "continent": "Europe",
    "country": "France",
    "region": "Normandy",
    "parent_place_id": null
  },
  "event_mentions": [
    {
      "MentionID": "01ULID...",
      "Event_Name": "Operation Overlord",
      "EventID": "01ULID...",
      "Sub_event_Name": "Planning phase",
      "Sub_eventID": "01ULID...",
      "book": "Cross-Channel Attack",
      "author": "Gordon A. Harrison",
      "series": "United States Army in World War II",
      "date_context": "June 1944",
      "DateMentionID": "01ULID..."
    }
  ]
}
```

**Cross-references:**
- `DateMentionID` in mentions → top-level `DateID` in `output/dates/*.json`
- `hierarchy.parent_place_id` → top-level `PlaceID` in another place file (when populated)

### Index Structure

```json
{
  "normandy": "Normandy_01KHYP2M.json",
  "paris": "Paris_01KHYP3N.json",
  "english channel": "English_Channel_01KHYP4P.json"
}
```

---

## Features

### 1. Central Repository

**One file per unique place:**
- Prevents duplication across chapters/books
- Enables cross-referencing
- Supports geographic queries

**Deduplication Logic:**
```python
# Normalize place name for lookup
place_key = place_name.lower().strip()

# Check if place already exists
if place_key in index:
    place_file = places_dir / index[place_key]
    # Add mention to existing file
else:
    # Create new place file
```

### 2. GPS Coordinates

**Required for all places:**
- Latitude: -90 to 90 (decimal degrees)
- Longitude: -180 to 180 (decimal degrees)
- Precision: 2-4 decimal places

**For large regions:**
- Use geographic center point
- Example: "Western Front" → center of region

### 3. Automatic Bounding Box

**100km radius around coordinates:**
```python
bounding_box = {
    "north": lat + 0.9,  # ~100km north
    "south": lat - 0.9,  # ~100km south
    "east": lon + 0.9,   # ~100km east
    "west": lon - 0.9    # ~100km west
}
```

**Use cases:**
- Geographic queries (find events near location)
- Map rendering
- Spatial analysis

### 4. Map Service URLs

**Automatically generated:**
```json
{
  "google_maps": "https://www.google.com/maps?q=49.18,-0.37",
  "openstreetmap": "https://www.openstreetmap.org/?mlat=49.18&mlon=-0.37&zoom=12"
}
```

**Benefits:**
- Direct links to modern maps
- Visual verification of coordinates
- User-friendly navigation

### 5. Place Type Classification

**Geography Types:**
- `city` - Urban area
- `town` - Small urban area
- `village` - Rural settlement
- `region` - Geographic region
- `province` - Administrative province
- `state` - Administrative state
- `country` - Nation state
- `continent` - Continent
- `sea` - Body of water (sea)
- `ocean` - Body of water (ocean)
- `river` - Waterway
- `lake` - Lake
- `mountain` - Mountain or range
- `island` - Island
- `peninsula` - Peninsula
- `military_base` - Military installation
- `battlefield` - Battle site
- `fortification` - Defensive structure
- `bridge` - Bridge
- `port` - Port or harbor
- `airfield` - Airfield or airport
- `other` - Other type

### 6. Historical Names & Aliases

**Tracks name changes (array of historical names):**
```json
{
  "current_name": "Gdańsk",
  "historical_names": [
    { "name": "Danzig", "language": "German", "date_range": "1939-1945" }
  ],
  "aliases": ["Gdansk"],
  "source_language": "German"
}
```

**Use cases:**
- Historical accuracy
- Cross-referencing
- Multi-language support

### 7. Event Context

**Date Context:**
- Temporal context for place mention
- Example: "June 1944", "morning of D-Day"

**Role in Event:**
- Place's function in the event
- Examples: "target of attack", "defensive position", "supply route"

### 8. Hierarchy (via Enrichment)

**Geographic hierarchy populated during enrichment:**
```json
{
  "hierarchy": {
    "continent": "Europe",
    "country": "France",
    "region": "Normandy",
    "parent_place_id": null
  }
}
```

Enrichment uses Grok + Wikipedia fallback to populate hierarchy for places missing this data.

---

## Configuration

No specific configuration options. Runs automatically in Phase 2.

**Enrichment:** Run `phase3_enrich_data.py` or use `enrich_all_places()` to populate hierarchy data.

---

## Usage

### Automatic (Phase 2)

```bash
python3 phase2_extract.py
```

Places extracted automatically after dates.

### Programmatic

```python
from pathlib import Path
from src.grok_client import GrokClient
from src.extraction.places import extract_places

grok_client = GrokClient(cache_dir=Path("cache/api"))

event_file = Path("output/BreakoutAndPursuit/chapter1-event.json")
parsed_file = Path("output/BreakoutAndPursuit/chapter1-parsed.json")
places_dir = Path("output/places")

extract_places(
    event_file=event_file,
    grok_client=grok_client,
    places_dir=places_dir,
    parsed_file=parsed_file,
    max_retries=3
)
```

---

## Output Files

### Place Files

**Location:** `output/places/{PlaceName}_{ID}.json`

**Examples:**
- `Normandy_01KHYP2M.json`
- `Paris_01KHYP3N.json`
- `English_Channel_01KHYP4P.json`

### Index File

**Location:** `output/places/index.json`

Maps normalized place names to filenames for fast lookup.

---

## Integration

### With Events
- Reads event files
- Extracts places from sub-event text
- Links via EventID and Sub-eventID

### With Dates
- Places and dates often co-occur
- Both link to same EventID/Sub-eventID
- Enables temporal-spatial queries

### With Maps (Optional)
- Map extraction links to PlaceID
- Maps show geographic context
- Visual representation of places

### With External Maps (Optional)
- External map search uses place names
- Links third-party maps to PlaceID
- Enriches place data with additional maps

---

## Error Handling

### Invalid Place Filtering

Automatically removes mentions with:
- Missing `current_name`
- Missing coordinates (latitude/longitude)
- Null required fields

```python
# Before filtering: 5 mentions
# After filtering: 3 mentions (2 invalid removed)
logger.debug("Removed place mention with null current_name")
```

### Null Field Fixing

**Automatic fixes:**
```python
# Null geography_type → "Unknown"
if mention.get("geography_type") is None:
    mention["geography_type"] = "Unknown"
```

### ULID Auto-fix

Invalid ULIDs automatically replaced:
```python
# Invalid: "01KHYP2M 4N6P8Q" (has space)
# Fixed:   "01KHYP2M4N6P8Q0R2S4T6V8W0X"
```

### Retry Logic

**Default:** 3 attempts per sub-event

**Behavior:**
- First attempt uses cache
- Retries bypass cache
- Continues to next sub-event on failure

---

## Performance

### Caching

All API responses cached in `cache/api/places/`

**Clear cache:**
```bash
rm -rf cache/api/places/*
```

### Processing Time

**Typical:** 5-15 seconds per sub-event

**Factors:**
- Text length
- Number of place mentions
- API response time
- Coordinate lookup time

---

## Examples

### Example 1: City

**Input Text:**
```
"The 1st Infantry Division advanced toward Paris on 25 August."
```

**Output:**
```json
{
  "PlaceMentionID": "01KHYP2M4N6P8Q0R2S4T6V8W0X",
  "current_name": "Paris",
  "historical_name": null,
  "source_language": "English",
  "latitude": 48.8566,
  "longitude": 2.3522,
  "geography_type": "city",
  "date_context": "25 August",
  "role_in_event": "objective of Allied advance",
  "original_text": "Paris"
}
```

### Example 2: Region

**Input Text:**
```
"Allied forces landed on the beaches of Normandy."
```

**Output:**
```json
{
  "PlaceMentionID": "01KHYP3N5Q7R9S1T3V5W7X9Y1Z",
  "current_name": "Normandy",
  "historical_name": null,
  "source_language": "English",
  "latitude": 49.18,
  "longitude": -0.37,
  "geography_type": "region",
  "date_context": null,
  "role_in_event": "location of Allied invasion",
  "original_text": "beaches of Normandy"
}
```

### Example 3: Historical Name

**Input Text:**
```
"German forces retreated from Danzig in March 1945."
```

**Output:**
```json
{
  "PlaceMentionID": "01KHYP4P6R8S0T2V4W6X8Y0Z2A",
  "current_name": "Gdańsk",
  "historical_name": "Danzig",
  "source_language": "German",
  "latitude": 54.3520,
  "longitude": 18.6466,
  "geography_type": "city",
  "date_context": "March 1945",
  "role_in_event": "location of German retreat",
  "original_text": "Danzig"
}
```

---

## API Reference

### `extract_places()`

Extract places from event file and add to central repository.

**Signature:**
```python
def extract_places(
    event_file: Path,
    grok_client: GrokClient,
    places_dir: Path,
    parsed_file: Optional[Path] = None,
    max_retries: int = 3
) -> Optional[Path]
```

**Parameters:**
- `event_file` (Path): Path to `*-event.json` file
- `grok_client` (GrokClient): Initialized Grok API client
- `places_dir` (Path): Central places directory (`output/places/`)
- `parsed_file` (Path, optional): Path to parsed file for book metadata
- `max_retries` (int): Maximum retry attempts per sub-event (default: 3)

**Returns:**
- `Path`: Path to places directory if places were extracted
- `None`: If no places were extracted

**Raises:**
- `ValueError`: If book metadata is missing

---

## Troubleshooting

### No places being extracted

**Check:**
1. Event file exists and has sub-events
2. Sub-event text contains place mentions
3. API key is set
4. Check logs for "Extracted 0 places"

### Places not deduplicating

**Check:**
1. Index file exists: `output/places/index.json`
2. Place name normalization working correctly
3. Check logs for "Created place file" vs "Added mention"

### Invalid coordinates

**Check logs for:**
```
DEBUG - Removed place 'Unknown Location' with null coordinates
```

This is expected - LLM sometimes can't determine coordinates.

### Duplicate place files

**Possible causes:**
1. Different spellings (e.g., "Normandy" vs "Normandie")
2. Index corruption
3. Concurrent writes

**Solution:**
Use `scripts/find_duplicate_places.py` and `scripts/merge_duplicate_places.py`

---

## Related Documentation

- [Events Extraction](../events/README.md)
- [Dates Extraction](../dates/README.md)
- [Maps Extraction](../maps/README.md)
- [External Maps](../external-maps/README.md)
- [Error Handling](../../core/error_handling.md)

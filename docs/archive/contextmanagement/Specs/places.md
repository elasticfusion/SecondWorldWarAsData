# Place Extraction - Central Repository

**Version:** 3.0.0  
**Status:** Active  
**Last Updated:** 2026-02-23

---

## Overview

Place extraction uses a **central repository pattern** where each unique geographic location has its own file with event mentions tracked across all documents.

---

## Architecture

### Central Repository Structure

```
output/places/
├── Warsaw_01H8XYZ8.json           # Individual place file
├── London_01LONDON.json
├── Normandy_01NORMANDY.json
├── index.json                      # Name → filename lookup
└── duplicate_report.json           # (optional) Deduplication report
```

### Index Format

Index maps normalized place names to filenames:

```json
{
  "warsaw": "Warsaw_01H8XYZ8.json",
  "london": "London_01LONDON.json",
  "normandy": "Normandy_01NORMANDY.json",
  "uk": "United_Kingdom_01UKGBR.json",
  "united kingdom": "United_Kingdom_01UKGBR.json",
  "brittany": "Brittany_01KHYP2M.json",
  "brittany peninsula": "Brittany_01KHYP2M.json"
}
```

**Features:**
- Case-insensitive lookup
- Includes aliases (UK → United Kingdom)
- Multiple names point to same file

---

## Place File Schema

### Structure

```json
{
  "PlaceID": "01H8XYZ8AB123CD456EF789GH",
  "current_name": "Warsaw",
  "historical_names": [
    {
      "name": "Warszawa",
      "language": "Polish",
      "date_range": "1939-1945"
    }
  ],
  "aliases": ["Warszawa"],
  "source_language": "English",
  "geography_type": "city",
  "coordinates": {
    "latitude": 52.2297,
    "longitude": 21.0122,
    "precision": "exact",
    "confidence": 1.0
  },
  "bounding_box_100km": {
    "north": 53.1297,
    "south": 51.3297,
    "east": 22.0122,
    "west": 20.0122
  },
  "map_urls": {
    "google_maps": "https://www.google.com/maps?q=52.2297,21.0122",
    "openstreetmap": "https://www.openstreetmap.org/?mlat=52.2297&mlon=21.0122&zoom=12"
  },
  "hierarchy": {
    "continent": "Europe",
    "country": "Poland",
    "parent_place_id": "01POLAND00..."
  },
  "event_mentions": [...],
  "related_places": [...]
}
```

### Core Fields

| Field | Type | Description |
|-------|------|-------------|
| `PlaceID` | string | 26-character ULID |
| `current_name` | string | Modern canonical name |
| `historical_names` | array | Names used during WWII |
| `aliases` | array | Alternative names (added during deduplication) |
| `source_language` | string | Language of source text |
| `geography_type` | string | Type of geographic feature |

### Geography Types

21 supported types:

- **Political:** `country`, `state`, `province`, `city`, `town`, `village`
- **Military:** `military_theater`, `military_base`, `fortification`
- **Geographic:** `region`, `peninsula`, `island`, `archipelago`, `mountain`, `mountain_range`, `valley`, `plain`
- **Water:** `sea`, `ocean`, `river`, `lake`, `channel`

### Coordinates

```json
{
  "latitude": 52.2297,
  "longitude": 21.0122,
  "precision": "exact",
  "confidence": 1.0
}
```

**Precision:**
- `exact` - Specific location known
- `approximate` - Estimated center
- `region_center` - Geographic center of region

**Confidence:**
- `1.0` - High confidence
- `0.8` - Medium confidence
- `0.5` - Low confidence

### Bounding Box

100km radius box for spatial queries:

```json
{
  "north": 53.1297,
  "south": 51.3297,
  "east": 22.0122,
  "west": 20.0122
}
```

### Map URLs

Links to modern map services:

```json
{
  "google_maps": "https://www.google.com/maps?q=52.2297,21.0122",
  "openstreetmap": "https://www.openstreetmap.org/?mlat=52.2297&mlon=21.0122&zoom=12"
}
```

### Event Mention Structure

```json
{
  "MentionID": "01...",
  "Event_Name": "Invasion of Poland",
  "EventID": "01...",
  "Sub_event_Name": "...",
  "Sub_eventID": "01...",
  "book": "Cross-Channel Attack",
  "author": "Gordon A. Harrison",
  "series": "United States Army in World War II",
  "date_context": "September 1939",
  "DateMentionID": null,
  "role_in_event": "target city",
  "original_text": "Warsaw"
}
```

---

## Features

### 1. Central Repository

**One file per unique place:**
- Deduplicates identical places across documents
- Tracks all event mentions for each place
- Enables spatial analysis and mapping

### 2. Alias Management

**Handled via `place_aliases.yaml`:**
- Normalization rules (remove " peninsula", " region", etc.)
- Large region types (merge by name, ignore distance)
- Merge distance threshold (50km for specific places)
- Geographical relationships (British Isles contains UK)

**Example:**
```yaml
normalization_rules:
  - " peninsula"
  - " region"
  - " of operations"
  - " theater"

large_region_types:
  - region
  - military_theater
  - continent
  - country
```

### 3. Deduplication

**Automatic detection:**
- Exact name matches
- Semantic matches (Brittany vs Brittany Peninsula)
- Coordinate proximity (within 50km)
- Large regions (ignore distance)

**Scripts:**
- `scripts/find_duplicate_places.py` - Detect duplicates
- `scripts/merge_duplicate_places.py` - Merge and add aliases

### 4. Hierarchical Relationships

**Political subdivisions:**
```json
{
  "hierarchy": {
    "continent": "Europe",
    "country": "Poland",
    "parent_place_id": "01POLAND00..."
  }
}
```

**Related places:**
```json
{
  "related_places": [
    {
      "PlaceID": "01POLAND00...",
      "relationship": "part_of"
    }
  ]
}
```

**Relationship types:**
- `contains` - Place contains another
- `part_of` - Place is part of another
- `near` - Places are nearby
- `connected_by_route` - Connected by road/rail
- `same_as` - Same place (alias)

### 5. Historical Names

**Track name changes:**
```json
{
  "historical_names": [
    {
      "name": "Danzig",
      "language": "German",
      "date_range": "1939-1945"
    }
  ],
  "current_name": "Gdańsk"
}
```

### 6. Map Integration

**Auto-generated URLs:**
- Google Maps (coordinates)
- OpenStreetMap (coordinates + zoom)

**Backfill script:**
- `scripts/fix_place_map_urls.py` - Add missing URLs

### 7. Incremental Updates

**Non-destructive:**
- Existing place files are updated, not replaced
- New event mentions are appended
- Duplicate mentions (same sub-event) are skipped

### 8. Book Metadata

**Source tracking:**
- Book title, author, series
- Required for all mentions
- Enables citation and provenance

### 9. Contextual Information

**Per mention:**
- `date_context` - When place was mentioned
- `role_in_event` - Place's role (target, defensive position, etc.)
- `original_text` - Exact text from document

---

## Extraction Process

### 1. Load Event File

Read event JSON with sub-events and full text.

### 2. Load Book Metadata

Read from parsed file:
- Book title (required)
- Author (required)
- Series (optional)

Raises error if missing.

### 3. Extract Places per Sub-event

Use Grok API with structured outputs:
- Extract all place mentions
- Get coordinates (or geographic center)
- Classify geography type
- Extract date context and role

### 4. Find or Create Place Files

For each extracted place:
- Normalize name (lowercase)
- Check index for existing file
- Create new file if needed
- Generate map URLs

### 5. Add Event Mentions

For each place file:
- Check for duplicate (same sub-event)
- Append new mention if unique
- Save updated file

### 6. Update Index

Save index mapping names to filenames (includes aliases).

---

## Usage

### Extract Places

```python
from src.extraction.places import extract_places

places_dir = extract_places(
    event_file=Path("output/book/chapter1-event.json"),
    grok_client=grok_client,
    places_dir=Path("output/places"),
    parsed_file=Path("output/book/chapter1-parsed.json")
)
```

### Query Places

```python
import json
from pathlib import Path

# Load index
with open("output/places/index.json") as f:
    index = json.load(f)

# Find Warsaw
place_file = index.get("warsaw")
if place_file:
    with open(f"output/places/{place_file}") as f:
        place_data = json.load(f)
    print(f"Warsaw mentioned in {len(place_data['event_mentions'])} events")
```

### Find Duplicates

```bash
python3 scripts/find_duplicate_places.py
```

### Merge Duplicates

```bash
python3 scripts/merge_duplicate_places.py
```

---

## File Naming Convention

**Format:** `{Name}_{ULID8}.json`

**Examples:**
- `Warsaw_01H8XYZ8.json`
- `London_01LONDON.json`
- `Normandy_01NORMANDY.json`
- `United_Kingdom_01UKGBR.json`
- `European_Theater_of_Operations_01KHYP2M.json`

**Rules:**
- Spaces preserved in name
- Underscores separate name from ULID
- ULID truncated to 8 characters for readability

---

## Deduplication

### Place Matching

Places are considered duplicates if:

**Exact match:**
- Same name (case-insensitive)
- Same coordinates (rounded to 2 decimals)

**Semantic match:**
- Same normalized name (after removing suffixes)
- Within distance threshold OR large region type

**Examples:**
- "Brittany" + "Brittany Peninsula" → Same (normalized to "brittany")
- "European Theater" + "European Theater of Operations" → Same (large region)
- "Belle-Isle" (47.35, -3.22) + "Belle-Isle" (47.35, -3.22) → Same (exact coords)

### Normalization Rules

From `place_aliases.yaml`:

```yaml
normalization_rules:
  - " peninsula"
  - " region"
  - " of operations"
  - " theater"
```

**Applied:**
- "Brittany Peninsula" → "brittany"
- "European Theater of Operations" → "european"

### Large Region Types

From `place_aliases.yaml`:

```yaml
large_region_types:
  - region
  - military_theater
  - continent
  - country
```

**Behavior:**
- Merge by normalized name only
- Ignore coordinate distance
- Reason: Large regions have approximate/varying coordinates

### Merge Process

1. **Find duplicates** - Detect by name and coordinates
2. **Choose primary** - Keep file with most mentions
3. **Merge mentions** - Combine all event mentions
4. **Add aliases** - Add other names as aliases
5. **Delete duplicates** - Remove duplicate files
6. **Rebuild index** - Update index with aliases

---

## Configuration

### place_aliases.yaml

**Location:** Project root

**Sections:**
- `normalization_rules` - Strings to remove from names
- `large_region_types` - Geography types to merge by name
- `merge_distance_km` - Distance threshold (default: 50)
- `geographical_relationships` - Non-political containment
- `aliases` - Explicit alias mappings
- `temporal_boundaries` - Historical border changes
- `hierarchies` - Political subdivisions

**Example:**
```yaml
normalization_rules:
  - " peninsula"
  - " region"

large_region_types:
  - region
  - military_theater

merge_distance_km: 50

geographical_relationships:
  - geographical_region: "British Isles"
    contains:
      - place: "United Kingdom"
        relationship: "geographical_part"
```

---

## Limitations

### 1. Role in Event

**Status:** Partially implemented

The `role_in_event` field is extracted but may be `null` if:
- Not mentioned in text
- Grok doesn't extract it
- Pydantic model needs update

### 2. Date Context

**Status:** Partially implemented

The `date_context` field is extracted but may be `null` if:
- No date mentioned with place
- Grok doesn't extract it

### 3. DateMentionID

**Status:** Not implemented (TODO)

The `DateMentionID` field is always `null`. Should link to:
- Date extraction files
- Connect temporal and spatial data
- Enable "where was X on date Y" queries

**Blocked by:** Date extraction refactor (now complete)

### 4. Coordinate Validation

**No validation for:**
- Invalid coordinates (lat > 90, lon > 180)
- Coordinates outside WWII theater
- Impossible locations (ocean for city)

### 5. Historical Boundaries

**Not handled:**
- Places that changed countries (Gdańsk/Danzig)
- Borders that shifted (Poland 1939 vs 1945)
- Dissolved countries (Yugoslavia, Czechoslovakia)

**Tracked in:** `place_aliases.yaml` but not applied

---

## Schema Version

**Current:** 3.0.0  
**Schema File:** `contextmanagement/Specs/place_v3_central.json`

**Changes from v2:**
- Central repository (was per-chapter)
- Event mentions array
- Role in event field
- Date context field
- Map URLs
- Bounding box
- Related places

---

## Related Documentation

- **Schema:** `contextmanagement/Specs/place_v3_central.json`
- **Code:** `src/extraction/places.py`
- **Phase 2:** `phase2_extract.py`
- **Aliases:** `place_aliases.yaml`
- **Deduplication:** `DUPLICATE_PLACES_FIX.md`
- **Config:** `PLACE_DEDUPLICATION_CONFIG.md`

---

## Scripts

### Deduplication

- `scripts/find_duplicate_places.py` - Detect duplicates
- `scripts/merge_duplicate_places.py` - Merge duplicates

### Maintenance

- `scripts/fix_place_map_urls.py` - Backfill missing map URLs
- `scripts/consolidate_places.py` - Apply aliases from YAML (TODO)
- `scripts/suggest_place_aliases.py` - AI-powered alias suggestions (TODO)

---

## Future Enhancements

1. **DateMentionID Linking:** Connect places to dates
2. **Historical Boundaries:** Apply temporal boundary changes
3. **Coordinate Validation:** Validate coordinates are reasonable
4. **Hierarchy Auto-detection:** Infer parent places from coordinates
5. **Route Tracking:** Track movement routes between places
6. **Place Clustering:** Group related places (same battle)
7. **Gazetteer Integration:** Link to external gazetteers
8. **Visualization:** Generate maps with place markers

---

**Status:** ✅ Production Ready (with noted limitations)

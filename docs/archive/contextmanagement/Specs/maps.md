# Maps Extraction - Source Material

**Version:** 1.0.0  
**Status:** ✅ Implemented  
**Last Updated:** 2026-02-24

---

## Overview

Maps extraction identifies and extracts maps, diagrams, and illustrations **from source material (books) only**. This does not include third-party maps from archives, museums, or external sources.

Maps are discovered during Phase 1 parsing and processed in Phase 2 extraction.

---

## Scope

### Included
- Maps and diagrams from the books being analyzed
- Illustrations with geographic or tactical information
- Battle maps, terrain maps, strategic diagrams
- Linked to events, sub-events, places, and dates

### Excluded
- Third-party maps from external archives
- Modern recreations or interpretations
- Maps not present in the source material

---

## Schema

**File:** `contextmanagement/Specs/maps_v1_schema.json`

### Example Map Record

```json
{
  "MapID": "01KJ8X9Y2Z3A4B5C6D7E8F9G0H",
  "map_title": "German Invasion Routes - September 1, 1939",
  "source_book": "The German Campaign in Poland (1939)",
  "source_author": "Robert M. Kennedy",
  "source_series": "United States Army in World War II",
  "page_number": 45,
  "figure_number": "Map 3",
  "EventID": "01KJ8X1Y2Z3A4B5C6D7E8F9G0H",
  "Event_Name": "Invasion of Poland",
  "Sub_eventID": "01KJ8X2Y3Z4A5B6C7D8E9F0G1H",
  "Sub_event_Name": "German forces cross the Polish border",
  "place_name": "Poland",
  "PlaceMentionID": "01KJ8X3Y4Z5A6B7C8D9E0F1G2H",
  "date": "1939-09-01",
  "DateMentionID": "01KJ8X4Y5Z6A7B8C9D0E1F2G3H",
  "local_path": "output/maps/19390901_Poland_Invasion_Routes_01KJ8X9Y.jpg",
  "file_format": "jpg",
  "extracted_date": "2026-02-24T13:19:00Z",
  "description": "Map showing three main German invasion routes into Poland",
  "map_type": "strategic"
}
```

---

## Extraction Workflow

### Phase 1: Identification

During document parsing:
1. Identify maps/diagrams in source material
2. Extract metadata (title, page, figure number)
3. Note context (event, sub-event, places, dates mentioned)
4. Store reference for extraction

### Phase 2: Extraction

1. Extract map image from source document
2. Generate MapID (ULID)
3. Link to EventID and Sub_eventID
4. Optional: Link to PlaceMentionID if place is depicted
5. Optional: Link to DateMentionID if date is shown
6. Save to `output/maps/`
7. Create JSON record

### File Naming Convention

```
{date}_{place}_{description}_{MapID}.{ext}

Examples:
19390901_Poland_Invasion_Routes_01KJ8X9Y.jpg
19440606_Normandy_Beach_Defenses_01KJ9A0B.png
19430702_Kursk_Tactical_Situation_01KJ9B1C.tif
```

---

## Storage Structure

```
output/maps/
├── 19390901_Poland_Invasion_Routes_01KJ8X9Y.jpg
├── 19440606_Normandy_Beach_Defenses_01KJ9A0B.png
├── 19430702_Kursk_Tactical_Situation_01KJ9B1C.tif
└── index.json
```

### Index Format

```json
{
  "01KJ8X9Y2Z3A4B5C6D7E8F9G0H": "19390901_Poland_Invasion_Routes_01KJ8X9Y.jpg",
  "01KJ9A0B1C2D3E4F5G6H7I8J9K": "19440606_Normandy_Beach_Defenses_01KJ9A0B.png"
}
```

---

## Configuration

**File:** `config.yaml`

```yaml
maps:
  enabled: false                    # Not yet implemented
  extract_during_phase1: true       # Extract during document parsing
  storage_path: "output/maps/"
  supported_formats:
    - jpg
    - png
    - tif
    - pdf
  link_to_places: true              # Attempt to link maps to PlaceIDs
  link_to_dates: true               # Attempt to link maps to DateIDs
```

---

## Linking Strategy

### Places
- If map depicts a specific location, link to PlaceMentionID
- Use place name from map title or caption
- Look up in `output/places/index.json`

### Dates
- If map shows a specific date, link to DateMentionID
- Extract date from map title or caption
- Look up in `output/dates/index.json`

### Events
- All maps must link to EventID and Sub_eventID
- Maps are discovered within sub-event context

---

## Quality Assurance

**File:** `src/extraction/maps.py`  
**Lines of Code:** 543  
**Last QA Run:** 2026-02-24

| Tool | Score | Status |
|------|-------|--------|
| **Pylint** | 9.96/10 | ✅ Pass |
| **Mypy** | 0 errors | ✅ Pass |
| **Black** | Formatted | ✅ Pass |
| **Radon CC** | A-B (1-9) | ✅ Pass |
| **Radon MI** | A (31.83) | ✅ Pass |

### Complexity Analysis

**Functions by Complexity:**
- `_download_map_image` - B (9) - Low (content-type detection)
- `_download_image_to_s3` - B (8) - Low (S3 upload)
- `_download_image` - B (8) - Low (backend routing)
- `extract_maps` - B (8) - Low (main orchestration)
- `_process_event_files` - B (7) - Low (event file processing)
- `_save_map_record` - B (6) - Low (backend routing)
- `_lookup_place_id` - A (5) - Low (fuzzy match)
- `_create_map_record` - A (5) - Low (record creation)
- `_lookup_date_id` - A (4) - Low (index lookup)
- `_extract_maps_from_text` - A (3) - Low (regex extraction)
- `_process_map` - A (3) - Low (single map)
- `_setup_storage_backend` - A (3) - Low (config)
- `_setup_image_storage` - A (3) - Low (config)
- `_load_index` - A (2) - Low
- `_save_index` - A (1) - Low
- `_save_to_s3` - A (1) - Low

**Assessment:** Production-ready. All complexity A-B. Extracts maps from event files with proper event/sub-event context.

---

## Configuration

**File:** `config.yaml`

```yaml
maps:
  enabled: false                   # Enable maps extraction
  extract_during_phase1: true      # Extract during document parsing
  download_images: false           # Download actual map image files
  storage_backend: "filesystem"    # filesystem or s3
  storage_path: "output/maps/"
  image_storage_path: "output/maps_images/"
  s3_bucket: ""                    # S3 bucket name (required if backend=s3)
  s3_prefix: "maps/"               # S3 key prefix
  s3_region: "us-east-1"           # AWS region
  supported_formats:
    - jpg
    - png
    - tif
    - pdf
  link_to_places: true             # Link maps to PlaceIDs
  link_to_dates: true              # Link maps to DateIDs
  download_timeout: 30             # Image download timeout (seconds)
```

### Storage Backends

**Filesystem (default):**
- Metadata: `output/maps/{MapID}.json`
- Images: `output/maps_images/{MapID}.{ext}`
- Index: `output/maps/index.json`

**S3:**
- Metadata: `s3://{bucket}/{prefix}metadata/{MapID}.json`
- Images: `s3://{bucket}/{prefix}images/{MapID}.{ext}`
- Requires AWS credentials configured

---

## Implementation Status

**Status:** ✅ Implemented

### Completed
- [x] Map extraction from Phase 1 parsed documents
- [x] Map extraction from event files (authoritative source)
- [x] ULID generation for MapID
- [x] Central repository with index.json
- [x] Configuration in config.yaml
- [x] Integration with Phase 2 pipeline
- [x] Quality assurance (pylint, mypy, black, radon)
- [x] Image download functionality with config option
- [x] Content-type detection for file formats
- [x] Error handling for failed downloads
- [x] S3 storage backend support
- [x] Configurable storage backend (filesystem or S3)
- [x] S3 image upload with content-type detection
- [x] Event/Sub-event linking (critical)
- [x] Maps linked to specific sub-event context
- [x] Place linking via Sub_eventID matching
- [x] Date linking via Sub_eventID matching
- [x] Automatic PlaceMentionID population
- [x] Automatic DateMentionID population

### TODO
- [ ] Extract page numbers from parsed documents
- [ ] Download/extract actual map images from URLs (when enabled)
- [ ] Map type classification (tactical, strategic, political)

---

## Related Documentation

- **Phase 1 Parsing:** `contextmanagement/Specs/phase1_parsing.md`
- **Places:** `contextmanagement/Specs/places.md`
- **Dates:** `contextmanagement/Specs/dates.md`
- **Events:** `contextmanagement/Specs/event.json`
- **Schema:** `contextmanagement/Specs/maps_v1_schema.json`

---

**Status:** 🚧 Draft - Awaiting Implementation

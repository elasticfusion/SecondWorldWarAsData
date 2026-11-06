# External Maps - Third-Party Sources

**Version:** 2.0.0  
**Status:** ✅ Implemented (AI-Ready)  
**Last Updated:** 2026-02-24

---

## Overview

Search for and catalog **third-party maps from external archives, museums, websites, or other sources** that relate to events in the source material but are NOT contained in the books themselves.

**Critical:** Uses place-based event lookup. Maps link to places, which already contain event/sub-event context. This is more efficient than searching all events.

**AI-Ready:** Grok can populate `external_maps.yaml` by searching online for maps. Source documentation (`found_via`, `found_date`) is preserved for later human review.

This is separate from the existing maps extraction (v1.0.0) which extracts maps FROM the source books.

**Implementation:** `src/extraction/external_maps.py`

---

## Scope

### Included
- Third-party maps from external archives (National Archives, Imperial War Museum, etc.)
- Modern recreations or interpretations
- Maps from other historical sources
- Battle maps, terrain maps, strategic diagrams from external sources
- Linked to events, sub-events, places, and dates from our corpus

### Excluded
- Maps already in the source material (handled by maps v1.0.0)
- Modern Google Maps/OpenStreetMap (already in place records)

---

## Schema Differences from v1.0.0

### New Required Fields
```json
{
  "external_source": "National Archives",
  "external_source_url": "https://catalog.archives.gov/id/12345",
  "license": "Public Domain",
  "license_url": "https://...",
  "archive_id": "NARA-12345",
  "date_created": "1944-06-06",
  "creator": "U.S. Army Signal Corps"
}
```

### Removed Fields
- `source_book` - Not from a book
- `source_author` - Not from a book
- `source_series` - Not from a book
- `page_number` - Not from a book
- `figure_number` - Not from a book

### Modified Fields
- `source_url` - Now points to external archive, not book URL
- `map_title` - Title from external source

---

## Example External Map Record

```json
{
  "MapID": "01KJ8X9Y2Z3A4B5C6D7E8F9G0H",
  "map_title": "Normandy Invasion - D-Day Beaches",
  "external_source": "National Archives",
  "external_source_url": "https://catalog.archives.gov/id/531424",
  "archive_id": "NARA-531424",
  "license": "Public Domain",
  "license_url": "https://www.archives.gov/legal/public-domain",
  "date_created": "1944-06-06",
  "creator": "U.S. Army Signal Corps",
  "EventID": "01KJ8X1Y2Z3A4B5C6D7E8F9G0H",
  "Event_Name": "Operation Overlord",
  "Sub_eventID": "01KJ8X2Y3Z4A5B6C7D8E9F0G1H",
  "Sub_event_Name": "D-Day landings",
  "place_name": "Normandy",
  "PlaceMentionID": "01KJ8X3Y4Z5A6B7C8D9E0F1G2H",
  "date": "1944-06-06",
  "DateMentionID": "01KJ8X4Y5Z6A7B8C9D0E1F2G3H",
  "local_path": "output/external_maps/01KJ8X9Y.json",
  "local_image_path": "cache/external_maps/NARA_531424_01KJ8X9Y.jpg",
  "source_url": "https://catalog.archives.gov/OpaAPI/media/531424/content/...",
  "file_format": "jpg",
  "extracted_date": "2026-02-24T13:19:00Z",
  "description": "Detailed tactical map of D-Day landing beaches showing unit positions",
  "map_type": "tactical",
  "storage_backend": "filesystem"
}
```

---

## Discovery Strategy

### Context Requirement
**All external maps MUST link to Event/Sub-event context:**
- EventID (required) - Parent event from corpus
- Sub_eventID (required) - Specific sub-event
- PlaceMentionID (automatic) - Linked if place exists in that event
- DateMentionID (automatic) - Linked if date exists in that event

Maps without event context cannot be imported.

### Manual Curation (Implemented)
1. Review events/sub-events in corpus
2. Search external archives for maps related to those events
3. Create YAML file with map metadata + place keywords
4. Import script matches place keywords → finds event context from place
5. Automatic place mention/date linking via event context
6. Download images if license permits (future enhancement)

### Automated Search (Implemented)
1. Script reads all places from `output/places/*.json`
2. Extracts place name, date, event context from each place
3. Grok searches online archives for related maps
4. Validates required fields (title, source, URL)
5. Checks for duplicates by external_source_url
6. Imports maps directly with full metadata
7. Links to events via place's event_mentions

**See:** `docs/current/EXTERNAL_MAPS_AUTOMATED_SEARCH.md`

### Automated Search (Phase 2 - Future)
1. Extract key terms from events (dates, places, operations)
2. Query external APIs (NARA, IWM, etc.)
3. Filter results by relevance to specific events
4. Present for human review with suggested event links
5. Import approved maps with event context

---

## External Sources

### Primary Archives
- **National Archives (NARA)** - https://catalog.archives.gov
- **Imperial War Museum** - https://www.iwm.org.uk
- **Library of Congress** - https://www.loc.gov
- **Bundesarchiv (Germany)** - https://www.bundesarchiv.de
- **Archives Nationales (France)** - https://www.archives-nationales.culture.gouv.fr

### License Considerations
- Public Domain (preferred)
- Creative Commons licenses
- Fair Use (research/educational)
- Requires attribution

---

## Storage Structure

```
output/external_maps/
├── NARA_531424_01KJ8X9Y.json
├── IWM_67890_01KJ9A0B.json
└── index.json

cache/external_maps/
├── NARA_531424_01KJ8X9Y.jpg
└── IWM_67890_01KJ9A0B.png
```

Separate from `output/maps/` (source material maps).

---

## Configuration

**File:** `config.yaml`

```yaml
external_maps:
  enabled: false                    # Not yet implemented
  storage_path: "output/external_maps/"
  image_storage_path: "cache/external_maps/"
  storage_backend: "filesystem"     # filesystem or s3
  s3_bucket: ""
  s3_prefix: "external_maps/"
  download_images: true             # Download from external sources
  download_timeout: 60              # Longer timeout for external sources
  require_license: true             # Require license information
  allowed_licenses:
    - "Public Domain"
    - "CC0"
    - "CC-BY"
    - "CC-BY-SA"
  classification_keywords:          # Same as maps v1.0.0
    tactical: [attack, assault, advance, retreat, defense, battle]
    strategic: [campaign, theater, front, invasion, offensive, deployment]
    logistical: [supply, logistics, transport, route, port]
    political: [border, territory, zone, occupation, political]
```

---

## Implementation Plan

### Phase 1: Manual Import (Recommended Start)
1. Create `external_maps.yaml` with curated map metadata
2. Build import script `src/extraction/external_maps.py`
3. Link to existing events/places/dates
4. Download images if permitted by license

### Phase 2: API Integration (Future)
1. Integrate with NARA API
2. Integrate with IWM API
3. Automated search based on event keywords
4. Human review workflow

---

## Example YAML Input

**File:** `external_maps.yaml`

```yaml
maps:
  - title: "Normandy Invasion - D-Day Beaches"
    external_source: "National Archives"
    external_source_url: "https://catalog.archives.gov/id/531424"
    archive_id: "NARA-531424"
    license: "Public Domain"
    license_url: "https://www.archives.gov/legal/public-domain"
    date_created: "1944-06-06"
    creator: "U.S. Army Signal Corps"
    description: "Detailed map of D-Day landing beaches"
    map_type: "tactical"
    file_url: "https://catalog.archives.gov/OpaAPI/media/531424/content/..."
    
    # Place Context (REQUIRED) - Import script matches to place → event
    place_keywords: ["Normandy"]
    # Date linking (AUTOMATIC) - Matched via event context
    date: "1944-06-06"
    
  - title: "Operation Market Garden - Allied Airborne Assault"
    external_source: "Imperial War Museum"
    external_source_url: "https://www.iwm.org.uk/collections/item/object/205123456"
    archive_id: "IWM-205123456"
    license: "CC-BY-NC"
    license_url: "https://creativecommons.org/licenses/by-nc/4.0/"
    date_created: "1944-09-17"
    creator: "British Army"
    description: "Map showing airborne drop zones and objectives"
    map_type: "tactical"
    file_url: "https://..."
    
    # Place Context (REQUIRED)
    place_keywords: ["Arnhem", "Nijmegen"]
    # Date linking (AUTOMATIC)
    date: "1944-09-17"
```

---

## Linking Logic

### Place-Based Event Lookup (Required)
1. **place_keywords** in YAML matched against place names (filename or place_name field)
2. Place file contains `event_mentions` array with EventID/Sub_eventID
3. First non-null event mention provides event context
4. Map record created with EventID/Sub_eventID from place
5. **Maps without place match are rejected**

**Why place-based?** Places already contain event linkage via `event_mentions`. More efficient than searching all event files.

### Place Mention Linking (Automatic)
1. Once EventID/Sub_eventID established from place
2. Use Sub_eventID to find specific PlaceMentionID
3. Links map to exact place mention in that sub-event

### Date Linking (Automatic)
1. Once EventID/Sub_eventID established, search dates repository
2. Find dates with `event_mentions` containing that Sub_eventID
3. If date matches, link to DateMentionID
4. Date from YAML used to find exact match

**Result:** All external maps have full event/sub-event/place/date context, working backwards from places.

---

## Implementation Status

**Status:** ✅ Implemented (AI-Ready)

### Completed
- [x] Create external maps schema (v2.0.0)
- [x] Build YAML import script (`src/extraction/external_maps.py`)
- [x] Add license validation (including "Unknown" for AI)
- [x] Implement place-based event lookup
- [x] Add place/date linking via event context
- [x] Add external source tracking (`found_via`, `found_date`)
- [x] Integrate with Phase 2 pipeline
- [x] Apply error handling patterns
- [x] Pass quality assurance (pylint 10/10, mypy, bandit)
- [x] Create AI population guide

### Future Enhancements
- [ ] Automated search via archive APIs
- [ ] Image download from external sources
- [ ] S3 storage backend support

---

## Related Documentation

- **Automated Search:** `docs/current/EXTERNAL_MAPS_AUTOMATED_SEARCH.md` (recommended)
- **User Guide:** `docs/current/EXTERNAL_MAPS.md`
- **AI Guide:** `docs/current/EXTERNAL_MAPS_AI_GUIDE.md` (YAML approach)
- **Source Material Maps:** `contextmanagement/Specs/maps.md` (v1.0.0 - implemented)
- **Places:** `contextmanagement/Specs/places.md`
- **Dates:** `contextmanagement/Specs/dates.md`
- **Events:** `contextmanagement/Specs/event.json`

---

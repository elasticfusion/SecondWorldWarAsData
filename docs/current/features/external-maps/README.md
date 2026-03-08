# External Maps

**Version:** 2.0.0  
**Status:** ✅ Implemented (AI-Ready)  
**Last Updated:** 2026-02-24

---

## Overview

Import and catalog maps from third-party sources (archives, museums, external websites) that relate to events in your corpus. These are separate from maps extracted from source material (v1.0.0).

**Key principle:** Uses place-based event lookup. Maps link to places, which already contain event/sub-event context. This is more efficient than searching all events.

**Two Approaches:**
1. **Automated Search (Recommended):** Grok searches online for all places automatically
2. **Manual YAML:** Curate specific maps in `external_maps.yaml`

See: `docs/current/EXTERNAL_MAPS_AUTOMATED_SEARCH.md` for automated workflow.

---

## Quick Start

### Automated Search (Recommended)

Search for maps across all 220 places automatically:

```bash
# Test with 5 places
python3 -m src.extraction.search_external_maps

# Process all places (edit max_places=None in script)
python3 -m src.extraction.search_external_maps
```

See: `docs/current/EXTERNAL_MAPS_AUTOMATED_SEARCH.md`

### Manual YAML Curation

For specific maps you want to add:

### 1. Enable in Config

Edit `config.yaml`:
```yaml
external_maps:
  enabled: true  # Set to true
```

### 2. Create YAML File

**Option A: Manual Curation**
```bash
cp external_maps.yaml.example external_maps.yaml
```

Edit with your maps:
```yaml
maps:
  - title: "Your Map Title"
    external_source: "National Archives"
    external_source_url: "https://..."
    license: "Public Domain"
    place_keywords: ["Normandy"]  # REQUIRED - matches place names
    date: "1944-06-06"            # Optional
```

**Option B: AI Population**

Grok can populate `external_maps.yaml` by searching online:
- Searches archives/museums for maps related to corpus places
- Documents sources with `found_via` and `found_date`
- License can be "Unknown" for later review
- See: `docs/current/EXTERNAL_MAPS_AI_GUIDE.md`

### 3. Run Phase 2

External maps import automatically during Phase 2:
```bash
python3 phase2_extract.py
```

Or run standalone:
```bash
python3 -m src.extraction.external_maps
```

### 4. Review Output

Maps saved to `output/external_maps/`:
```bash
ls output/external_maps/
```

---

## Configuration

Edit `config.yaml`:

```yaml
external_maps:
  enabled: false                   # Set to true to enable
  storage_path: "output/external_maps/"
  require_license: true            # Reject maps without license
  allowed_licenses:
    - "Public Domain"
    - "CC0"
    - "CC-BY"
  download_images: false           # Download map images
```

---

## Linking Logic

### Place-Based Event Lookup (Required)
1. **place_keywords** matched against place names (filename or place_name field)
2. Place file contains `event_mentions` array with EventID/Sub_eventID
3. First non-null event mention provides event context
4. **Maps without place match are rejected**

**Why place-based?** Places already contain event linkage. More efficient than searching all events.

### Place Mention Linking (Automatic)
- Uses Sub_eventID to find specific PlaceMentionID
- Links map to exact place mention in that sub-event

### Date Linking (Automatic)
- Searches dates with `event_mentions` containing Sub_eventID
- Matches date from YAML
- Links to DateMentionID

**Result:** Full event/sub-event/place/date context, working backwards from places.

---

## YAML Format

### Required Fields
```yaml
title: "Map title"
external_source: "Archive name"
external_source_url: "https://..."
license: "Public Domain"
place_keywords: ["Place1", "Place2"]  # Matches place names
```

### Optional Fields
```yaml
archive_id: "NARA-531424"
license_url: "https://..."
date_created: "1944-06-06"
creator: "U.S. Army Signal Corps"
description: "Brief description"
map_type: "tactical"  # tactical, strategic, political, logistical
file_url: "https://..."
date: "1944-06-06"
found_via: "Search query or discovery method"  # For AI population
found_date: "2026-02-24"                       # When entry was added
```

---

## Supported Licenses

Default allowed licenses (configurable in `config.yaml`):
- Public Domain
- CC0
- CC-BY
- CC-BY-SA
- Unknown (for AI population - human review later)

To allow additional licenses, edit `config.yaml`:
```yaml
external_maps:
  allowed_licenses:
    - "Public Domain"
    - "CC0"
    - "CC-BY"
    - "CC-BY-NC"  # Add this
```

---

## Output Format

Each map saved as JSON in `output/external_maps/`:

```json
{
  "MapID": "01HXYZ...",
  "map_title": "Normandy Invasion - D-Day Beaches",
  "external_source": "National Archives",
  "external_source_url": "https://...",
  "archive_id": "NARA-531424",
  "license": "Public Domain",
  "EventID": "01HXYZ...",
  "Event_Name": "D-Day",
  "Sub_eventID": "01HXYZ...",
  "Sub_event_Name": "Normandy Landings",
  "PlaceMentionID": "01HXYZ...",
  "DateMentionID": "01HXYZ...",
  "extracted_date": "2026-02-24T20:00:00Z"
}
```

---

## Examples

### National Archives Map
```yaml
- title: "Normandy Invasion - D-Day Beaches"
  external_source: "National Archives"
  external_source_url: "https://catalog.archives.gov/id/531424"
  archive_id: "NARA-531424"
  license: "Public Domain"
  place_keywords: ["Normandy"]
  date: "1944-06-06"
  creator: "U.S. Army Signal Corps"
  description: "Detailed map of D-Day landing beaches"
  map_type: "tactical"
```

### Imperial War Museum Map
```yaml
- title: "Operation Market Garden"
  external_source: "Imperial War Museum"
  external_source_url: "https://www.iwm.org.uk/..."
  archive_id: "IWM-205123456"
  license: "CC-BY-NC"
  license_url: "https://creativecommons.org/licenses/by-nc/4.0/"
  place_keywords: ["Arnhem", "Netherlands"]
  date: "1944-09-17"
```

---

## Troubleshooting

### "No place match for keywords"
- Check place_keywords match place names
- Review places: `jq -r '.place_name' output/places/*.json | grep -i keyword`
- Check filenames: `ls output/places/ | grep -i keyword`
- Place must have non-null event_mentions

### "No event mentions in place"
- Place file must contain event_mentions array
- Check: `jq '.event_mentions' output/places/PlaceName*.json`
- At least one mention must have Event_Name and Sub_event_Name

### License Rejected
- Check `config.yaml` allowed_licenses
- Add license type if appropriate
- Verify license field matches exactly

---

## See Also

- **Automated Search:** `docs/current/EXTERNAL_MAPS_AUTOMATED_SEARCH.md` (recommended)
- **AI Guide:** `docs/current/EXTERNAL_MAPS_AI_GUIDE.md` (YAML approach)
- **Specification:** `contextmanagement/Specs/external_maps.md`
- **Schema:** `contextmanagement/Specs/external_maps_v2_schema.json`
- **Source Material Maps:** `docs/current/MAPS.md` (v1.0.0)
- **Example YAML:** `external_maps.yaml.example`

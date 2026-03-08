# External Maps - AI Population Guide

**For:** Grok AI  
**Purpose:** Populate `external_maps.yaml` with maps found online  
**Date:** 2026-02-24

---

## Your Task

Search for historical maps online that relate to places/events in the WWII corpus and add them to `external_maps.yaml` with full source documentation.

## Workflow

1. **Review corpus places** - Check `output/places/*.json` for place names
2. **Search online** - Find maps from archives, museums, websites
3. **Add to YAML** - Document each map with source information
4. **Import runs automatically** - Phase 2 pipeline links maps to events
5. **Human review later** - User reviews sources outside this workflow

## Required Fields

```yaml
- title: "Map title from source"
  external_source: "Archive/museum/website name"
  external_source_url: "URL where you found the map"
  license: "Public Domain" # or "CC0", "CC-BY", "Unknown"
  place_keywords: ["Normandy"] # Place names to link to corpus
```

## Optional Fields (include when available)

```yaml
  archive_id: "NARA-531424"
  license_url: "https://..."
  date_created: "1944-06-06"
  creator: "U.S. Army Signal Corps"
  description: "Brief description"
  map_type: "tactical" # tactical, strategic, political, logistical
  file_url: "https://direct-image-url.jpg"
  date: "1944-06-06"
  found_via: "Search query: 'Normandy D-Day maps'"
  found_date: "2026-02-24"
```

## Source Documentation

**Critical:** Always document where you found the map:
- `external_source_url` - URL where map was found (REQUIRED)
- `found_via` - How you discovered it (search query, recommendation, etc.)
- `found_date` - When you added this entry

This allows human review later without constraining your search.

## License Handling

Allowed licenses (configurable):
- Public Domain
- CC0
- CC-BY
- CC-BY-SA

If license is unclear, use `"Unknown"` - user can review later.

## Place Keywords

**Critical:** `place_keywords` must match place names in `output/places/*.json`

The import script:
1. Matches your keywords to place filenames (e.g., `Normandy_01KJ3KMK.json`)
2. Extracts event context from place's `event_mentions`
3. Links map to EventID/Sub_eventID automatically

**Tip:** Check place files to see what names exist:
```bash
ls output/places/ | head -20
```

## Example Entry

```yaml
- title: "Normandy Invasion - D-Day Beaches"
  external_source: "National Archives"
  external_source_url: "https://catalog.archives.gov/id/531424"
  archive_id: "NARA-531424"
  license: "Public Domain"
  license_url: "https://www.archives.gov/legal/public-domain"
  date_created: "1944-06-06"
  creator: "U.S. Army Signal Corps"
  description: "Detailed tactical map showing D-Day landing beaches and Allied positions"
  map_type: "tactical"
  file_url: "https://catalog.archives.gov/OpaAPI/media/531424/content/..."
  place_keywords: ["Normandy"]
  date: "1944-06-06"
  found_via: "NARA catalog search: 'D-Day maps'"
  found_date: "2026-02-24"
```

## Search Strategies

### Good Sources
- National Archives (NARA) - https://catalog.archives.gov
- Imperial War Museum - https://www.iwm.org.uk
- Library of Congress - https://www.loc.gov
- Bundesarchiv (Germany) - https://www.bundesarchiv.de
- University digital collections
- Museum collections

### Search Tips
- Use place names from corpus
- Search for "WWII [place] map"
- Look for tactical/strategic maps
- Prefer archival sources over modern recreations
- Check license/copyright before adding

## Output

Maps are saved to `output/external_maps/{MapID}.json` with:
- Full event/sub-event context (from place linkage)
- PlaceMentionID (automatic)
- DateMentionID (automatic if date matches)
- All source documentation you provided

## Testing

After adding entries:
```bash
python3 phase2_extract.py 2>&1 | grep -A30 "Importing external maps"
```

Check output:
```bash
jq '.' output/external_maps/*.json
```

## Notes

- No manual review required before import
- Source documentation preserved for later review
- License can be "Unknown" if unclear
- Multiple maps per place are allowed
- Duplicates are automatically detected (same sub-event + same URL)

---

**File to edit:** `external_maps.yaml`  
**See also:** `docs/current/EXTERNAL_MAPS.md`

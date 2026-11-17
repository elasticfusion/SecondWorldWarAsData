# Maps Extraction Requirements

**Status:** Superseded by `maps.md`  
**Replaced:** 2026-02-24

This file has been replaced by comprehensive documentation:
- **Specification:** `contextmanagement/Specs/maps.md`
- **Schema:** `contextmanagement/Specs/maps_v1_schema.json`

## Original Requirements (Archived)

Maps extraction shall:
- Review source material for maps (books only, no third-party sources)
- Include event name and EventID
- Include sub-event name and Sub_eventID where maps are identified
- Include a ULID (MapID) for each entry
- Note metadata (title, page number, figure number)
- Extract from source material during Phase 1 parsing
- Include download/extraction option in config file
- If tied to specific places:
  - Note place name
  - Link to PlaceMentionID from output/places
- If tied to specific date:
  - Note the date
  - Link to DateMentionID from output/dates

**See `maps.md` for full specification.**
        
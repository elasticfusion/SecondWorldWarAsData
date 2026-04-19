# JSON Schema Reference

**Last Updated:** 2026-03-22
**Schema Version:** 2.0

All entity files use 26-character ULIDs for cross-referencing. Cross-references always point to top-level entity IDs (e.g., `DateMentionID` → `DateID` in a date file, `PlaceMentionID` → `PlaceID` in a place file).

> **Note:** This document describes the **output file format** — what is stored on disk after extraction and consolidation. This differs from the extraction-time schemas in `src/json_schemas.py`, which validate intermediate results returned by the LLM during pipeline execution. Where the two diverge (field names, enum values), this document reflects the final output.

---

## Cross-Reference Convention

All entity types follow a consistent pattern for linking:

| Field Name | Points To | Target Field |
|---|---|---|
| `DateMentionID` | `output/dates/*.json` | `DateID` (top-level) |
| `DateID` | `output/dates/*.json` | `DateID` (top-level) |
| `PlaceMentionID` | `output/places/*.json` | `PlaceID` (top-level) |
| `EventID` | `output/{Book}/*-event.json` | `Event.EventID` |
| `Sub_eventID` | `output/{Book}/*-event.json` | `Event.Sub-events[].Sub-eventID` |
| `PersonID` | `output/people/*.json` | `PersonID` (top-level) |
| `GroupID` / `PeopleGroupID` | `output/people_groups/*.json` | `GroupID` (top-level) |
| `EquipmentID` | `output/equipment/*.json` | `EquipmentID` (top-level) |
| `WeatherID` | `output/weather/*.json` | `WeatherID` (top-level) |
| `LogisticsID` | `output/logistics/*.json` | `LogisticsID` (top-level) |

---

## Events — `output/{Book}/*-event.json`

Per-chapter event files containing hierarchical sub-events.

```json
{
  "Chapter": "The Breakthrough Idea",
  "Book": "BreakoutAndPursuit",
  "Event": {
    "EventID": "01ULID...",
    "Event_Name": "The Breakthrough Idea",
    "Sub-events": [
      {
        "Sub-eventID": "01ULID...",
        "Sub-event_summary": "Brief description of action",
        "Sub-event_fulltext": { "p_145": "Full paragraph text..." },
        "Endnote_References": [],
        "Footnote_References": [],
        "dates": ["01ULID..."],
        "places": ["01ULID..."],
        "people": ["01ULID..."],
        "peoplegroups": ["01ULID..."]
      }
    ]
  }
}
```

Sub-event entity arrays contain top-level entity IDs (DateID, PlaceID, PersonID, GroupID).

---

## Dates — `output/dates/*.json` (356 files)

```json
{
  "DateID": "01ULID...",
  "date_start": "1944-06-06",
  "date_end": null,
  "date_precision": "exact|early|mid|late|spring|summer|fall|winter",
  "time_start": "06:30|null",
  "time_end": null,
  "time_precision": "exact|approximate|null",
  "time_source": "German|Allied|Zulu|Local|null",
  "original_text": "6 June 1944",
  "normalized_datetime": null,
  "event_mentions": [
    {
      "MentionID": "01ULID...",
      "Event_Name": "...",
      "EventID": "01ULID...",
      "Sub_event_Name": "...",
      "Sub_eventID": "01ULID...",
      "book": "Cross-Channel Attack",
      "author": "Gordon A. Harrison",
      "series": "United States Army in World War II"
    }
  ]
}
```

---

## Places — `output/places/*.json` (1138 files)

```json
{
  "PlaceID": "01ULID...",
  "name": "Caen",
  "current_name": "Caen",
  "source_language": "English",
  "geography_type": "city|town|village|country|region|province|state|sea|ocean|river|lake|mountain|island|peninsula|continent|military_base|battlefield|fortification|bridge|port|airfield|other",
  "historical_names": [{ "name": "...", "language": "French", "date_range": "1939-1945" }],
  "aliases": [],
  "coordinates": {
    "latitude": 49.18,
    "longitude": -0.37,
    "precision": "exact|approximate|center_point|estimated",
    "confidence": 0.8
  },
  "bounding_box_100km": { "north": 50.08, "south": 48.28, "east": 0.53, "west": -1.27 },
  "map_urls": {
    "google_maps": "https://www.google.com/maps?q=49.18,-0.37",
    "openstreetmap": "https://www.openstreetmap.org/?mlat=49.18&mlon=-0.37&zoom=12"
  },
  "hierarchy": { "continent": "Europe", "country": "France", "region": "Normandy" },
  "related_places": [{ "PlaceID": "01ULID...", "relationship": "contains|part_of|near|connected_by_route|same_as" }],
  "event_mentions": [
    {
      "MentionID": "01ULID...",
      "Event_Name": "...", "EventID": "01ULID...",
      "Sub_event_Name": "...", "Sub_eventID": "01ULID...",
      "book": "...", "author": "...", "series": "...",
      "date_context": "1944-06-06",
      "DateMentionID": "01ULID...",
      "role_in_event": "battle location|null",
      "original_text": "exact quote|null"
    }
  ]
}
```

---

## People — `output/people/*.json` (470 files)

```json
{
  "PersonID": "01ULID...",
  "name": "Omar N. Bradley",
  "source_language": "English",
  "biographical_profile": {
    "birth_date": "1893-02-12",
    "death_date": "1981-04-08",
    "nationality": "American",
    "biographical_details": "...",
    "ranks": [{ "rank": "General", "branch": "US Army", "date": "1945-03-12" }],
    "units_served": [{ "unit": "First Army", "from": "1944-01", "to": "1944-08" }],
    "military_awards": [{ "award": "...", "class": null, "date_awarded": null }],
    "biography_sources": [{ "source": "Wikipedia", "confidence": 0.9, "fields_sourced": ["birth_date"], "page": null }]
  },
  "event_mentions": [
    {
      "MentionID": "01ULID...",
      "Event_Name": "...", "EventID": "01ULID...",
      "Sub_event_Name": "...", "Sub_eventID": "01ULID...",
      "book": "...", "author": "...", "series": "...",
      "date": "1944-06-06",
      "DateMentionID": "01ULID..."
    }
  ]
}
```

---

## People Groups — `output/people_groups/*.json` (328 files)

```json
{
  "GroupID": "01ULID...",
  "name": "1st Infantry Division",
  "group_name": "1st Infantry Division",
  "group_type": "military_unit|country|alliance|political_party|government_organization|anti_government_organization|religious_organization",
  "military_hierarchy": "division|corps|army|regiment|battalion|brigade|...",
  "source_language": "English",
  "country_of_origin": "USA",
  "alliance_membership": ["Allied Powers"],
  "common_name": "Big Red One",
  "description": "...",
  "parent_organization": "V Corps",
  "enrichment_data": {
    "full_name": "...", "unit_type": "infantry_division", "branch": "US Army",
    "nationality": "American", "formed_date": "1917-06-08", "disbanded_date": null,
    "parent_unit": "V Corps", "description": "...",
    "commanding_officers": [{ "name": "...", "from_date": "...", "to_date": "..." }],
    "notable_operations": ["Operation Overlord"]
  },
  "members": [{ "PersonID": "01ULID...", "name": "...", "role": "Commander", "confidence": 0.9, "source": "enrichment", "from_date": null, "to_date": null }],
  "event_mentions": [
    {
      "MentionID": "01ULID...",
      "Event_Name": "...", "EventID": "01ULID...",
      "Sub_event_Name": "...", "Sub_eventID": "01ULID...",
      "book": "...", "author": "...", "series": "...",
      "date": "1944-06-06",
      "DateMentionID": "01ULID..."
    }
  ]
}
```

---

## Weather — `output/weather/*.json` (79 files)

Deduplicated by date+location. One file per unique weather observation.

```json
{
  "WeatherID": "01ULID...",
  "date": "1944-06-06",
  "DateID": "01ULID...",
  "location": {
    "place_name": "Normandy",
    "PlaceID": "01ULID...",
    "latitude": 49.35,
    "longitude": -0.50
  },
  "source_type": "extracted|api|hybrid",
  "extracted_data": {
    "description": "Heavy overcast with rain",
    "temperature": null,
    "temperature_unit": "celsius|fahrenheit|null",
    "measurement_system": "metric|imperial|null",
    "notable_impact": "Delayed air support",
    "original_text": "exact quote from source",
    "book": "...", "author": "..."
  },
  "api_data": {
    "provider": "open-meteo",
    "retrieved_at": "2026-03-22T16:53:30Z",
    "temperature_max_c": 16.2, "temperature_min_c": 12.8,
    "precipitation_mm": 4.8, "windspeed_max_kmh": 23.2,
    "cloud_cover_percent": 91,
    "raw_response": { "...": "full Open-Meteo response" }
  },
  "event_mentions": [
    {
      "MentionID": "01ULID...",
      "Event_Name": "...", "EventID": "01ULID...",
      "Sub_event_Name": "...", "Sub_eventID": "01ULID...",
      "book": "...", "author": "...", "series": "..."
    }
  ]
}
```

---

## Equipment — `output/equipment/*.json` (100 files)

```json
{
  "EquipmentID": "01ULID...",
  "common_name": "M4 Sherman",
  "technical_identifier": "M4A1",
  "category": "armor|aircraft|naval|artillery|infantry_weapons|communications|vehicles|uniforms|other",
  "subcategory": "medium_tank",
  "country_of_origin": "USA",
  "description": "...",
  "alternate_names": ["Sherman"],
  "variants": [{ "variant_name": "M4A3E8", "description": "..." }],
  "specifications": { "weight": "33 tons", "crew": 5, "armament": "75mm M3 gun", "range": "120 miles" },
  "extracted_date": "2026-03-15T...",
  "mentions": [
    {
      "MentionID": "01ULID...",
      "EventID": "01ULID...", "Sub_eventID": "01ULID...",
      "DateID": "01ULID...", "date": "1944-07-25",
      "context": "Attack on St. Lô",
      "original_text": "The Shermans advanced...",
      "paragraph_numbers": [145, 146],
      "using_unit": { "name": "2nd Armored Division", "PeopleGroupID": "01ULID..." },
      "using_person": { "name": "...", "PersonID": "01ULID..." },
      "supporting_units": [{ "unit_name": "...", "support_type": "air", "PeopleGroupID": "01ULID..." }],
      "performance_notes": {
        "successes": [], "failures": [],
        "field_modifications": [], "maintenance_issues": []
      }
    }
  ]
}
```

Note: Equipment uses `mentions` (not `event_mentions`) and `DateID`/`DateMentionID` (both present).

---

## Logistics — `output/logistics/*.json` (826 files)

```json
{
  "LogisticsID": "01ULID...",
  "logistics_type": "supply_shortage|supply_excess|delivery_delay|transport_disruption",
  "category": "ammunition|fuel|food|medical|equipment|personnel|general",
  "description": "...",
  "severity": "critical|high|medium|low",
  "status": "unresolved|in_progress|resolved|worsened",
  "temporal": {
    "date_start": "1944-06-10",
    "date_end": "1944-06-15|null",
    "date_type": "exact|approximate",
    "DateID_start": "01ULID...",
    "DateID_end": "01ULID...|null"
  },
  "extracted_date": "2026-03-15T...",
  "event_mentions": [
    {
      "EventMentionID": "01ULID...",
      "EventID": "01ULID...",
      "Sub_eventID": "01ULID...",
      "context": "...",
      "paragraph_numbers": [200, 201]
    }
  ]
}
```

Note: Logistics uses `EventMentionID` (not `MentionID`) in event_mentions.

---

## Casualties — `output/casualties/*.json` (955 files)

```json
{
  "CasualtyID": "01ULID...",
  "type": "casualties|killed|wounded|pow|missing",
  "description": "...",
  "event_context": {
    "EventID": "01ULID...",
    "Sub-eventID": "01ULID..."
  },
  "source": {
    "book": "Breakout and Pursuit",
    "chapter": "The Breakthrough Idea",
    "paragraph_number": null
  },
  "count": { "total": 500, "killed": 100, "wounded": 300, "missing": 50, "captured": 50 },
  "impacted_organizations": [
    { "name": "29th Infantry Division", "PeopleGroupID": "01ULID...", "nationality": "American", "role": "attacking" }
  ],
  "impacted_people": [],
  "impacted_places": [],
  "impacted_equipment": [
    { "common_name": "M4 Sherman", "EquipmentID": "01ULID...|null", "count_lost": 5 }
  ]
}
```

Note: Casualties use `event_context` (not `event_mentions`). `Sub-eventID` uses hyphen (not underscore). The `type` value `pow` means "prisoner of war".

---

## Images — `output/images/*.json` (86 files)

```json
{
  "ImageID": "01ULID...",
  "image_title": "...",
  "image_type": "photograph|map|diagram|...",
  "content_type": "...",
  "source": "...",
  "resource_type": "...",
  "url": "https://...",
  "local_copy": "path/to/file|null",
  "url_capture_date": null,
  "license": "public_domain|...",
  "description": "...",
  "extracted_date": "2026-03-15T...",
  "EventID": "01ULID...",
  "Event_Name": "...",
  "Sub-eventID": "01ULID...",
  "Sub-event_Name": "...",
  "place_name": null,
  "PlaceMentionID": "01ULID...|null",
  "date": "1944-06-06|null",
  "DateMentionID": "01ULID...|null"
}
```

Note: Images use `Sub-eventID` (hyphen) and `Sub-event_Name` (hyphen). No `event_mentions` array — event context is top-level.

---

## Bibliography — `output/bibliography/*.json` (2105 files)

```json
{
  "BibliographyID": "01ULID...",
  "title": "...",
  "alt_title": null,
  "citation": {
    "title": "...", "alt_title": null,
    "author": ["Harrison, Gordon A."],
    "publisher": "...", "publication_date": "1951",
    "publication_location": "Washington, D.C.",
    "publication_country": "USA",
    "document_type": "book|memo|report|...",
    "volume": null, "edition": null, "pages": null,
    "isbn": null, "isbn_edition": null,
    "periodical_name": null, "translator": null,
    "first_edition_date": null, "author_death_date": null
  },
  "availability": "public_domain|restricted|...",
  "resource_urls": ["https://..."],
  "archive_reference_number": null,
  "archive_physical_address": null,
  "license": "public_domain|...",
  "license_notes": "...",
  "mentions": [
    {
      "MentionID": "01ULID...",
      "EventID": "01ULID...",
      "Sub-eventID": "01ULID...",
      "book": "Cross-Channel Attack",
      "chapter": "The Breakthrough Idea",
      "reference_type": "endnote|footnote",
      "reference_number": "1",
      "verbatim_reference": "Harrison, Cross-Channel Attack, p. 234"
    }
  ]
}
```

Note: Bibliography uses `Sub-eventID` (hyphen) in mentions. The extraction-time schema (`src/json_schemas.py`) uses `MaterialID` per item, while the consolidated output uses `BibliographyID`. Similarly, the extraction-time `availability` enum is `online|offline|archive|unknown`, while the output format uses `public_domain|restricted|unknown`.

---

## Maps — `output/maps/*.json` (55 files)

```json
{
  "MapID": "01ULID...",
  "map_title": "Operation Cobra - Breakthrough",
  "map_type": null,
  "source_book": "Breakout and Pursuit",
  "source_author": "Martin Blumenson",
  "source_series": "United States Army in World War II",
  "page_number": null,
  "figure_number": "Map XII",
  "EventID": "01ULID...",
  "Event_Name": "...",
  "Sub_eventID": "01ULID...",
  "Sub_event_Name": "...",
  "place_name": null,
  "PlaceMentionID": "01ULID...|null",
  "date": null,
  "DateMentionID": "01ULID...|null",
  "description": "...",
  "source_url": "https://...",
  "local_path": "output/maps/01ULID.json",
  "local_image_path": "path|null",
  "file_format": "png|jpg|null",
  "storage_backend": "local|s3",
  "extracted_date": "2026-03-15T..."
}
```

Note: Maps use `Sub_eventID` (underscore) — inconsistent with images/bibliography which use hyphen.

---

## Known Inconsistencies

| Issue | Entities Affected | Notes |
|---|---|---|
| `Sub-eventID` vs `Sub_eventID` | Images/Bibliography use hyphen; Maps/Equipment/Logistics use underscore | Legacy; both resolve correctly |
| `mentions` vs `event_mentions` | Equipment uses `mentions`; most others use `event_mentions` | Equipment predates convention |
| `EventMentionID` vs `MentionID` | Logistics uses `EventMentionID`; others use `MentionID` | Logistics predates convention |
| `event_context` vs `event_mentions` | Casualties use `event_context` object; others use `event_mentions` array | Casualties are single-event |

---

## Index Files

Each entity directory contains an `index.json` mapping lookup keys to filenames:

```json
{
  "caen": "Caen_01ULID.json",
  "normandy": "Normandy_01ULID.json"
}
```

Equipment and maps directories also contain `.processed_events.json` tracking which event files have been processed.

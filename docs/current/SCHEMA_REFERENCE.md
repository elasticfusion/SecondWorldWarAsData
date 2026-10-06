# JSON Schema Reference

**Last Updated:** 2026-10-06
**Schema Version:** 2.24

All entity files use 26-character ULIDs for cross-referencing. Cross-references always point to top-level entity IDs (e.g., `DateMentionID` → `DateID` in a date file, `PlaceMentionID` → `PlaceID` in a place file).

All entity files include internal metadata fields (prefixed with `_`):

| Field | Type | Description |
|---|---|---|
| `_schema_version` | string | Output format version (currently "2.24") |
| `_last_updated` | string | ISO date of last modification (e.g., "2026-10-06") |

These are auto-injected by `src/schemas.inject_metadata()` at write time and excluded from schema validation via `patternProperties: {"^_": {}}`.

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
| `MapID` | `output/map_features/*.json` | `MapID` (FeatureCollection top-level) |

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
        "groups": ["01ULID..."],
        "equipment": ["01ULID..."]
      }
    ]
  }
}
```

Sub-event entity arrays contain top-level entity IDs (DateID, PlaceID, PersonID, GroupID, EquipmentID). `Chapter` may be a string, object, or null. The only required fields are `Event` (top-level), `Event.EventID`, `Event.Sub-events`, and each sub-event's `Sub-eventID`.

---

## Dates — `output/dates/*.json` (356 files)

```json
{
  "DateID": "01ULID...",
  "date_start": "1944-06-06",
  "date_end": null,
  "date_precision": "exact|early|mid|late|seasonal|approximate|spring|summer|fall|winter",
  "time_start": "06:30|null",
  "time_end": null,
  "time_precision": "exact|approximate|null",
  "time_source": "German|Allied|Zulu|Local|null",
  "original_text": "6 June 1944",
  "normalized_datetime": null,
  "resolved_earliest": "1944-06-06T00:00:00Z|null",
  "resolved_latest": "1944-06-06T23:59:59Z|null",
  "resolution_method": "precision_rule|range|unresolved|null",
  "summary": "synthesized significance summary|null",
  "summary_source": "synthesized|null",
  "summary_generated_at": "2026-10-06T...|null",
  "summary_mention_count": 12,
  "summary_mentions_hash": "sha...|null",
  "mention_count": 12,
  "event_mentions": [
    {
      "DateMentionID": "01ULID...",
      "EventID": "01ULID...",
      "Sub_eventID": "01ULID...|null",
      "book": "Cross-Channel Attack",
      "chapter": "...",
      "time_start": "06:30|null",
      "original_text": "6 June 1944|null"
    }
  ]
}
```

Notes:
- `date_start` is the only required field besides `DateID`. `date_start`/`original_text` remain the verbatim authority; `resolved_earliest`/`resolved_latest` are the derived, queryable ISO-8601 bounds (`resolution_method` records how they were derived).
- `time_precision` and `time_source` are free-form nullable strings (not enforced enums).
- `summary*` fields are a derived convenience layer synthesized strictly from this date's own `event_mentions`.
- Date `event_mentions` items use `DateMentionID` (not `MentionID`) and carry only `EventID`, `Sub_eventID`, `book`, `chapter`, `time_start`, `original_text`.

---

## Places — `output/places/*.json` (1138 files)

```json
{
  "PlaceID": "01ULID...",
  "name": "Caen",
  "current_name": "Caen",
  "source_language": "English",
  "geography_type": "city|town|...|other (free-form string)",
  "historical_names": ["..."],
  "aliases": [],
  "coordinates": {
    "latitude": 49.18,
    "longitude": -0.37,
    "precision": "exact|approximate|estimated",
    "confidence": 0.8
  },
  "bounding_box": { "north": 50.08, "south": 48.28, "east": 0.53, "west": -1.27 },
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
      "Sub_event_Name": "...", "Sub_eventID": "01ULID...|null",
      "book": "...", "author": "...", "series": "...",
      "context": "...|null",
      "date_context": "1944-06-06",
      "DateMentionID": "01ULID...|null",
      "role_in_event": "battle location|null",
      "original_text": "exact quote|null",
      "nationality": "...|null"
    }
  ],
  "enrichment_status": "enriched|not_found|null",
  "last_enrichment_search": "2026-05-04|null"
}
```

Notes:
- `PlaceID` is the only required field. `precision` enum is `exact|approximate|estimated`; `relationship` and `geography_type` are free-form nullable strings (common values shown).
- `historical_names` and `aliases` are arrays of **strings** (not objects).
- The bounding box field is `bounding_box` (not `bounding_box_100km`); `hierarchy` may be an object or an array.

---

## People — `output/people/*.json` (470 files)

```json
{
  "PersonID": "01ULID...",
  "name": "Omar N. Bradley",
  "source_language": "English",
  "rank": "General",
  "nationality": "USA",
  "side": "allied|axis|neutral|civilian|null",
  "enrichment_status": "enriched|not_found",
  "last_enrichment_search": "2026-05-04",
  "openserp_searched": true,
  "images": [{"url": "https://...", "title": "...", "source": "openserp"}],
  "academic_references": [{"url": "https://...", "title": "...", "type": "oral_history|video|academic|archive", "source": "openserp"}],
  "military_awards": [{"url": "https://...", "title": "...", "source": "..."}],
  "biographical_profile": {
    "birth_date": "1893-02-12",
    "death_date": "1981-04-08",
    "nationality": "USA",
    "nationality_served": null,
    "role_type": null,
    "primary_group_id": "01ULID… (optional derived pointer to the principal unit GroupID; set by resolution/dedup, not extraction)",
    "biographical_details": "...",
    "ranks": ["General", "Lieutenant General"],
    "military_awards": ["Distinguished Service Cross", "..."],
    "aliases": ["..."],
    "biography_sources": ["Wikipedia", "..."],
    "units_served": ["9th Infantry Division", "..."],
    "group_affiliations": [{ "group": "9th Infantry Division", "GroupID": "01ULID…|null", "group_kind": null, "implied_from_title": false, "date_verified": false, "as_of_source_date": null, "source_title": null }],
    "education": ["United States Military Academy"],
    "wikipedia_url": "https://...",
    "grokipedia_url": "https://..."
  },
  "event_mentions": [
    {
      "EventID": "01ULID...",
      "Sub-eventID": "01ULID...",
      "book": "...",
      "chapter": "..."
    }
  ]
}
```

Notes:
- `PersonID` and `name` are required.
- Inside `biographical_profile`, `ranks`, `military_awards`, `aliases`, `biography_sources`, `units_served`, and `education` are arrays of **strings** (not objects). Structured per-mention award provenance is tracked separately (see Award Sourcing); `group_affiliations` is the array of objects linking people to units.
- Top-level `military_awards` is a separate enrichment list of `{url, title, source}` objects (distinct from `biographical_profile.military_awards`).
- People `event_mentions` use the shared minimal shape (`EventID`, `Sub-eventID` with hyphen, `book`, `chapter`) — no `MentionID`, `Event_Name`, `date`, or `DateMentionID`.

---

## People Groups — `output/people_groups/*.json` (328 files)

```json
{
  "GroupID": "01ULID...",
  "name": "1st Infantry Division",
  "group_name": "1st Infantry Division",
  "common_name": "Big Red One",
  "group_type": "military_unit (free-form nullable string)",
  "military_hierarchy": "division (object, array, string, or null)",
  "source_language": "English",
  "nationality": "American",
  "country_of_origin": "USA",
  "alliance_membership": ["Allied Powers"],
  "description": "...",
  "aliases": ["..."],
  "parent_organization": "V Corps",
  "sub_organizations": ["16th Infantry Regiment", "..."],
  "enrichment_data": {
    "full_name": "...", "unit_type": "infantry_division", "branch": "US Army",
    "nationality": "American", "formed_date": "1917-06-08", "disbanded_date": null,
    "parent_unit": "V Corps", "description": "...",
    "commanding_officers": [{ "name": "...", "from_date": "...", "to_date": "..." }],
    "notable_operations": ["Operation Overlord"]
  },
  "event_mentions": [
    {
      "MentionID": "01ULID...",
      "Event_Name": "...", "EventID": "01ULID...",
      "Sub_event_Name": "...", "Sub_eventID": "01ULID...|null",
      "book": "...", "author": "...", "series": "...",
      "context": "...|null",
      "original_text": "...|null",
      "role_in_event": "...|null"
    }
  ],
  "enrichment_status": "enriched|not_found|ambiguous|null",
  "enrichment_ambiguity_reason": "...|null",
  "last_enrichment_search": "2026-05-04|null"
}
```

Notes:
- `GroupID` is the only required field. `group_type` is a free-form nullable string; `military_hierarchy` may be an object, array, string, or null.
- `enrichment_data` is a free-form object (no enforced inner properties; common fields shown).
- Group `event_mentions` carry `MentionID`, `EventID`, `Event_Name`, `Sub_eventID`, `Sub_event_Name`, `book`, `author`, `series`, `context`, `original_text`, `role_in_event` — no `date` or `DateMentionID`.

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
  "source_type": "extracted|api_only|hybrid",
  "time_source": "Allied|German|Zulu|Local|null",
  "extracted_data": {
    "description": "Heavy overcast with rain",
    "temperature": null,
    "temperature_unit": "C|F|null",
    "measurement_system": "metric|imperial|null",
    "precipitation_text": "3 inches of snow",
    "precipitation_amount": 3,
    "precipitation_unit": "in|cm|mm|null",
    "precipitation_type": "snow|rain|sleet|hail|mixed|null",
    "notable_impact": "Delayed air support",
    "original_text": "exact quote from source",
    "book": "...", "author": "..."
  },
  "api_data": {
    "provider": "open-meteo",
    "data_type": "reanalysis",
    "retrieved_at": "2026-03-22T16:53:30Z",
    "temperature_max_c": 16.2, "temperature_min_c": 12.8,
    "precipitation_mm": 4.8, "windspeed_max_kmh": 23.2,
    "cloud_cover_percent": 91,
    "raw_response": { "...": "full Open-Meteo response" }
  },
  "noaa_observed": {
    "temperature_high_c": -1.0, "temperature_low_c": -8.0, "temperature_avg_c": -4.5,
    "precipitation_mm": 2.0, "snowfall_mm": 81.3, "snow_depth_mm": 150.0,
    "wind_speed_ms": 3.1,
    "wind_gust_fastest2min_ms": 7.2, "wind_gust_fastest5sec_ms": 9.4,
    "wind_dir_fastest2min_deg": 230, "wind_dir_fastest5sec_deg": 240,
    "raw_elements": { "WT01": 1, "WESD": 5.0 },
    "station_id": "GHCND:BE000006447", "station_name": "ANTWERPEN/DEURNE, BE",
    "station_latitude": 51.19, "station_longitude": 4.46, "station_distance_km": 142.3,
    "source": "noaa_cdo", "source_url": "https://www.ncei.noaa.gov/...",
    "data_type": "observed"
  },
  "event_mentions": [
    { "EventID": "01ULID...", "Sub-eventID": "01ULID..." }
  ],
  "enrichment_status": "enriched|not_found|null",
  "last_enrichment_search": "2026-10-06"
}
```

---

## Equipment — `output/equipment/*.json` (100 files)

```json
{
  "EquipmentID": "01ULID...",
  "common_name": "M4 Sherman",
  "technical_identifier": "M4A1",
  "canonical_name": "M4 Sherman",
  "identity_source": "exact|alias|learned_alias|fuzzy|grok_disambiguation|null",
  "category": "armor (free-form nullable string)",
  "subcategory": "medium_tank",
  "country_of_origin": "USA",
  "description": "...",
  "aliases": ["Sherman"],
  "alternate_names": ["Sherman"],
  "variants": [
    {
      "variant_name": "M4A3E8",
      "differences": "...",
      "alternate_names": ["Easy Eight"],
      "specifications": { "weight": "...", "crew": 5, "armament": "..." },
      "images": [{ "url": "https://...", "source": "...", "image_scope": "representative|documentary|null", "vision_verified": true }]
    }
  ],
  "specifications": {
    "weight": "33 tons", "weight_kg": 30000, "speed": "...", "max_speed_kmh": 38,
    "armament": "75mm M3 gun", "main_armament": "...", "armor": "...",
    "crew": 5, "range": "120 miles", "range_km": 193
  },
  "related_equipment": [
    { "relationship": "predecessor|successor|variant|null", "name": "...", "basis": "...", "EquipmentID": "01ULID...|null", "original_text": "...|null" }
  ],
  "media": [],
  "images": [
    { "url": "https://...", "local_path": "...|null", "source": "...", "license": "...", "caption": "...", "image_scope": "representative|documentary|null", "vision_verified": true }
  ],
  "external_data": {
    "grokipedia_url": "https://...|null",
    "wikipedia_url": "https://...|null",
    "additional_sources": [
      { "source_type": "...", "source_name": "...", "url": "https://...", "data_points": [{ "field": "...", "value": "...", "verified": true }] }
    ]
  },
  "crew_accounts": [
    { "PersonID": "01ULID...|null", "person_name": "...", "role": "...", "observations": "...", "original_text": "...", "book": "..." }
  ],
  "environmental_performance": [
    { "condition": "sub-zero temperatures", "effect": "...", "original_text": "..." }
  ],
  "timeline": {
    "first_production": "...", "first_combat_use": "...", "last_combat_use": "...",
    "total_produced": 49000, "combat_losses": null, "source": "...", "source_url": "https://..."
  },
  "technical_evolution": [
    { "date": "...", "change": "...", "reason": "...", "effectiveness": "...", "source": "...", "source_url": "https://..." }
  ],
  "logistics": {
    "fuel_consumption": "...", "ammunition_capacity": "...",
    "maintenance_hours_per_100_miles": 10, "common_spare_parts": ["..."],
    "supply_challenges": ["..."], "source": "...", "source_url": "https://..."
  },
  "extracted_date": "2026-03-15T...",
  "event_mentions": [
    {
      "MentionID": "01ULID...",
      "EventID": "01ULID...", "Sub_eventID": "01ULID...|null",
      "book": "...",
      "context": "Attack on St. Lô",
      "original_text": "The Shermans advanced...",
      "operating_country": "USA|null",
      "captured": false,
      "quantity": 10,
      "quantity_text": "10|several|null",
      "PlaceID": "01ULID...|null",
      "place_name": "...|null",
      "assertion_source": "narrative|media_narration|null",
      "Event_Name": "...|null",
      "Sub_event_Name": "...|null",
      "DateID": "01ULID...|null",
      "DateMentionID": "01ULID...|null",
      "paragraph_numbers": [145, 146],
      "variant_mentioned": "...|null",
      "using_unit": { "...": "object|null" },
      "using_person": { "...": "object|null" }
    }
  ],
  "enrichment_status": "enriched|not_found|null",
  "enrichment_checked_at": 1750000000,
  "openserp_searched": true
}
```

Notes:
- `EquipmentID` is the only required field. `category`/`subcategory`/`country_of_origin` are free-form nullable strings; `identity_source` is an enum (`exact|alias|learned_alias|fuzzy|grok_disambiguation`); `related_equipment[].relationship` enum is `predecessor|successor|variant`.
- Equipment uses `event_mentions` (not the earlier `mentions`); each mention carries both `DateID` and `DateMentionID`, plus per-mention operator (`operating_country`, `captured`), quantity (`quantity`, `quantity_text`), place (`PlaceID`, `place_name`), and `assertion_source` (`narrative|media_narration`). `using_unit`/`using_person` are free-form objects.
- `variants` items may be strings or objects; `specifications` (top-level and per-variant) is tolerant of extra keys. `canonical_name`, `timeline`, `technical_evolution`, `logistics`, and `environmental_performance` are source-tracked (narrative-sourced carry `original_text`; enrichment-sourced carry `source`/`source_url`).
- `enrichment_checked_at` is an epoch integer stamped on every enrichment check.

---


## Logistics — `output/logistics/*.json` (826 files)

```json
{
  "LogisticsID": "01ULID...",
  "logistics_type": "supply_shortage|supply_excess|delivery_delay|transport_disruption (free-form string)",
  "category": "ammunition|fuel|food|... (free-form nullable string)",
  "description": "...",
  "severity": "critical|high|medium|low|null",
  "status": "unresolved|in_progress|resolved|worsened (free-form nullable string)",
  "temporal": {
    "date_start": "1944-06-10",
    "date_end": "1944-06-15|null",
    "DateID_start": "01ULID...",
    "DateID_end": "01ULID...|null"
  },
  "delivery_method": "...|null",
  "quantity": { "...": "object, string, number, or null" },
  "resolution": { "...": "object, string, or null" },
  "extracted_date": "2026-03-15T...",
  "impacted_organizations": [{ "name": "...", "PeopleGroupID": "01ULID..." }],
  "impacted_people": [{ "name": "...", "PersonID": "01ULID..." }],
  "impacted_equipment": [{ "name": "...", "EquipmentID": "01ULID..." }],
  "event_mentions": [
    {
      "MentionID": "01ULID...",
      "EventID": "01ULID...",
      "Sub_eventID": "01ULID...|null",
      "book": "...",
      "chapter": "..."
    }
  ]
}
```

Notes:
- `LogisticsID` and `logistics_type` are required. `logistics_type` is a free-form string; `category` and `status` are free-form nullable strings; `severity` is the only enforced enum (`critical|high|medium|low`).
- `temporal`, `quantity`, and `resolution` are loosely typed (object/string/number/null) — the inner shape shown is illustrative, not enforced.
- `impacted_organizations`, `impacted_people`, and `impacted_equipment` are arrays whose items may be strings or objects.
- Logistics `event_mentions` use `MentionID` (not `EventMentionID`) and carry `EventID`, `Sub_eventID`, `book`, `chapter`.

---

## Casualties — `output/casualties/*.json`

Casualties track **personnel** losses only — killed, wounded, missing, and prisoners of war. Equipment and materiel losses belong in the Equipment entity.

```json
{
  "CasualtyID": "01ULID...",
  "type": "casualties|killed|wounded|pow|missing|non_battle (free-form string)",
  "cause": "combat|weather_exposure|disease|accident|other|null",
  "side": "allied|axis|neutral|civilian|unknown|null",
  "description": "...",
  "original_text": "exact quote from source",
  "PersonID": "01ULID...|null",
  "PlaceID": "01ULID...|null",
  "date": { "...": "object, string, or null" },
  "event_context": {
    "EventID": "01ULID...",
    "Sub-eventID": "01ULID...|null",
    "book": "Breakout and Pursuit|null",
    "chapter": "The Breakthrough Idea|null"
  },
  "source": {
    "book": "Breakout and Pursuit",
    "chapter": "The Breakthrough Idea",
    "paragraph_number": null
  },
  "count": {
    "total": {"value": 500, "qualifier": "approximately"},
    "killed": {"value": 100, "qualifier": "exact"},
    "wounded": {"value": 300, "qualifier": "exact"},
    "missing": {"value": 50, "qualifier": "exact"},
    "captured": {"value": 50, "qualifier": "exact"}
  },
  "impacted_organizations": [
    { "name": "29th Infantry Division", "PeopleGroupID": "01ULID...", "nationality": "USA", "role": "attacking_force" }
  ],
  "impacted_people": [
    { "name": "Captain Smith", "PersonID": "01ULID...", "casualty_type": "killed" }
  ],
  "impacted_places": [
    { "name": "Omaha Beach", "PlaceID": "01ULID..." }
  ]
}
```

Notes:
- `CasualtyID` and `type` are required. `type` is a free-form string (`kia`, `wia`, `mia`, `pow`, `non_battle`, `casualties`, etc.).
- `cause` classifies HOW the casualty occurred — enum `combat|weather_exposure|disease|accident|other` — enabling queries like weather-caused casualties (frostbite/trench foot/exposure).
- `side` is an enum reflecting who **suffered** the casualties: `allied|axis|neutral|civilian|unknown`.
- Top-level `PersonID`/`PlaceID` are direct individual anchors (for single-person casualties) in addition to the loose `impacted_*` lists, enabling a person+place+date join without parsing free text. `original_text` is retained for source traceability.
- `count`, `date`, and `source` are loosely typed (object/string/number/null); the inner shapes shown are illustrative, not enforced.
- Uses `event_context` (not `event_mentions`); `Sub-eventID` uses hyphen. `event_context` also carries `book` and `chapter`.
- POW entries (`type` `pow`) involve both `captured` and `captor` organizations; organization `role` values include `attacking_force`, `defending_force`, `captured`, `captor`, `suffered_casualties`.

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

> **Not an enforced schema:** There is no `src/schemas/images_output.py` module, so image files are **not** validated against a strict output schema (unlike every other entity above). The fields shown here are descriptive of current output, not contractually enforced.

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
    "periodical_name": null, "alt_periodical_name": null, "translator": null,
    "first_edition_date": null, "author_death_date": null
  },
  "availability": "online|offline|archive|unknown|null",
  "resource_urls": ["https://..."],
  "archive_reference_number": null,
  "archive_physical_address": null,
  "license": "public_domain|...",
  "license_notes": "...",
  "copyright_status": {
    "status": "...|null",
    "author_death_date": "...|null",
    "determination_basis": "...|null",
    "jurisdiction": "...|null"
  },
  "search_status": "resolved|not_found|pending|null",
  "search_source": "...|null",
  "download_status": "pending|downloaded|extracted|gated|skipped|error|null",
  "download_path": "...|null",
  "mentions": [
    {
      "MentionID": "01ULID...",
      "EventID": "01ULID...",
      "Sub-eventID": "01ULID...",
      "book": "Cross-Channel Attack",
      "chapter": "The Breakthrough Idea",
      "reference_type": "endnote|footnote",
      "reference_number": "1",
      "verbatim_reference": "Harrison, Cross-Channel Attack, p. 234",
      "volume": null,
      "pages": null
    }
  ]
}
```

Note: `BibliographyID` and `title` are required. Bibliography uses `Sub-eventID` (hyphen) in mentions. The output `availability` enum is `online|offline|archive|unknown`. The extraction-time schema (`src/json_schemas.py`) uses `MaterialID` per item, while the consolidated output uses `BibliographyID`.

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

## Map Features — `output/map_features/*.json`

A GeoJSON `FeatureCollection` carrying the structured **interior** of a historical map
(places, unit positions, fortifications, routes) extracted by Grok vision, with
entity-graph links (`PlaceID`/`GroupID`/`PersonID`/`DateID`) + provenance. Adapts GeoJSON +
the TacticalJSON/APP-6 profile. Coordinates come from the resolved `PlaceID` (geocode
cascade), **never** map pixels — so `geometry` is null until geocoded and
`coordinate_source` is never "map_pixels".

```json
{
  "type": "FeatureCollection",
  "MapID": "01ULID...|null",
  "map_number": "Map XII|null",
  "map_title": "Operation Cobra - Breakthrough|null",
  "legend": [
    {
      "symbol_description": "...|null",
      "meaning": "...|null",
      "date_text": "...|null",
      "DateID": "01ULID...|null",
      "confidence": 0.8
    }
  ],
  "elevation_scale": "...|null",
  "distance_scale": "...|null",
  "covered_places": ["01ULID..."],
  "date_range": { "earliest": "...|null", "latest": "...|null" },
  "features": [
    {
      "type": "Feature",
      "geometry": null,
      "properties": {
        "feature_kind": "place|unit_position|unit_boundary|route|fortification|river|road|railroad|elevation|front_line",
        "original_label": "...|null",
        "source": "map",
        "confidence": 0.9,
        "legend_key": "...|null",
        "tile_id": "...|null",
        "coordinate_source": "placeid_gazetteer|anchor_place|none|null",
        "PlaceID": "01ULID...|null",
        "GroupID": "01ULID...|null",
        "PersonID": "01ULID...|null",
        "DateID": "01ULID...|null",
        "sidc": "...|null",
        "uniqueDesignation": "...|null",
        "higherFormation": "...|null",
        "additionalInformation": "...|null",
        "dtg": "...|null",
        "affiliation": "friend|hostile|neutral|unknown|null",
        "echelon": "...|null",
        "branch": "...|null",
        "posture": "attack|defend|axis|null"
      }
    }
  ],
  "resolution_report": { "...": "object|null" }
}
```

Notes:
- Required top-level fields are `type` and `features`. `type` is the fixed enum `FeatureCollection`.
- `MapID` cross-references `output/maps/*.json` (`MapID`). `covered_places` is an array of `PlaceID` ULIDs (reverse-link coverage extent; optional).
- `legend[]` items carry `symbol_description`, `meaning`, `date_text`, `DateID`, `confidence`.
- Each feature is a GeoJSON `Feature` (`type` fixed enum `Feature`) with required `type`, `geometry`, `properties`.
  - `geometry` is `null` (until geocoded) or an object whose `type` is the enum `Point|LineString|Polygon` with a `coordinates` array (Point=`[lon,lat]`; Line/Polygon nested).
  - `properties` requires `feature_kind` (enum above) and `source` (enum, fixed value `map`). `coordinate_source` enum is `placeid_gazetteer|anchor_place|none`; `affiliation` enum is `friend|hostile|neutral|unknown`; `posture` enum is `attack|defend|axis`.
  - Entity-graph links (`PlaceID`/`GroupID`/`PersonID`/`DateID`) are null when unresolved (never guessed). The 2525/APP-6 modifiers (`sidc`, `uniqueDesignation`, `higherFormation`, `additionalInformation`, `dtg`, `echelon`, `branch`) are free-form nullable strings.
- `resolution_report` is a prototype diagnostics object (optional; not persisted in production records).

---

## Known Inconsistencies

| Issue | Entities Affected | Notes |
|---|---|---|
| `Sub-eventID` vs `Sub_eventID` | Images/Bibliography/Casualties use hyphen; Maps/Equipment/Logistics/Dates/Places/Groups use underscore | Legacy; both resolve correctly |
| `mentions` vs `event_mentions` | Bibliography uses `mentions`; most others use `event_mentions` | Legacy convention |
| `event_context` vs `event_mentions` | Casualties use `event_context` object; others use `event_mentions` array | Casualties are single-event |
| `DateMentionID` vs `MentionID` | Dates use `DateMentionID`; places/groups/equipment/logistics/bibliography use `MentionID` | Per-entity mention-ID naming |

---

## DynamoDB Keys (AWS Mode)

Pipeline state and coordination entries stored in the cache table (`dev-wwii-api-cache`).

### Job Queue — `batch_job#{batch_id}`

```json
{
  "cache_key": "batch_job#batch_abc123",
  "batch_id": "batch_abc123",
  "phase": "phase2",
  "book": "BreakoutAndPursuit",
  "batch_name": "BreakoutAndPursuit-3files-45requests",
  "submitted_at": "2026-05-20T14:30:00Z",
  "status": "pending|complete|failed|retrieved",
  "completed_at": "2026-05-20T16:45:00Z",
  "request_count": 45,
  "ttl": 1750000000
}
```

TTL is set to 30 days after submission for automatic cleanup.

### Name-Based Exclusions — `name_exclusion#{type}#{name1}#{name2}`

```json
{
  "cache_key": "name_exclusion#people#eisenhower#eisenhower dwight d",
  "type": "people",
  "name1": "eisenhower",
  "name2": "eisenhower dwight d",
  "created_at": "2026-05-20T10:00:00Z",
  "source": "dedup_ui|merge_script"
}
```

Survives file recreation — matches on normalized names rather than file IDs.

### Book Entity Manifest — `book_manifest#{book}#{entity_type}`

```json
{
  "cache_key": "book_manifest#BreakoutAndPursuit#people",
  "book": "BreakoutAndPursuit",
  "entity_type": "people",
  "keys": ["output/people/Eisenhower_01ABC.json", "output/people/Patton_01DEF.json"],
  "updated_at": "2026-05-20T15:00:00Z"
}
```

Used by Phase 3 to scope S3 downloads to only the entities relevant to the current book.

### Pipeline Locks — `lock#{family}`

```json
{
  "cache_key": "lock#phase2",
  "task_arn": "arn:aws:ecs:...",
  "started_at": "2026-05-20T14:00:00Z"
}
```

### Pending Content — `pending#content` / `pending#parsed`

```json
{
  "cache_key": "pending#content",
  "keys": ["content/Book/chapter5/chapter5-content.md"],
  "queued_at": "2026-05-20T14:30:00Z"
}
```

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

---

## Related documents
- [People Biographical Enrichment](features/people/biographical-enrichment.md) — populates the v2.5 person fields (`nationality_served`, `primary_group_id`, award provenance).
- [Award Sourcing](dataquality/AWARD_SOURCING.md) — defines the `MilitaryAward` provenance + `sourcing_attempts` semantics.
- [Entity Relationship Map](ENTITY_RELATIONSHIP_MAP.md) — cross-reference / ID conventions across entity types.
- [Data Quality Status](DATA_QUALITY_STATUS.md) — canonical entity counts + enrichment rates for this schema version.

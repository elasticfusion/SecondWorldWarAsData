# JSON Schema Documentation

**Schema Version:** 1.1  
**Last Updated:** 2026-03-19

This document describes all JSON schemas used in the WWII data extraction pipeline.

## Event Schema

**Version:** 1.0

### Fields

- `Chapter` (string, **required**)
- `Event` (object, **required**)
  - Object properties:
    - `EventID` (string, **required**)
      - Pattern: `^[0-9A-HJKMNP-TV-Z]{26}$`
    - `Sub-events` (array, **required**)
      - Array items:
        - `Sub-eventID` (string, **required**)
          - Pattern: `^[0-9A-HJKMNP-TV-Z]{26}$`
        - `Sub-event_summary` (string, **required**)
        - `Sub-event_fulltext` (object, **required**)
        - `Endnote_References` (array, optional)
        - `Footnote_References` (array, optional)
        - `dates` (array of ULID strings, optional) — DateIDs referenced by this sub-event
        - `places` (array of ULID strings, optional) — PlaceIDs referenced by this sub-event
        - `people` (array of ULID strings, optional) — PersonIDs referenced by this sub-event
        - `peoplegroups` (array of ULID strings, optional) — GroupIDs referenced by this sub-event

## Date Schema

**Version:** 1.1

### Central Repository File (`output/dates/{date}.json`)

- `DateID` (string, **required**)
  - Pattern: `^[0-9A-HJKMNP-TV-Z]{26}$`
- `date` (string, **required**) — ISO date or approximate (e.g. `1944-07-01`, `mid-1944-07`)
- `date_start` (string, optional) — present when created by full extractor
- `date_end` (['string', 'null'], optional)
- `time_start` (['string', 'null'], optional)
- `time_end` (['string', 'null'], optional)
- `time_precision` (['string', 'null'], optional)
- `date_precision` (['string', 'null'], optional)
- `time_source` (['string', 'null'], optional)
- `original_text` (string, optional)
- `event_mentions` (array, **required**) — cross-references back to events
  - Array items:
    - `MentionID` (string, **required**)
      - Pattern: `^[0-9A-HJKMNP-TV-Z]{26}$`
    - `Event_Name` (string, **required**)
    - `EventID` (string, **required**)
    - `Sub_event_Name` (string, **required**)
    - `Sub_eventID` (string, **required**)
    - `book` (string, **required**)
    - `author` (string, **required**)
    - `series` (string, **required**)

### Full Extractor Response (per sub-event API response)

- `Event_Name` (string, **required**)
- `EventID` (string, **required**)
  - Pattern: `^[0-9A-HJKMNP-TV-Z]{26}$`
- `Sub-event_Name` (string, **required**)
- `Sub-eventID` (string, **required**)
  - Pattern: `^[0-9A-HJKMNP-TV-Z]{26}$`
- `Date_Mentions` (array, **required**)
  - Array items:
    - `DateMentionID` (string, **required**)
      - Pattern: `^[0-9A-HJKMNP-TV-Z]{26}$`
    - `date_start` (string, **required**)
    - `date_end` (['string', 'null'], optional)
    - `time_start` (['string', 'null'], optional)
    - `time_end` (['string', 'null'], optional)
    - `time_precision` (['string', 'null'], optional)
    - `time_source` (['string', 'null'], optional)
    - `original_text` (string, **required**)

## Place Schema

**Version:** 1.1

### Central Repository File (`output/places/{name}.json`)

- `PlaceID` (string, **required**)
  - Pattern: `^[0-9A-HJKMNP-TV-Z]{26}$`
- `name` (string, **required**)
- `event_mentions` (array, **required**) — cross-references back to events
  - Array items:
    - `MentionID` (string, **required**)
      - Pattern: `^[0-9A-HJKMNP-TV-Z]{26}$`
    - `Event_Name` (string, **required**)
    - `EventID` (string, **required**)
    - `Sub_event_Name` (string, **required**)
    - `Sub_eventID` (string, **required**)
    - `book` (string, **required**)
    - `author` (string, **required**)
    - `series` (string, **required**)

### Full Extractor Response (per sub-event API response)

- `Event_Name` (string, **required**)
- `EventID` (string, **required**)
  - Pattern: `^[0-9A-HJKMNP-TV-Z]{26}$`
- `Sub-event_Name` (string, **required**)
- `Sub-eventID` (string, **required**)
  - Pattern: `^[0-9A-HJKMNP-TV-Z]{26}$`
- `Place_Mentions` (array, **required**)
  - Array items:
    - `PlaceMentionID` (string, **required**)
      - Pattern: `^[0-9A-HJKMNP-TV-Z]{26}$`
    - `current_name` (string, **required**)
    - `historical_name` (['string', 'null'], optional)
    - `source_language` (string, **required**)
    - `latitude` (['number', 'null'], optional)
    - `longitude` (['number', 'null'], optional)
    - `bounding_box_100km` (['object', 'null'], optional)
    - `geography_type` (string, **required**)
    - `date_context` (['string', 'null'], optional)
    - `original_text` (string, **required**)
    - `route` (['array', 'null'], optional)

## Supplemental Schema

**Version:** 1.0

### Fields

- `Event_Name` (string, **required**)
- `EventID` (string, **required**)
  - Pattern: `^[0-9A-HJKMNP-TV-Z]{26}$`
- `Sub-event_Name` (string, **required**)
- `Sub-eventID` (string, **required**)
  - Pattern: `^[0-9A-HJKMNP-TV-Z]{26}$`
- `Supplemental_Material` (array, **required**)
  - Array items:
    - `MaterialID` (string, **required**)
      - Pattern: `^[0-9A-HJKMNP-TV-Z]{26}$`
    - `EventID` (string, **required**)
      - Pattern: `^[0-9A-HJKMNP-TV-Z]{26}$`
    - `Sub-eventID` (string, **required**)
      - Pattern: `^[0-9A-HJKMNP-TV-Z]{26}$`
    - `reference_type` (string, **required**)
    - `reference_number` (['string', 'integer', 'null'], **required**)
    - `verbatim_reference` (string, **required**)
    - `material_category` (string, optional)
    - `citation` (object, **required**)
      - Object properties:
        - `author` (array, optional)
        - `title` (string, **required**)
        - `alt_title` (['string', 'null'], optional) — expanded/unabbreviated form of the title, or null if already full
        - `publisher` (['string', 'null'], optional)
        - `publication_date` (['string', 'null'], optional)
        - `first_edition_date` (['string', 'null'], optional)
        - `publication_location` (['string', 'null'], optional)
        - `publication_country` (['string', 'null'], optional)
        - `isbn` (['string', 'null'], optional)
        - `isbn_edition` (['string', 'null'], optional)
        - `pages` (['string', 'null'], optional)
        - `volume` (['string', 'null'], optional)
        - `edition` (['string', 'null'], optional)
        - `translator` (['string', 'null'], optional)
        - `periodical_name` (['string', 'null'], optional)
        - `document_type` (['string', 'null'], optional)
        - `author_death_date` (['string', 'null'], optional)
    - `content_class` (string, optional) — Grok classification: `document_reference`, `factual_content`, or `ambiguous`
    - `availability` (string, **required**)
    - `resource_urls` (array, optional)
    - `archive_reference_number` (['string', 'null'], optional)
    - `archive_physical_address` (['string', 'null'], optional)
    - `url_validation_status` (['string', 'null'], optional)
    - `url_validation_date` (['string', 'null'], optional)
    - `license` (['string', 'null'], optional)
    - `license_notes` (['string', 'null'], optional)

## People Schema

**Version:** 1.1

### Central Repository File (`output/people/{name}.json`)

- `PersonID` (string, **required**)
  - Pattern: `^[0-9A-HJKMNP-TV-Z]{26}$`
- `name` (string, **required**)
- `rank` (['string', 'null'], optional)
- `role` (['string', 'null'], optional)
- `nationality` (['string', 'null'], optional)
- `branch` (['string', 'null'], optional)
- `unit` (['string', 'null'], optional)
- `birth_date` (['string', 'null'], optional)
- `death_date` (['string', 'null'], optional)
- `biography` (['string', 'null'], optional)
- `awards` (array, optional)
  - Array items:
    - `award_name` (string, **required**)
    - `date_awarded` (['string', 'null'], optional)
    - `citation` (['string', 'null'], optional)
- `family` (['object', 'null'], optional)
- `event_mentions` (array, **required**) — cross-references back to events
  - Array items:
    - `MentionID` (string, **required**)
      - Pattern: `^[0-9A-HJKMNP-TV-Z]{26}$`
    - `Event_Name` (string, **required**)
    - `EventID` (string, **required**)
    - `Sub_event_Name` (string, **required**)
    - `Sub_eventID` (string, **required**)
    - `book` (string, **required**)
    - `author` (string, **required**)
    - `series` (string, **required**)
- `events` (array, optional) — legacy format from full extractor
  - Array items:
    - `EventID` (string, **required**)
      - Pattern: `^[0-9A-HJKMNP-TV-Z]{26}$`
    - `Sub-eventID` (string, **required**)
      - Pattern: `^[0-9A-HJKMNP-TV-Z]{26}$`
    - `Event_Name` (string, optional)
    - `Sub-event_Name` (string, optional)

## People Groups Schema

**Version:** 1.1

### Central Repository File (`output/people_groups/{name}.json`)

- `GroupID` (string, **required**)
  - Pattern: `^[0-9A-HJKMNP-TV-Z]{26}$`
- `name` (string, **required**)
- `group_name` (string, optional) — present when created by full extractor
- `group_type` (string, optional)
- `nationality` (['string', 'null'], optional)
- `branch` (['string', 'null'], optional)
- `parent_unit` (['string', 'null'], optional)
- `commander` (['string', 'null'], optional)
- `formation_date` (['string', 'null'], optional)
- `dissolution_date` (['string', 'null'], optional)
- `description` (['string', 'null'], optional)
- `event_mentions` (array, **required**) — cross-references back to events
  - Array items:
    - `MentionID` (string, **required**)
      - Pattern: `^[0-9A-HJKMNP-TV-Z]{26}$`
    - `Event_Name` (string, **required**)
    - `EventID` (string, **required**)
    - `Sub_event_Name` (string, **required**)
    - `Sub_eventID` (string, **required**)
    - `book` (string, **required**)
    - `author` (string, **required**)
    - `series` (string, **required**)
- `events` (array, optional) — legacy format from full extractor
  - Array items:
    - `EventID` (string, **required**)
      - Pattern: `^[0-9A-HJKMNP-TV-Z]{26}$`
    - `Sub-eventID` (string, **required**)
      - Pattern: `^[0-9A-HJKMNP-TV-Z]{26}$`
    - `Event_Name` (string, optional)
    - `Sub-event_Name` (string, optional)

## Equipment Schema

**Version:** 1.0

### Fields

- `EquipmentID` (string, **required**)
  - Pattern: `^[0-9A-HJKMNP-TV-Z]{26}$`
- `common_name` (string, **required**)
- `official_designation` (['string', 'null'], optional)
- `equipment_type` (string, **required**)
- `manufacturer` (['string', 'null'], optional)
- `country_of_origin` (['string', 'null'], optional)
- `introduction_year` (['integer', 'null'], optional)
- `production_years` (['string', 'null'], optional)
- `units_produced` (['integer', 'null'], optional)
- `specifications` (['object', 'null'], optional)
- `variants` (array, optional)
- `description` (['string', 'null'], optional)
- `events` (array, **required**)
  - Array items:
    - `EventID` (string, **required**)
      - Pattern: `^[0-9A-HJKMNP-TV-Z]{26}$`
    - `Sub-eventID` (string, **required**)
      - Pattern: `^[0-9A-HJKMNP-TV-Z]{26}$`
    - `Event_Name` (string, optional)
    - `Sub-event_Name` (string, optional)

## Map Schema

**Version:** 1.0

### Fields

- `MapID` (string, **required**)
  - Pattern: `^[0-9A-HJKMNP-TV-Z]{26}$`
- `title` (string, **required**)
- `description` (['string', 'null'], optional)
- `source` (string, **required**)
- `url` (['string', 'null'], optional)
- `image_path` (['string', 'null'], optional)
- `license` (['string', 'null'], optional)
- `attribution` (['string', 'null'], optional)
- `date_created` (['string', 'null'], optional)
- `scale` (['string', 'null'], optional)
- `EventID` (['string', 'null'], optional)
  - Pattern: `^[0-9A-HJKMNP-TV-Z]{26}$`
- `Sub-eventID` (['string', 'null'], optional)
  - Pattern: `^[0-9A-HJKMNP-TV-Z]{26}$`
- `PlaceMentionID` (['string', 'null'], optional)
  - Pattern: `^[0-9A-HJKMNP-TV-Z]{26}$`
- `DateMentionID` (['string', 'null'], optional)
  - Pattern: `^[0-9A-HJKMNP-TV-Z]{26}$`

## Casualties Schema

**Version:** 1.0

### Fields

- `CasualtyID` (string, **required**)
  - Pattern: `^[0-9A-HJKMNP-TV-Z]{26}$`
- `type` (string, **required**)
- `EventID` (string, **required**)
  - Pattern: `^[0-9A-HJKMNP-TV-Z]{26}$`
- `Sub-eventID` (string, **required**)
  - Pattern: `^[0-9A-HJKMNP-TV-Z]{26}$`
- `PersonID` (['string', 'null'], optional)
  - Pattern: `^[0-9A-HJKMNP-TV-Z]{26}$`
- `name` (['string', 'null'], optional)
- `rank` (['string', 'null'], optional)
- `unit` (['string', 'null'], optional)
- `nationality` (['string', 'null'], optional)
- `date` (['string', 'null'], optional)
- `location` (['string', 'null'], optional)
- `circumstances` (['string', 'null'], optional)

## Bibliography Schema

**Version:** 1.0

### Central Repository File (`output/bibliography/{title_slug}_{ULID}.json`)

Deduplicated document storage with cross-book mention tracking. One file per unique document/book.

- `BibliographyID` (string, **required**)
  - Pattern: `^[0-9A-HJKMNP-TV-Z]{26}$`
- `title` (string, **required**)
- `alt_title` (['string', 'null'], optional) — expanded/unabbreviated form of the title
- `citation` (object, **required**)
  - Object properties: same as supplemental citation (author, publisher, dates, ISBN, etc.)
- `availability` (string, **required**) — online, offline, archive, unknown
- `resource_urls` (array, optional)
- `archive_reference_number` (['string', 'null'], optional)
- `archive_physical_address` (['string', 'null'], optional)
- `license` (['string', 'null'], optional)
- `license_notes` (['string', 'null'], optional)
- `mentions` (array, **required**) — all references to this document across chapters/books
  - Array items:
    - `MentionID` (string, **required**)
      - Pattern: `^[0-9A-HJKMNP-TV-Z]{26}$`
    - `EventID` (string, **required**)
    - `Sub-eventID` (string, **required**)
    - `book` (string, **required**)
    - `chapter` (string, **required**)
    - `reference_type` (string, **required**)
    - `reference_number` (['string', 'integer', 'null'], optional)
    - `verbatim_reference` (string, **required**)
    - `pages` (['string', 'null'], optional)
    - `volume` (['string', 'null'], optional)

### Index File (`output/bibliography/index.json`)

Maps normalized titles to filenames:
```json
{
  "first u.s. army report of operations": "first_us_army_report_of_operations_01ABCDEF.json"
}
```

## Casualty Item Schema (Grok Response Validation)

**Version:** 1.0

Per-item validation applied to each casualty returned by Grok before building casualty objects.

- `type` (string, **required**) — enum: `wounded`, `killed`, `casualties`, `pow`
- `description` (string, **required**)
- `count` (object, optional)

## People Group Item Schema (Grok Response Validation)

**Version:** 1.0

Per-item validation applied to each group returned by Grok before saving.

- `group_name` (string, **required**)
- `group_type` (['string', 'null'], optional)
- `nationality` (['string', 'null'], optional)
- `event_mentions` (array, optional)

## Validation Coverage

All extractors validate Grok API responses before writing output:

| Extractor | Validation Method | ULID Fixing |
|---|---|---|
| events.py | JSON Schema (`EVENT_SCHEMA`) | ✅ `_fix_invalid_ulids` |
| supplemental.py | JSON Schema (`SUPPLEMENTAL_SCHEMA`) | ✅ `_fix_invalid_ulids` |
| dates.py | Pydantic (`extract_structured`) | ✅ `_fix_invalid_ulids` |
| places.py | Pydantic (`extract_structured`) | ✅ `_fix_invalid_ulids` |
| people.py | Pydantic (`extract_structured`) | ✅ `_fix_invalid_ulids` |
| weather_central.py | Pydantic fields + `_fix_invalid_ulids` (batch: `extract_json`) | ✅ `_fix_invalid_ulids` |
| logistics.py | Pydantic `model_validate` (batch: `extract_json`) | N/A (ULIDs local) |
| equipment.py | Pydantic (`model_validate`) | ✅ `_fix_invalid_ulids` |
| casualties.py | JSON Schema (`CASUALTY_ITEM_SCHEMA`) per item (batch: `chat_completion`) | ✅ `_fix_invalid_ulids` |
| people_groups.py | JSON Schema (`PEOPLE_GROUP_ITEM_SCHEMA`) per item | ✅ `_fix_invalid_ulids` |
| batch_parallel.py | Downstream extractors validate | ✅ `_fix_invalid_ulids` |

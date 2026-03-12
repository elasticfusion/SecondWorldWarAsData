# JSON Schema Documentation

**Schema Version:** 1.0

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

## Date Schema

**Version:** 1.0

### Fields

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

**Version:** 1.0

### Fields

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
    - `availability` (string, **required**)
    - `resource_urls` (array, optional)
    - `archive_reference_number` (['string', 'null'], optional)
    - `archive_physical_address` (['string', 'null'], optional)
    - `url_validation_status` (['string', 'null'], optional)
    - `url_validation_date` (['string', 'null'], optional)
    - `license` (['string', 'null'], optional)
    - `license_notes` (['string', 'null'], optional)

## People Schema

**Version:** 1.0

### Fields

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
- `events` (array, **required**)
  - Array items:
    - `EventID` (string, **required**)
      - Pattern: `^[0-9A-HJKMNP-TV-Z]{26}$`
    - `Sub-eventID` (string, **required**)
      - Pattern: `^[0-9A-HJKMNP-TV-Z]{26}$`
    - `Event_Name` (string, optional)
    - `Sub-event_Name` (string, optional)

## People Groups Schema

**Version:** 1.0

### Fields

- `GroupID` (string, **required**)
  - Pattern: `^[0-9A-HJKMNP-TV-Z]{26}$`
- `group_name` (string, **required**)
- `group_type` (string, **required**)
- `nationality` (['string', 'null'], optional)
- `branch` (['string', 'null'], optional)
- `parent_unit` (['string', 'null'], optional)
- `commander` (['string', 'null'], optional)
- `formation_date` (['string', 'null'], optional)
- `dissolution_date` (['string', 'null'], optional)
- `description` (['string', 'null'], optional)
- `events` (array, **required**)
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

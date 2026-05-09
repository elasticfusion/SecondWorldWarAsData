# Casualties Extraction Specification

## Overview

Extract personnel casualty information from historical events: killed, wounded, missing, and prisoners of war. Casualties are about **people** — individual soldiers, units, or civilian populations. Equipment and materiel losses belong in the Equipment entity.

## Requirements

- Extract casualty mentions from event text
- Every entry has a ULID (CasualtyID)
- Link to referenced people, organizations, places, dates
- Include description of the incident
- Note nationality and side (Allied/Axis/other)
- Distinguish combatant vs civilian casualties when stated
- Track both named individuals and aggregate unit counts

## JSON Schema

```json
{
  "CasualtyID": "01JBQR8X9K2M3N4P5Q6R7S8T9V",
  "type": "killed|wounded|casualties|pow|missing",
  "description": "Brief description of the casualty incident",
  "side": "allied|axis|civilian|unknown",
  "count": {
    "killed": {"value": 150, "qualifier": "exact"},
    "wounded": {"value": 450, "qualifier": "approximately"},
    "missing": {"value": 20, "qualifier": "exact"},
    "captured": {"value": 30, "qualifier": "exact"},
    "total": {"value": 650, "qualifier": "exact"}
  },
  "date": {
    "DateID": "01JBQR8X9K2M3N4P5Q6R7S8T9V",
    "date_string": "6 June 1944",
    "precision": "day"
  },
  "event_context": {
    "EventID": "01JBQR8X9K2M3N4P5Q6R7S8T9V",
    "Sub-eventID": "01JBQR8X9K2M3N4P5Q6R7S8T9V"
  },
  "impacted_organizations": [
    {
      "PeopleGroupID": "01JBQR8X9K2M3N4P5Q6R7S8T9V",
      "name": "1st Infantry Division",
      "nationality": "USA",
      "role": "attacking_force"
    }
  ],
  "impacted_people": [
    {
      "PersonID": "01JBQR8X9K2M3N4P5Q6R7S8T9V",
      "name": "Captain John Smith",
      "casualty_type": "killed"
    }
  ],
  "impacted_places": [
    {
      "PlaceID": "01JBQR8X9K2M3N4P5Q6R7S8T9V",
      "name": "Omaha Beach"
    }
  ],
  "source": {
    "book": "Cross-Channel Attack",
    "chapter": "chapter-6",
    "paragraph_number": 142
  }
}
```

## Field Definitions

### Required Fields

- **CasualtyID**: ULID identifier
- **type**: Enum — `killed`, `wounded`, `casualties` (generic), `pow`, `missing`
- **description**: Text description of the casualty incident
- **event_context**: Links to source event (EventID, Sub-eventID)
- **source**: Book, chapter, paragraph traceability

### Optional Fields

- **side**: Which side suffered the casualties — `allied`, `axis`, `civilian`, `unknown`
- **count**: Structured casualty counts with qualifiers
  - Each field is `{value: int, qualifier: "exact"|"approximately"|"greater_than"|"less_than"|"unknown"}`
  - Fields: `killed`, `wounded`, `missing`, `captured`, `total`
  - Use only when specific numbers or estimates are mentioned in text
- **date**: DateID reference with date string and precision
- **impacted_organizations**: Array of PeopleGroupID references with nationality (ISO 3166-1 alpha-3) and role
- **impacted_people**: Array of PersonID references with casualty_type per individual
- **impacted_places**: Array of PlaceID references (where casualties occurred)

### Organization Roles

- `attacking_force` — unit was attacking when casualties occurred
- `defending_force` — unit was defending
- `captured` — unit/personnel were taken prisoner (POW entries)
- `captor` — unit that captured the prisoners (POW entries)
- `suffered_casualties` — general, when attack/defense role unclear

### POW Entries

POW entries MUST include both organizations:
- One with role `captured` (the prisoners)
- One with role `captor` (who captured them)

## Storage

- **Location**: `output/casualties/`
- **Filename**: `{type}_{ulid}.json`
- **Example**: `killed_01JBQR8X9K2M3N4P5Q6R7S8T9V.json`

## What Does NOT Belong Here

- **Equipment/materiel losses** — tracked in Equipment entity (`output/equipment/`)
- **Weather conditions** — tracked in Weather entity and linked to events
- **Infrastructure damage** — not a personnel casualty

## Examples

### Named Individual Killed

```json
{
  "CasualtyID": "01JBQR8X9K2M3N4P5Q6R7S8T9V",
  "type": "killed",
  "description": "Company commander killed during assault on German positions",
  "side": "allied",
  "count": {
    "killed": {"value": 1, "qualifier": "exact"},
    "total": {"value": 1, "qualifier": "exact"}
  },
  "date": {
    "DateID": "01JBQR8X9K2M3N4P5Q6R7S8T9W",
    "date_string": "7 June 1944",
    "precision": "day"
  },
  "event_context": {
    "EventID": "01JBQR8X9K2M3N4P5Q6R7S8T9X",
    "Sub-eventID": "01JBQR8X9K2M3N4P5Q6R7S8T9Y"
  },
  "impacted_organizations": [
    {
      "PeopleGroupID": "01JBQR8X9K2M3N4P5Q6R7S8T9Z",
      "name": "Company A, 16th Infantry Regiment",
      "nationality": "USA",
      "role": "attacking_force"
    }
  ],
  "impacted_people": [
    {
      "PersonID": "01JBQR8X9K2M3N4P5Q6R7S8TA1",
      "name": "Captain John Smith",
      "casualty_type": "killed"
    }
  ],
  "impacted_places": [
    {
      "PlaceID": "01JBQR8X9K2M3N4P5Q6R7S8TA2",
      "name": "Colleville-sur-Mer"
    }
  ],
  "source": {
    "book": "Cross-Channel Attack",
    "chapter": "chapter-6",
    "paragraph_number": 142
  }
}
```

### Mass Casualties (Unit-Level)

```json
{
  "CasualtyID": "01JBQR8X9K2M3N4P5Q6R7S8TB1",
  "type": "casualties",
  "description": "Heavy casualties sustained during beach landing operations",
  "side": "allied",
  "count": {
    "killed": {"value": 150, "qualifier": "approximately"},
    "wounded": {"value": 450, "qualifier": "approximately"},
    "missing": {"value": 20, "qualifier": "exact"},
    "total": {"value": 620, "qualifier": "approximately"}
  },
  "date": {
    "DateID": "01JBQR8X9K2M3N4P5Q6R7S8TB2",
    "date_string": "6 June 1944",
    "precision": "day"
  },
  "event_context": {
    "EventID": "01JBQR8X9K2M3N4P5Q6R7S8TB3",
    "Sub-eventID": null
  },
  "impacted_organizations": [
    {
      "PeopleGroupID": "01JBQR8X9K2M3N4P5Q6R7S8TB4",
      "name": "1st Infantry Division",
      "nationality": "USA",
      "role": "attacking_force"
    }
  ],
  "impacted_places": [
    {
      "PlaceID": "01JBQR8X9K2M3N4P5Q6R7S8TB5",
      "name": "Omaha Beach"
    }
  ],
  "source": {
    "book": "Cross-Channel Attack",
    "chapter": "chapter-6",
    "paragraph_number": 89
  }
}
```

### Prisoners of War

```json
{
  "CasualtyID": "01JBQR8X9K2M3N4P5Q6R7S8TC1",
  "type": "pow",
  "description": "German prisoners captured during counterattack",
  "side": "axis",
  "count": {
    "captured": {"value": 230, "qualifier": "exact"},
    "total": {"value": 230, "qualifier": "exact"}
  },
  "date": {
    "DateID": "01JBQR8X9K2M3N4P5Q6R7S8TC2",
    "date_string": "8 June 1944",
    "precision": "day"
  },
  "event_context": {
    "EventID": "01JBQR8X9K2M3N4P5Q6R7S8TC3",
    "Sub-eventID": "01JBQR8X9K2M3N4P5Q6R7S8TC4"
  },
  "impacted_organizations": [
    {
      "PeopleGroupID": "01JBQR8X9K2M3N4P5Q6R7S8TC5",
      "name": "352nd Infantry Division",
      "nationality": "DEU",
      "role": "captured"
    },
    {
      "PeopleGroupID": "01JBQR8X9K2M3N4P5Q6R7S8TC7",
      "name": "1st Infantry Division",
      "nationality": "USA",
      "role": "captor"
    }
  ],
  "impacted_places": [
    {
      "PlaceID": "01JBQR8X9K2M3N4P5Q6R7S8TC6",
      "name": "Formigny"
    }
  ],
  "source": {
    "book": "Cross-Channel Attack",
    "chapter": "chapter-6",
    "paragraph_number": 201
  }
}
```

## Notes

- Use ISO 3166-1 alpha-3 codes for nationality (USA, GBR, DEU, FRA, CAN, etc.)
- All `impacted_*` arrays are optional — only include when explicitly mentioned
- `count` fields are all optional — use only when specific numbers are provided
- Generic `casualties` type used when specific breakdown (killed/wounded) not specified
- `side` reflects who suffered the casualties, not who inflicted them
- POW entries must include both `captured` and `captor` organizations

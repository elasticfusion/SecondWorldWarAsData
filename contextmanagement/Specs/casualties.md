# Casualties Extraction Specification

## Overview

Extract casualty information from historical events including wounded, killed, generic casualties, and prisoners of war.

## Requirements

- Search events for casualty mentions
- Every entry has a ULID (CasualtyID)
- Embed ULIDs for referenced people, organizations, places, equipment, weather
- Include description of the incident
- Tie to specific dates
- Note nationality of casualties
- Link impacted organizations, people, places when appropriate

## JSON Schema

```json
{
  "CasualtyID": "01JBQR8X9K2M3N4P5Q6R7S8T9V",
  "type": "wounded|killed|casualties|pow",
  "description": "Brief description of the casualty incident",
  "count": {
    "killed": 150,
    "wounded": 450,
    "missing": 20,
    "captured": 30,
    "total": 650
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
      "name": "John Doe",
      "casualty_type": "killed"
    }
  ],
  "impacted_places": [
    {
      "PlaceID": "01JBQR8X9K2M3N4P5Q6R7S8T9V",
      "name": "Omaha Beach"
    }
  ],
  "impacted_equipment": [
    {
      "EquipmentID": "01JBQR8X9K2M3N4P5Q6R7S8T9V",
      "common_name": "M4 Sherman",
      "count_lost": 12
    }
  ],
  "weather_conditions": {
    "WeatherID": "01JBQR8X9K2M3N4P5Q6R7S8T9V"
  },
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
- **type**: Enum - `wounded`, `killed`, `casualties`, `pow`
- **description**: Text description of the casualty incident
- **event_context**: Links to source event (EventID, Sub-eventID)
- **source**: Book, chapter, paragraph traceability

### Optional Fields

- **count**: Structured casualty counts (killed, wounded, missing, captured, total)
  - All numeric fields are optional
  - Use when specific numbers are mentioned
- **date**: DateID reference with date string and precision
- **impacted_organizations**: Array of PeopleGroupID references with nationality (ISO 3166-1 alpha-3)
- **impacted_people**: Array of PersonID references with casualty_type
- **impacted_places**: Array of PlaceID references
- **impacted_equipment**: Array of EquipmentID references with count_lost
- **weather_conditions**: WeatherID reference

## Storage

- **Location**: `output/casualties/`
- **Filename**: `{type}_{ulid}.json`
- **Example**: `killed_01JBQR8X9K2M3N4P5Q6R7S8T9V.json`

## Integration

Casualties are extracted during Phase 2 and linked to:
- Events (source context)
- Dates (temporal context)
- Places (geographic context)
- People (individual casualties)
- People Groups (unit casualties)
- Equipment (material losses)
- Weather (environmental conditions)

## Examples

### Example 1: Specific Casualties with Named Individuals

```json
{
  "CasualtyID": "01JBQR8X9K2M3N4P5Q6R7S8T9V",
  "type": "killed",
  "description": "Company commander killed during assault on German positions",
  "count": {
    "killed": 1,
    "total": 1
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

### Example 2: Mass Casualties (Generic)

```json
{
  "CasualtyID": "01JBQR8X9K2M3N4P5Q6R7S8TB1",
  "type": "casualties",
  "description": "Heavy casualties sustained during beach landing operations",
  "count": {
    "killed": 150,
    "wounded": 450,
    "missing": 20,
    "total": 620
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
  "weather_conditions": {
    "WeatherID": "01JBQR8X9K2M3N4P5Q6R7S8TB6"
  },
  "source": {
    "book": "Cross-Channel Attack",
    "chapter": "chapter-6",
    "paragraph_number": 89
  }
}
```

### Example 3: Prisoners of War

```json
{
  "CasualtyID": "01JBQR8X9K2M3N4P5Q6R7S8TC1",
  "type": "pow",
  "description": "German prisoners captured during counterattack",
  "count": {
    "captured": 230,
    "total": 230
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

### Example 4: Equipment Losses with Casualties

```json
{
  "CasualtyID": "01JBQR8X9K2M3N4P5Q6R7S8TD1",
  "type": "casualties",
  "description": "Tank crews lost during armored engagement",
  "count": {
    "killed": 15,
    "wounded": 8,
    "total": 23
  },
  "date": {
    "DateID": "01JBQR8X9K2M3N4P5Q6R7S8TD2",
    "date_string": "10 June 1944",
    "precision": "day"
  },
  "event_context": {
    "EventID": "01JBQR8X9K2M3N4P5Q6R7S8TD3",
    "Sub-eventID": "01JBQR8X9K2M3N4P5Q6R7S8TD4"
  },
  "impacted_organizations": [
    {
      "PeopleGroupID": "01JBQR8X9K2M3N4P5Q6R7S8TD5",
      "name": "2nd Armored Division",
      "nationality": "USA",
      "role": "attacking_force"
    }
  ],
  "impacted_equipment": [
    {
      "EquipmentID": "01JBQR8X9K2M3N4P5Q6R7S8TD6",
      "common_name": "M4 Sherman",
      "count_lost": 5
    }
  ],
  "impacted_places": [
    {
      "PlaceID": "01JBQR8X9K2M3N4P5Q6R7S8TD7",
      "name": "Caumont"
    }
  ],
  "source": {
    "book": "Cross-Channel Attack",
    "chapter": "chapter-7",
    "paragraph_number": 56
  }
}
```

## Notes

- Use ISO 3166-1 alpha-3 codes for nationality in impacted_organizations (consistent with existing schemas)
- All impacted_* arrays are optional - only include when explicitly mentioned
- count fields are all optional - use only when specific numbers are provided
- Generic "casualties" type used when specific breakdown (killed/wounded) not specified
- Equipment losses (count_lost) tracked when mentioned alongside personnel casualties
- **POW entries must include both organizations**: one with role "captured" and one with role "captor"

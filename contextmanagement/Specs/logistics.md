# Logistics Extraction Specification

**Version:** 1.0.0  
**Status:** Draft  
**Created:** 2026-03-05

---

## Overview

Extract logistics issues from WWII historical documents, including supply shortages, delivery delays, transport disruptions, and material excess. Links logistics events to people, organizations, places, equipment, and weather conditions.

---

## JSON Schema

### Complete Example

```json
{
  "LogisticsID": "01KJXYZ123ABC456DEF789GHI0",
  "logistics_type": "supply_shortage",
  "category": "ammunition",
  "description": "Critical shortage of 105mm artillery shells affecting VII Corps operations in Normandy. Artillery units limited to 10 rounds per gun per day, forcing commanders to ration fire support during the Saint-Lô offensive.",
  "severity": "critical",
  "quantity": {
    "required": 50000,
    "available": 12000,
    "unit": "rounds",
    "shortage": 38000
  },
  "temporal": {
    "date_start": "1944-07-15",
    "date_end": "1944-07-20",
    "date_type": "range",
    "DateID_start": "01KJ67F5XCTPXR2S4K02RQKXSB",
    "DateID_end": "01KJ67F5XCTPXR2S4K02RQKXSC",
    "DateMentionID": "01KJ67F5XY6K73Y1Z0E1RBSCSH"
  },
  "delivery_method": "sea_transport",
  "status": "resolved",
  "impacted_organizations": [
    {
      "PeopleGroupID": "01KJXABC123DEF456GHI789JKL",
      "group_name": "VII Corps",
      "impact_description": "Reduced artillery support capability by 75%, limited offensive operations"
    },
    {
      "PeopleGroupID": "01KJXDEF456GHI789JKL012MNO",
      "group_name": "1st Infantry Division",
      "impact_description": "Delayed attack on Saint-Lô by 48 hours"
    }
  ],
  "impacted_people": [
    {
      "PersonID": "01KJXGHI789JKL012MNO345PQR",
      "name": "J. Lawton Collins",
      "role": "VII Corps Commander",
      "impact_description": "Forced to ration artillery ammunition and delay offensive operations"
    },
    {
      "PersonID": "01KJXJKL012MNO345PQR678STU",
      "name": "Omar Bradley",
      "role": "First Army Commander",
      "impact_description": "Redirected ammunition supplies from V Corps to VII Corps"
    }
  ],
  "impacted_places": [
    {
      "PlaceID": "01KHYP2M4N6P8Q0R2S4T6V8W2B",
      "place_name": "Saint-Lô",
      "country": "FRA",
      "impact_description": "Delayed offensive operations by 48 hours due to ammunition shortage"
    },
    {
      "PlaceID": "01KHYP3N5M7Q1S3U5W7Y9A0C2E",
      "place_name": "Cherbourg",
      "country": "FRA",
      "impact_description": "Port capacity insufficient to handle ammunition resupply requirements"
    }
  ],
  "impacted_equipment": [
    {
      "EquipmentID": "01KJXMNO345PQR678STU901VWX",
      "common_name": "M101 105mm Howitzer",
      "impact_description": "Limited to 10 rounds per gun per day, reducing fire support effectiveness"
    },
    {
      "EquipmentID": "01KJXPQR678STU901VWX234YZA",
      "common_name": "M1 155mm Gun",
      "impact_description": "Completely out of ammunition for 3 days"
    }
  ],
  "weather_impact": {
    "WeatherID": "01KJXSTU901VWX234YZA567BCD",
    "impact_description": "Storm in English Channel delayed supply ships by 3 days, preventing ammunition resupply",
    "severity": "high"
  },
  "event_mentions": [
    {
      "EventMentionID": "01KJXVWX234YZA567BCD890EFG",
      "EventID": "01KJ3C8D3RP3MD210G7R6CHH3Z",
      "Sub_eventID": "01KJ3C8D3RFQMD33B8CZ8GW8XK",
      "paragraph_numbers": [15, 16, 17],
      "context": "Artillery ammunition shortage forced VII Corps to limit fire support during Saint-Lô offensive. Collins reported critical shortage to Bradley."
    },
    {
      "EventMentionID": "01KJXYZA567BCD890EFG123HIJ",
      "EventID": "01KJ3C8D3RP3MD210G7R6CHH3Z",
      "Sub_eventID": "01KJ3C8D3RFQMD33B8CZ8GW8XL",
      "paragraph_numbers": [22],
      "context": "Emergency airlift of ammunition authorized by Bradley to resolve VII Corps shortage."
    }
  ],
  "resolution": {
    "resolved": true,
    "resolution_date": "1944-07-20",
    "resolution_description": "Emergency airlift of 30,000 rounds via C-47 transport aircraft from England. Additional 20,000 rounds diverted from V Corps reserves.",
    "resolution_method": "air_delivery"
  },
  "extracted_date": "2026-03-05T14:30:00Z"
}
```

---

## Field Definitions

### Core Identification

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `LogisticsID` | string (ULID) | Yes | Unique identifier for logistics issue |
| `logistics_type` | enum | Yes | Type of logistics issue |
| `category` | enum | Yes | Category of supplies/resources |
| `description` | string | Yes | Detailed description of the issue |
| `severity` | enum | Yes | Severity level of the issue |

### Logistics Types

- `supply_shortage` - Insufficient supplies available
- `supply_excess` - Surplus supplies causing storage/distribution issues
- `delivery_delay` - Transport delays preventing timely delivery
- `transport_disruption` - Infrastructure damage or unavailability

### Categories

- `ammunition` - Artillery shells, small arms ammunition
- `fuel` - Gasoline, diesel, aviation fuel
- `food` - Rations, fresh food, water
- `medical` - Medical supplies, pharmaceuticals
- `equipment` - Vehicles, weapons, tools
- `personnel` - Troop replacements, specialists
- `general` - Mixed or unspecified supplies

### Severity Levels

- `critical` - Mission-critical impact, immediate action required
- `high` - Significant operational impact
- `medium` - Moderate impact, workarounds possible
- `low` - Minor inconvenience, minimal operational impact

---

## Quantity Object (Optional)

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `required` | number | No | Amount needed |
| `available` | number | No | Amount on hand |
| `unit` | string | No | Unit of measurement (rounds, gallons, tons, etc.) |
| `shortage` | number | No | Calculated shortage (required - available) |
| `excess` | number | No | Calculated excess (available - required) |

**Example:**
```json
{
  "quantity": {
    "required": 50000,
    "available": 12000,
    "unit": "rounds",
    "shortage": 38000
  }
}
```

---

## Temporal Object (Required)

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `date_start` | string (ISO date) | Yes | Start date or only date if specific |
| `date_end` | string (ISO date) | No | End date (null if specific date) |
| `date_type` | enum | Yes | `specific` or `range` |
| `DateID_start` | string (ULID) | No | Reference to start date entity |
| `DateID_end` | string (ULID) | No | Reference to end date entity |
| `DateMentionID` | string (ULID) | No | Reference to date mention in text |

**Specific Date Example:**
```json
{
  "temporal": {
    "date_start": "1944-07-15",
    "date_end": null,
    "date_type": "specific",
    "DateID_start": "01KJ67F5XCTPXR2S4K02RQKXSB",
    "DateID_end": null,
    "DateMentionID": "01KJ67F5XY6K73Y1Z0E1RBSCSH"
  }
}
```

**Date Range Example:**
```json
{
  "temporal": {
    "date_start": "1944-07-15",
    "date_end": "1944-07-20",
    "date_type": "range",
    "DateID_start": "01KJ67F5XCTPXR2S4K02RQKXSB",
    "DateID_end": "01KJ67F5XCTPXR2S4K02RQKXSC",
    "DateMentionID": "01KJ67F5XY6K73Y1Z0E1RBSCSH"
  }
}
```

---

## Delivery and Status

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `delivery_method` | enum | No | Primary delivery method |
| `status` | enum | Yes | Current status of the issue |

### Delivery Methods

- `sea_transport` - Ships, landing craft
- `air_delivery` - Aircraft, airdrops
- `ground_transport` - Trucks, convoys
- `rail` - Railway transport
- `pipeline` - Fuel pipelines
- `mixed` - Multiple methods

### Status Values

- `unresolved` - Issue ongoing, no solution yet
- `in_progress` - Resolution efforts underway
- `resolved` - Issue resolved
- `worsened` - Situation deteriorated

---

## Impact Arrays (All Optional)

### Impacted Organizations

Array of people groups affected by the logistics issue.

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `PeopleGroupID` | string (ULID) | Yes | Reference to organization |
| `group_name` | string | Yes | Name of organization |
| `impact_description` | string | Yes | How organization was impacted |

**Example:**
```json
{
  "impacted_organizations": [
    {
      "PeopleGroupID": "01KJXABC123DEF456GHI789JKL",
      "group_name": "VII Corps",
      "impact_description": "Reduced artillery support capability by 75%"
    }
  ]
}
```

### Impacted People

Array of individuals affected by the logistics issue.

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `PersonID` | string (ULID) | Yes | Reference to person |
| `name` | string | Yes | Person's name |
| `role` | string | No | Person's role/position |
| `impact_description` | string | Yes | How person was impacted |

**Example:**
```json
{
  "impacted_people": [
    {
      "PersonID": "01KJXGHI789JKL012MNO345PQR",
      "name": "J. Lawton Collins",
      "role": "VII Corps Commander",
      "impact_description": "Forced to ration artillery ammunition"
    }
  ]
}
```

### Impacted Places

Array of locations affected by the logistics issue.

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `PlaceID` | string (ULID) | Yes | Reference to place |
| `place_name` | string | Yes | Name of place |
| `country` | string (ISO 3166-1 alpha-3) | No | Country code |
| `impact_description` | string | Yes | How place was impacted |

**Example:**
```json
{
  "impacted_places": [
    {
      "PlaceID": "01KHYP2M4N6P8Q0R2S4T6V8W2B",
      "place_name": "Saint-Lô",
      "country": "FRA",
      "impact_description": "Delayed offensive operations by 48 hours"
    }
  ]
}
```

### Impacted Equipment

Array of equipment affected by the logistics issue.

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `EquipmentID` | string (ULID) | Yes | Reference to equipment |
| `common_name` | string | Yes | Common name of equipment |
| `impact_description` | string | Yes | How equipment was impacted |

**Example:**
```json
{
  "impacted_equipment": [
    {
      "EquipmentID": "01KJXMNO345PQR678STU901VWX",
      "common_name": "M101 105mm Howitzer",
      "impact_description": "Limited to 10 rounds per gun per day"
    }
  ]
}
```

### Weather Impact (Optional, Single Object)

Weather conditions affecting logistics.

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `WeatherID` | string (ULID) | Yes | Reference to weather event |
| `impact_description` | string | Yes | How weather impacted logistics |
| `severity` | enum | Yes | Severity of weather impact |

**Example:**
```json
{
  "weather_impact": {
    "WeatherID": "01KJXSTU901VWX234YZA567BCD",
    "impact_description": "Storm delayed supply ships by 3 days",
    "severity": "high"
  }
}
```

---

## Event Mentions (Required)

Array linking logistics issue to specific events in source material.

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `EventMentionID` | string (ULID) | Yes | Unique mention identifier |
| `EventID` | string (ULID) | Yes | Reference to event |
| `Sub_eventID` | string (ULID) | No | Reference to sub-event |
| `paragraph_numbers` | array[int] | Yes | Paragraph numbers where mentioned |
| `context` | string | Yes | Context of the mention |

**Example:**
```json
{
  "event_mentions": [
    {
      "EventMentionID": "01KJXVWX234YZA567BCD890EFG",
      "EventID": "01KJ3C8D3RP3MD210G7R6CHH3Z",
      "Sub_eventID": "01KJ3C8D3RFQMD33B8CZ8GW8XK",
      "paragraph_numbers": [15, 16, 17],
      "context": "Artillery ammunition shortage forced VII Corps to limit fire support"
    }
  ]
}
```

---

## Resolution Object (Optional)

Information about how the logistics issue was resolved.

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `resolved` | boolean | Yes | Whether issue was resolved |
| `resolution_date` | string (ISO date) | No | When resolved |
| `resolution_description` | string | No | How it was resolved |
| `resolution_method` | enum | No | Method used to resolve |

### Resolution Methods

- `air_delivery` - Emergency airlift
- `sea_transport` - Additional ships
- `ground_transport` - Truck convoys
- `reallocation` - Diverted from other units
- `local_procurement` - Acquired locally
- `rationing` - Reduced consumption
- `substitution` - Used alternative supplies

**Example:**
```json
{
  "resolution": {
    "resolved": true,
    "resolution_date": "1944-07-20",
    "resolution_description": "Emergency airlift of 30,000 rounds via C-47 transport",
    "resolution_method": "air_delivery"
  }
}
```

---

## File Storage

### Directory Structure

```
output/
└── logistics/
    ├── index.json
    ├── ammunition_shortage_1944-07-15_01KJXYZ1.json
    ├── fuel_shortage_1944-08-10_01KJXYZ2.json
    └── ...
```

### Filename Convention

`{category}_{logistics_type}_{date_start}_{LogisticsID[:8]}.json`

**Examples:**
- `ammunition_shortage_1944-07-15_01KJXYZ1.json`
- `fuel_delivery_delay_1944-08-10_01KJXYZ2.json`
- `food_supply_excess_1944-09-05_01KJXYZ3.json`

### Index File

```json
{
  "ammunition_shortage_1944-07-15": "ammunition_shortage_1944-07-15_01KJXYZ1.json",
  "fuel_shortage_1944-08-10": "fuel_shortage_1944-08-10_01KJXYZ2.json"
}
```

---

## Extraction Requirements

### MUST Requirements

1. ✅ Search events for logistics issues
2. ✅ Assign ULID to every logistics entry
3. ✅ Embed ULIDs of referenced entities (people, organizations, places, equipment, weather)
4. ✅ Include detailed description
5. ✅ Tie to date (specific or range)
6. ✅ Note impacted organizations when appropriate
7. ✅ Note impacted people when appropriate
8. ✅ Note impacted places when appropriate
9. ✅ Note impacted equipment when appropriate
10. ✅ Note weather impact when appropriate

### Extraction Process

1. Scan event text for logistics-related keywords
2. Identify logistics type and category
3. Extract temporal information (date or range)
4. Identify impacted entities (people, organizations, places, equipment)
5. Check for weather-related impacts
6. Determine severity and status
7. Extract resolution information if available
8. Link to event mentions with paragraph numbers

### Keywords for Detection

**Supply Shortage:**
- shortage, lack, insufficient, depleted, exhausted, critical, scarce

**Supply Excess:**
- surplus, excess, overflow, stockpile, abundance

**Delivery Delay:**
- delayed, late, postponed, held up, behind schedule

**Transport Disruption:**
- blocked, damaged, destroyed, impassable, unavailable

---

## Validation Rules

1. **LogisticsID** must be valid ULID
2. **logistics_type** must be one of defined enum values
3. **category** must be one of defined enum values
4. **severity** must be one of defined enum values
5. **temporal.date_start** required
6. **temporal.date_type** must be "specific" or "range"
7. If **date_type** is "range", **date_end** must be provided
8. **event_mentions** array must have at least one entry
9. All ULID references must be valid ULIDs
10. **status** must be one of defined enum values

---

## Related Specifications

- [Dates](dates.md) - Date extraction and management
- [Places](places.md) - Place extraction and coordinates
- [People](../features/people/PEOPLE_MANAGEMENT.md) - People extraction
- [People Groups](../features/people_groups/PEOPLE_GROUPS.md) - Organization extraction
- [Equipment](../features/equipment/EQUIPMENT_IMPLEMENTATION_SUMMARY.md) - Equipment extraction
- [Weather](weather.md) - Weather extraction

---

## Version History

| Version | Date | Changes |
|---------|------|---------|
| 1.0.0 | 2026-03-05 | Initial specification |

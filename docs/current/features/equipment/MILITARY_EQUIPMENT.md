# Military Equipment Extraction - Proposal

**Version:** 1.0.0  
**Date:** 2026-03-03  
**Status:** Proposed

---

## Overview

Extract and track military equipment mentions from WWII historical texts, linking equipment to units, people, events, and performance observations.

---

## Schema Design

### Core Structure

```json
{
  "EquipmentID": "01H8XYZ...",
  "common_name": "Sherman",
  "technical_identifier": "M4",
  "category": "armor",
  "variants": [...],
  "mentions": [...]
}
```

### Key Features

1. **Single Record Per Equipment** - Variants stored within, not as separate records
2. **Rich Linking** - Connect to events, units, people, dates
3. **Performance Tracking** - Successes, failures, modifications, maintenance
4. **Supporting Units** - Track combined arms operations
5. **Source Tracking** - External data with provenance

---

## Schema Highlights

### 1. Equipment Identity

```json
{
  "EquipmentID": "01H8XYZDAB123CD456EF789GH",
  "common_name": "Sherman",
  "technical_identifier": "M4",
  "description": "American medium tank, primary Allied armored vehicle in Western Europe",
  "alternate_names": ["M4 Medium Tank", "Medium Tank M4"],
  "category": "armor",
  "subcategory": "medium_tank"
}
```

**Note:** `description` is a general description of the equipment. Event-specific descriptions go in the mention's `description` field.

### 2. Variants (Not Separate Records)

```json
{
  "variants": [
    {
      "variant_name": "M4A1",
      "differences": "Cast hull instead of welded",
      "alternate_names": ["Sherman I"]
    },
    {
      "variant_name": "M4A3E8",
      "differences": "76mm gun, HVSS suspension",
      "alternate_names": ["Easy Eight"]
    }
  ]
}
```

### 3. Mentions with Context

```json
{
  "mentions": [
    {
      "MentionID": "01KJ3DQ64DHFHYHA5WGWFHMCXV",
      "EventID": "01KJ3DQ64D7ESXAET2YZGYK8BT",
      "Sub_eventID": "01KJ3DQ64DPNVHEWY2H8G9GFFF",
      "DateID": "01KJ674B1A1R33MCCZ2BQPMTPF",
      "DateMentionID": "01KJ674B2D2XP5SGTQ2XSMF840",
      "using_unit": {
        "PeopleGroupID": "01H8XYZ...",
        "name": "2nd Armored Division"
      },
      "performance_notes": {
        "successes": ["Effective against infantry"],
        "failures": ["Outgunned by Panthers"],
        "field_modifications": ["Sandbags added for armor"]
      }
    }
  ]
}
```

**Key fields:**
- `MentionID` - Unique ID for this mention
- `EventID` - Links to Event.EventID in chapter event file
- `Sub_eventID` - Links to Sub-eventID in Event.Sub-events[] array
- `DateID` - Links to date file in output/dates/
- `DateMentionID` - Links to specific mention in date file

### 4. Supporting Units

```json
{
  "supporting_units": [
    {
      "support_type": "aircraft",
      "PeopleGroupID": "01H8XYZ...",
      "unit_name": "IX Tactical Air Command",
      "EquipmentID": "01H8XYZ...",
      "equipment_name": "P-47 Thunderbolt"
    }
  ]
}
```

### 5. External Data with Provenance

```json
{
  "external_data": {
    "grokipedia_url": "https://grokipedia.com/M4_Sherman",
    "wikipedia_url": "https://en.wikipedia.org/wiki/M4_Sherman",
    "additional_sources": [
      {
        "source_type": "museum",
        "source_name": "Tank Museum",
        "url": "https://tankmuseum.org/...",
        "data_points": [
          {
            "field": "production_numbers",
            "value": "49,234 units",
            "verified": true
          }
        ]
      }
    ]
  }
}
```

---

## Enhancements Proposed

### 1. **Comparative Analysis**

Add field for equipment comparisons:

```json
{
  "comparisons": [
    {
      "compared_to": {
        "EquipmentID": "01H8XYZ...",
        "name": "Panther"
      },
      "context": "Normandy 1944",
      "advantages": ["More reliable", "Better crew ergonomics"],
      "disadvantages": ["Thinner armor", "Weaker gun"],
      "source": {
        "book": "Breakout and Pursuit",
        "paragraph": 145
      }
    }
  ]
}
```

### 2. **Production & Deployment Timeline**

Track when equipment was introduced and phased out:

```json
{
  "timeline": {
    "first_production": "1942-02",
    "first_combat_use": "1942-11",
    "peak_deployment": "1944-06",
    "last_combat_use": "1945-05",
    "total_produced": 49234,
    "combat_losses": 8348
  }
}
```

### 3. **Crew Experiences**

Link to specific crew member accounts:

```json
{
  "crew_accounts": [
    {
      "PersonID": "01H8XYZ...",
      "person_name": "Belton Cooper",
      "role": "Maintenance Officer",
      "observations": "Frequent transmission failures in bocage terrain",
      "source": {
        "book": "Death Traps",
        "chapter": 3
      }
    }
  ]
}
```

### 4. **Tactical Doctrine**

How equipment was intended to be used:

```json
{
  "doctrine": {
    "intended_role": "Infantry support and exploitation",
    "typical_formation": "Company of 17 tanks",
    "combined_arms": ["Infantry", "Artillery", "Tank Destroyers"],
    "tactical_notes": "Not designed for tank-vs-tank combat"
  }
}
```

### 5. **Geographic Performance**

Performance by theater/terrain:

```json
{
  "geographic_performance": [
    {
      "theater": "European Theater",
      "terrain": "bocage",
      "performance_rating": "poor",
      "notes": "Vulnerable in hedgerow fighting",
      "adaptations": ["Hedgerow cutters", "Increased infantry coordination"]
    },
    {
      "theater": "North Africa",
      "terrain": "desert",
      "performance_rating": "good",
      "notes": "Reliable in desert conditions"
    }
  ]
}
```

### 6. **Logistics & Supply**

Supply chain information:

```json
{
  "logistics": {
    "fuel_consumption": "60 gallons per 100 miles",
    "ammunition_capacity": "90 rounds 75mm",
    "maintenance_hours_per_100_miles": 8,
    "common_spare_parts": ["tracks", "final drives", "transmission"],
    "supply_challenges": ["Track wear in bocage", "Engine overheating"]
  }
}
```

### 7. **Technical Evolution**

Track improvements over time:

```json
{
  "technical_evolution": [
    {
      "date": "1943-02",
      "change": "Wet ammunition storage",
      "reason": "Reduce fire risk",
      "effectiveness": "Reduced crew casualties by 15%"
    },
    {
      "date": "1944-01",
      "change": "76mm gun upgrade (M4A1(76)W)",
      "reason": "Counter German heavy armor",
      "effectiveness": "Improved penetration but still inferior to German guns"
    }
  ]
}
```

### 8. **Cross-References**

Link related equipment:

```json
{
  "related_equipment": [
    {
      "relationship": "predecessor",
      "EquipmentID": "01H8XYZ...",
      "name": "M3 Lee"
    },
    {
      "relationship": "successor",
      "EquipmentID": "01H8XYZ...",
      "name": "M26 Pershing"
    },
    {
      "relationship": "variant",
      "EquipmentID": "01H8XYZ...",
      "name": "M4A3E2 Jumbo"
    }
  ]
}
```

---

## Categories

### Primary Categories

- **armor** - Tanks, tank destroyers, armored cars
- **aircraft** - Fighters, bombers, reconnaissance
- **naval** - Ships, submarines, landing craft
- **artillery** - Field guns, howitzers, mortars
- **infantry_weapons** - Rifles, machine guns, grenades
- **communications** - Radios, field telephones
- **vehicles** - Trucks, jeeps, half-tracks
- **uniforms** - Combat uniforms, specialized gear
- **other** - Miscellaneous equipment

### Subcategories (Examples)

**Armor:**
- light_tank, medium_tank, heavy_tank, tank_destroyer, armored_car

**Aircraft:**
- fighter, bomber, reconnaissance, transport, glider

**Naval:**
- battleship, cruiser, destroyer, submarine, landing_craft

**Artillery:**
- field_gun, howitzer, mortar, anti_tank, anti_aircraft

---

## File Structure

```
output/equipment/
├── index.json                          # Name → filename lookup
├── M4_Sherman_01ABC123.json           # Individual equipment files
├── Tiger_I_01DEF456.json
├── P-51_Mustang_01GHI789.json
└── equipment_summary.json             # Statistics and overview
```

---

## Extraction Workflow

### Phase 1: Identify Equipment
```python
# Extract equipment mentions from parsed chapters
python3 -m src.extraction.equipment
```

### Phase 2: Link to Entities
- Link to events/sub-events
- Link to units (people_groups)
- Link to people
- Link to dates

### Phase 3: Enrich with External Data
```python
# Add external data from Grokipedia, Wikipedia, museums
python3 scripts/enrich_equipment.py
```

---

## Example Output

See full examples:
- `contextmanagement/Specs/military_equipment_example.json` - M4 Sherman
- `contextmanagement/Specs/military_equipment_example2.json` - Tiger I with multiple events

### Minimal Example

```json
{
  "EquipmentID": "01KJ3DQ64D7ESXAET2YZGYK8BT",
  "common_name": "Sherman",
  "technical_identifier": "M4",
  "description": "American medium tank",
  "category": "armor",
  "mentions": [
    {
      "MentionID": "01KJ3DQ64DHFHYHA5WGWFHMCXV",
      "EventID": "01KJ3DQ64D7ESXAET2YZGYK8BT",
      "Sub_eventID": "01KJ3DQ64DPNVHEWY2H8G9GFFF",
      "DateID": "01KJ674B1A1R33MCCZ2BQPMTPF",
      "DateMentionID": "01KJ674B2D2XP5SGTQ2XSMF840"
    }
  ]
}
```
{
  "EquipmentID": "01H8XYZI1AB123CD456EF789GH",
  "common_name": "Sherman",
  "technical_identifier": "M4",
  "description": "American medium tank, primary Allied armored vehicle in Western Europe. Reliable and easy to maintain but outgunned by German heavy tanks.",
  "alternate_names": ["M4 Medium Tank", "Medium Tank M4"],
  "category": "armor",
  "subcategory": "medium_tank",
  "variants": [
    {
      "variant_name": "M4A1",
      "differences": "Cast hull instead of welded hull",
      "alternate_names": ["Sherman I (British)"]
    },
    {
      "variant_name": "M4A3E8",
      "differences": "76mm gun, HVSS suspension, wet ammunition storage",
      "alternate_names": ["Easy Eight", "Sherman 76"]
    }
  ],
  "specifications": {
    "weight": "30.3 tons",
    "armament": "75mm M3 gun, .50 cal M2 Browning, 2x .30 cal M1919",
    "armor": "51mm frontal, 38mm side",
    "speed": "25 mph",
    "range": "120 miles",
    "crew": 5
  },
  "mentions": [
    {
      "MentionID": "01H8XYZ...",
      "book": "Breakout and Pursuit",
      "author": "Martin Blumenson",
      "series": "United States Army in World War II",
      "chapter": "Chapter 5",
      "paragraph_numbers": [145, 146],
      "variant_mentioned": "M4A1",
      "context": "2nd Armored Division attack on St. Lô",
      "description": "The Shermans performed admirably in the initial assault across open ground, but encountered difficulties in the bocage terrain. Several tanks were knocked out by well-concealed German anti-tank guns. Crews reported that the 75mm gun was inadequate against Panthers encountered during the advance.",
      "original_text": "The Shermans of the 2nd Armored Division advanced...",
      "EventID": "01H8XYZ...",
      "Event_Name": "Operation Cobra",
      "Sub_eventID": "01H8XYZ...",
      "Sub_event_Name": "St. Lô Breakthrough",
      "date": "1944-07-25",
      "DateID": "01H8XYZ...",
      "DateMentionID": "01H8XYZ...",
      "using_unit": {
        "PeopleGroupID": "01H8XYZ...",
        "name": "2nd Armored Division"
      },
      "supporting_units": [
        {
          "support_type": "aircraft",
          "PeopleGroupID": "01H8XYZ...",
          "unit_name": "IX Tactical Air Command",
          "EquipmentID": "01H8XYZ...",
          "equipment_name": "P-47 Thunderbolt"
        }
      ],
      "performance_notes": {
        "successes": [
          "Effective against German infantry positions",
          "Good mobility in open terrain"
        ],
        "failures": [
          "Outgunned by Panther tanks",
          "Vulnerable to 88mm anti-tank guns"
        ],
        "field_modifications": [
          "Sandbags added to frontal armor",
          "Hedgerow cutters welded to hull"
        ],
        "maintenance_issues": [
          "Track wear in bocage terrain",
          "Transmission failures under heavy use"
        ]
      }
    }
  ],
  "external_data": {
    "grokipedia_url": "https://grokipedia.com/M4_Sherman",
    "wikipedia_url": "https://en.wikipedia.org/wiki/M4_Sherman",
    "additional_sources": [
      {
        "source_type": "museum",
        "source_name": "The Tank Museum",
        "url": "https://tankmuseum.org/tank-nuts/tank-collection/m4-sherman/",
        "data_points": [
          {
            "field": "production_numbers",
            "value": "49,234 units",
            "verified": true,
            "notes": "All variants combined"
          }
        ]
      }
    ]
  },
```

---

## Cross-Referencing Capabilities

1. **Comprehensive Tracking** - All equipment mentions in one place
2. **Rich Context** - Links to events, units, people, dates
3. **Performance Analysis** - Track successes, failures, modifications
4. **Combined Arms** - See how different equipment types worked together
5. **Source Provenance** - Track where external data comes from
6. **Variant Management** - Keep variants together, not scattered
7. **Historical Accuracy** - Preserve original text and context

---

## Implementation Priority

### Phase 1 (Core)
1. Basic equipment extraction
2. Linking to events, units, people
3. Performance notes (successes, failures)

### Phase 2 (Enhanced)
4. Supporting units tracking
5. Field modifications
6. Maintenance issues

### Phase 3 (External)
7. External data integration
8. Grokipedia/Wikipedia links
9. Museum/archive data

### Phase 4 (Advanced)
10. Comparative analysis
11. Timeline tracking
12. Geographic performance
13. Logistics data

---

## See Also

- **Schema:** `contextmanagement/Specs/military_equipment_schema.json`
- **Original Spec:** `contextmanagement/Specs/military_equipment.yaml`
- **People Groups:** `docs/current/features/people/groups.md`
- **Events:** `docs/current/pipeline/PIPELINE.md`

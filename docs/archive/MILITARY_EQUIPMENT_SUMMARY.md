# Military Equipment - Quick Summary

**Status:** Proposed  
**Schema:** `contextmanagement/Specs/military_equipment_schema.json`  
**Docs:** `docs/current/features/MILITARY_EQUIPMENT.md`

---

## Core Concept

Track military equipment mentions with:
- Single record per equipment (variants within)
- Links to events, sub-events, dates via IDs
- Performance tracking (successes, failures, modifications)
- Supporting units (combined arms)
- External data with source tracking

---

## Schema Structure

```json
{
  "EquipmentID": "01H8XYZ...",
  "common_name": "Sherman",
  "technical_identifier": "M4",
  "description": "American medium tank",
  "category": "armor",
  "mentions": [
    {
      "MentionID": "01...",
      "EventID": "01...",
      "Sub_eventID": "01...",
      "DateID": "01...",
      "DateMentionID": "01...",
      "using_unit": {...},
      "supporting_units": [...],
      "performance_notes": {...}
    }
  ]
}
```

**Key Linking:**
- `EventID` → Links to Event.EventID in chapter event files
- `Sub_eventID` → Links to Sub-eventID in Event.Sub-events[] array
- `DateID` → Links to date file in output/dates/
- `DateMentionID` → Links to specific mention in date file

---

## Key Features

✅ **Variants** - Stored within parent record, not separate  
✅ **Performance** - Track what worked and what didn't  
✅ **Combined Arms** - Link supporting units and their equipment  
✅ **Source Tracking** - Provenance for all external data  
✅ **Rich Linking** - Connect to events, units, people, dates  

---

## Enhancements Proposed

1. **Comparative Analysis** - Equipment vs equipment comparisons
2. **Production Timeline** - When introduced, peak deployment, phased out
3. **Crew Experiences** - Link to crew member accounts
4. **Tactical Doctrine** - Intended use and formations
5. **Geographic Performance** - Performance by theater/terrain
6. **Logistics** - Fuel, ammo, maintenance requirements
7. **Technical Evolution** - Track improvements over time
8. **Cross-References** - Link predecessors, successors, variants

---

## Categories

- armor, aircraft, naval, artillery
- infantry_weapons, communications, vehicles
- uniforms, other

---

## File Structure

```
output/equipment/
├── index.json
├── M4_Sherman_01ABC123.json
├── Tiger_I_01DEF456.json
└── P-51_Mustang_01GHI789.json
```

---

## Implementation Phases

**Phase 1:** Core extraction + linking  
**Phase 2:** Performance tracking + supporting units  
**Phase 3:** External data integration  
**Phase 4:** Advanced features (comparisons, timelines, logistics)

---

## Next Steps

1. Review schema and proposal
2. Implement extraction module
3. Test with sample chapters
4. Iterate based on results

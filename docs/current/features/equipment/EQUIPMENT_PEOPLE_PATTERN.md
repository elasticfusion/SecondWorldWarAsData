# Equipment Schema - People.json Pattern Analysis

**Date:** 2026-03-03  
**Purpose:** Align equipment schema with proven people.json pattern

---

## People.json Pattern Analysis

### Core Structure

```json
{
  "PersonID": "01H8XYZI1AB123CD456EF789GH",
  "name": "Adolf Hitler",
  "source_language": "English",
  "biographical_profile": {...},
  "event_mentions": [...]
}
```

### Event Mention Structure

**Key insight:** Each mention tracks ALL context for cross-referencing:

```json
{
  "MentionID": "01H8XYZJ2MN456PQ789RS012TU",
  "Event_Name": "The Invasion of Poland",
  "EventID": "01H8XYZABC123DEF456GHJ789",
  "Sub-event_Name": "German forces cross the Polish border",
  "Sub-eventID": "01H8XYZ1MN456PQR789STU012",
  "date": "1939-09-01",
  "DateMentionID": "01H8XYZ3AB123CD456EF789GH",
  "position_at_event": "Führer and Chancellor",
  "life_event": "Ordered the invasion of Poland",
  "original_text": "Hitler gave the order to invade Poland at dawn"
}
```

### Critical Fields for Cross-Referencing

1. **MentionID** - Unique ID for this specific mention
2. **EventID + Event_Name** - Link to event
3. **Sub-eventID + Sub_event_Name** - Link to sub-event
4. **date** - Human-readable date
5. **DateMentionID** - Link to specific date mention
6. **original_text** - Source text for verification

---

## Equipment Schema Alignment

### Updated Mention Structure

```json
{
  "MentionID": "01H8XYZEAB123CD456EF789GH",
  "book": "Breakout and Pursuit",
  "author": "Martin Blumenson",
  "series": "United States Army in World War II",
  "chapter": "Chapter 5",
  "paragraph_numbers": [145, 146],
  "variant_mentioned": "M4A1",
  "context": "2nd Armored Division attack on St. Lô",
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
  "using_person": {
    "PersonID": "01H8XYZ...",
    "name": "George S. Patton"
  },
  "supporting_units": [...],
  "performance_notes": {...},
  "media": {...}
}
```

---

## Key Improvements Made

### 1. Added Book Metadata

**Before:** Only `book` field  
**After:** `book`, `author`, `series`

**Why:** Matches people.json pattern for source tracking

### 2. Reordered Fields for Consistency

**Order:**
1. MentionID
2. Book metadata (book, author, series, chapter)
3. Content location (paragraph_numbers)
4. Equipment-specific (variant_mentioned, context)
5. Original text
6. Event linking (EventID, Event_Name, Sub_eventID, Sub_event_Name)
7. Date linking (date, DateID, DateMentionID)
8. Entity linking (using_unit, using_person, supporting_units)
9. Performance data
10. Media (future)

### 3. Added DateMentionID

**Before:** Only `DateID` and `date`  
**After:** `DateID`, `DateMentionID`, `date`

**Why:** Links to specific date mention, not just date record

### 4. Consistent ULID Descriptions

**Pattern:** "26-character ULID of [entity]"

Examples:
- `EventID`: "26-character ULID of linked event"
- `Sub_eventID`: "26-character ULID of linked sub-event"
- `DateID`: "26-character ULID of linked date"
- `DateMentionID`: "26-character ULID of specific date mention"

---

## Media Fields (Future Expansion)

### Structure

```json
{
  "media": {
    "photos": [
      {
        "MediaID": "01H8XYZ...",
        "url": "https://...",
        "caption": "M4 Sherman in Normandy",
        "source": "National Archives",
        "date_taken": "1944-06-15"
      }
    ],
    "videos": [
      {
        "MediaID": "01H8XYZ...",
        "url": "https://...",
        "title": "Sherman Tank in Action",
        "description": "Combat footage from Operation Cobra",
        "source": "US Army Signal Corps",
        "duration": 180
      }
    ],
    "audio": [
      {
        "MediaID": "01H8XYZ...",
        "url": "https://...",
        "title": "Sherman Engine Sound",
        "description": "Recording of M4A3 engine",
        "source": "Tank Museum",
        "duration": 45
      }
    ],
    "documents": [
      {
        "MediaID": "01H8XYZ...",
        "url": "https://...",
        "title": "M4 Sherman Technical Manual",
        "document_type": "technical_manual",
        "source": "US Army"
      }
    ]
  }
}
```

### Media Types

1. **Photos** - Historical photographs
2. **Videos** - Combat footage, demonstrations
3. **Audio** - Engine sounds, combat recordings, oral histories
4. **Documents** - Technical manuals, field reports, after-action reports

### MediaID Pattern

Each media item gets its own ULID for tracking:
- Enables linking media across entities
- Tracks media provenance
- Supports media deduplication
- Allows media-specific metadata

---

## Comparison: People vs Equipment

### Similarities

| Feature | People | Equipment |
|---------|--------|-----------|
| **Entity ID** | PersonID | EquipmentID |
| **Mention ID** | MentionID | MentionID |
| **Event Linking** | EventID, Event_Name | EventID, Event_Name |
| **Sub-event Linking** | Sub-eventID, Sub_event_Name | Sub_eventID, Sub_event_Name |
| **Date Linking** | date, DateMentionID | date, DateID, DateMentionID |
| **Original Text** | original_text | original_text |
| **Source Tracking** | book, author, series | book, author, series, chapter |

### Differences

| Feature | People | Equipment |
|---------|--------|-----------|
| **Profile** | biographical_profile | specifications, variants |
| **Role** | position_at_event | variant_mentioned |
| **Context** | life_event | context, performance_notes |
| **Relationships** | - | using_unit, using_person, supporting_units |
| **Media** | - | photos, videos, audio, documents |

---

## Cross-Referencing Capabilities

### Equipment → People

```json
{
  "using_person": {
    "PersonID": "01H8XYZ5AB123CD456EF789GH",
    "name": "George S. Patton"
  }
}
```

### Equipment → People Groups

```json
{
  "using_unit": {
    "PeopleGroupID": "01H8XYZ7AB123CD456EF789GH",
    "name": "2nd Armored Division"
  }
}
```

### Equipment → Events

```json
{
  "EventID": "01H8XYZI1AB123CD456EF789GH",
  "Event_Name": "Operation Cobra",
  "Sub_eventID": "01H8XYZ3AB123CD456EF789GH",
  "Sub_event_Name": "St. Lô Breakthrough"
}
```

### Equipment → Dates

```json
{
  "date": "1944-07-25",
  "DateID": "01H8XYZAAB123CD456EF789GH",
  "DateMentionID": "01H8XYZ3AB123CD456EF789GH"
}
```

### Equipment → Equipment (Supporting Units)

```json
{
  "supporting_units": [
    {
      "support_type": "aircraft",
      "PeopleGroupID": "01H8XYZ8AB123CD456EF789GH",
      "unit_name": "IX Tactical Air Command",
      "EquipmentID": "01H8XYZFAB123CD456EF789GH",
      "equipment_name": "P-47 Thunderbolt"
    }
  ]
}
```

---

## Query Examples

### Find all equipment used by a person

```bash
# Search for PersonID in equipment mentions
grep -r "PersonID.*01H8XYZ5" output/equipment/
```

### Find all equipment used in an event

```bash
# Search for EventID in equipment mentions
grep -r "EventID.*01H8XYZI" output/equipment/
```

### Find all equipment with photos

```bash
# Search for media.photos in equipment files
jq 'select(.mentions[].media.photos | length > 0)' output/equipment/*.json
```

### Find equipment performance issues

```bash
# Search for failures in performance notes
jq '.mentions[].performance_notes.failures[]' output/equipment/*.json
```

---

## Implementation Checklist

### Phase 1: Core Extraction
- [x] EquipmentID generation
- [x] MentionID generation
- [x] Event linking (EventID, Event_Name, Sub_eventID, Sub_event_Name)
- [x] Date linking (date, DateID, DateMentionID)
- [x] Book metadata (book, author, series, chapter)
- [x] Original text capture
- [x] Variant tracking

### Phase 2: Entity Linking
- [ ] Link to people (using_person)
- [ ] Link to people groups (using_unit)
- [ ] Link to supporting units
- [ ] Performance notes extraction

### Phase 3: Media Integration
- [ ] Photo linking
- [ ] Video linking
- [ ] Audio linking
- [ ] Document linking
- [ ] MediaID generation
- [ ] Media deduplication

---

## Benefits of This Pattern

1. **Proven** - Same pattern as people.json (working in production)
2. **Consistent** - Matches existing entity schemas
3. **Traceable** - Every mention links back to source
4. **Queryable** - Rich cross-referencing via ULIDs
5. **Extensible** - Media fields ready for future expansion
6. **Verifiable** - original_text preserved for validation

---

## Next Steps

1. ✅ Update equipment schema to match people.json pattern
2. ✅ Add media fields for future expansion
3. ⏳ Implement extraction module
4. ⏳ Test with sample chapters
5. ⏳ Add media integration (Phase 3)

---

## See Also

- **People Schema:** `contextmanagement/Specs/people.json`
- **Equipment Schema:** `contextmanagement/Specs/military_equipment_schema.json`
- **ULID Implementation:** `docs/current/core/ULID_IMPLEMENTATION.md`
- **Equipment Proposal:** `docs/current/features/MILITARY_EQUIPMENT.md`

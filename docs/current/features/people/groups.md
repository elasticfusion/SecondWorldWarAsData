# People Groups → People Linking

**Last Updated:** 2026-03-22

## Implementation: Option 2 (Groups → People)

### Group File Schema

```json
{
  "GroupID": "01ULID...",
  "name": "XIX Army Corps",
  "group_name": "XIX Army Corps",
  "group_type": "military_unit",
  "military_hierarchy": "corps",
  "source_language": "English",
  "country_of_origin": "DEU",
  "alliance_membership": ["Axis Powers"],
  "common_name": "XIX Corps",
  "description": "...",
  "parent_organization": "Army Group B",
  "enrichment_data": {
    "full_name": "XIX Army Corps",
    "unit_type": "corps",
    "branch": "Wehrmacht",
    "nationality": "German",
    "formed_date": "1938-10-01",
    "disbanded_date": "1945-05-08",
    "parent_unit": "Army Group B",
    "description": "...",
    "commanding_officers": [
      { "name": "Heinz Guderian", "from_date": "1939-09-01", "to_date": "1940-05-10" }
    ],
    "notable_operations": ["Fall Gelb", "Operation Barbarossa"]
  },
  "members": [
    {
      "PersonID": "01ULID...",
      "name": "Heinz Guderian",
      "role": "Commander",
      "confidence": 0.9,
      "source": "enrichment",
      "from_date": "1939-09-01",
      "to_date": "1940-05-10"
    }
  ],
  "event_mentions": [
    {
      "MentionID": "01ULID...",
      "Event_Name": "...",
      "EventID": "01ULID...",
      "Sub_event_Name": "...",
      "Sub_eventID": "01ULID...",
      "book": "...",
      "author": "...",
      "series": "United States Army in World War II",
      "date": "1944-06-06",
      "DateMentionID": "01ULID..."
    }
  ]
}
```

**`group_type` enum:** `military_unit`, `country`, `alliance`, `political_party`, `government_organization`, `anti_government_organization`, `religious_organization`

**Cross-references:**
- `DateMentionID` in mentions → top-level `DateID` in `output/dates/*.json`
- `members[].PersonID` → top-level `PersonID` in `output/people/*.json`
- `enrichment_data` populated during Phase 3 enrichment

### Extraction Logic

The AI extracts:
1. **Group identification** (name, type, hierarchy)
2. **Member identification** from event context:
   - "XIX Corps under Guderian" → Guderian is Commander
   - "Eisenhower commanded SHAEF" → Eisenhower is Commander
   - "Churchill led the British government" → Churchill is Leader
3. **Role assignment** based on context
4. **Date ranges** when mentioned

### Merge Behavior

When updating existing groups:
- **Deduplicates members** by PersonID
- **Preserves existing members**
- **Adds new members** from subsequent extractions
- **Accumulates roles** across multiple books

### Linking Structure

```
People Group (Wehrmacht)
├── members: [
│   ├── {PersonID: "01ABC...", name: "Adolf Hitler", role: "Supreme Commander"}
│   ├── {PersonID: "01DEF...", name: "Wilhelm Keitel", role: "Chief of OKW"}
│   └── ...
│   ]
└── event_mentions: [...]

Person (Adolf Hitler)
├── event_mentions: [
│   ├── {EventID: "01...", position_at_event: "Supreme Commander"}
│   └── ...
│   ]
└── (can cross-reference to groups via PersonID)
```

### Query Capabilities

**Find all members of a group:**
```bash
jq '.members[] | {name, role}' output/people_groups/Wehrmacht_01H8XYZY.json
```

**Find which groups a person belongs to:**
```bash
grep -r "01ABC..." output/people_groups/*.json | jq '.group_name, .members[] | select(.PersonID == "01ABC...")'
```

### Benefits

✅ **Single extraction pass** - no separate cross-referencing needed
✅ **Context-aware** - AI understands "commanded", "led", "member of"
✅ **Incremental** - accumulates members across multiple books
✅ **Bidirectional** - can query from either direction

### Future Enhancement

Could add reverse index:
```json
// output/people_groups/person_group_index.json
{
  "01ABC...": ["Wehrmacht_01H8XYZY.json", "Nazi_Party_01H8XYZW.json"],
  "01DEF...": ["OKW_01H8XYZ1.json"]
}
```

## Status: ✅ Implemented

- Modified `src/extraction/people_groups.py`
- Added `members` field to extraction prompt
- Added member deduplication to merge logic
- Passed all QA checks (pylint 10/10, mypy, bandit)

# Place Schema v3.0.0 - Central Repository Summary

**Date:** 2026-02-23  
**Change Type:** Complete redesign (no migration)  
**Pattern:** Matches people/people_groups architecture

---

## What Changed

### Architecture: Per-Chapter → Central Repository

**Before (v2.0.0):**
```
output/BreakoutAndPursuit/chapter1a-places.json  # 50 places
output/BreakoutAndPursuit/chapter1b-places.json  # 45 places
output/Cross-Channel-Attack/chapter0a-places.json  # 60 places
```
❌ Duplicates across files  
❌ No cross-book tracking  
❌ No alias management  

**After (v3.0.0):**
```
output/places/Warsaw_01H8XYZ8AB.json
output/places/London_01LONDON00.json
output/places/Normandy_01NORMANDY.json
output/places/index.json
place_aliases.yaml  # Root level
```
✅ One file per place  
✅ Event mentions tracked  
✅ Aliases managed in YAML  
✅ Hierarchical relationships  

---

## Key Features

### 1. Event Mentions (Like People)
```json
{
  "PlaceID": "01NORMANDY000000000000000",
  "current_name": "Normandy",
  "event_mentions": [
    {
      "MentionID": "01...",
      "Event_Name": "D-Day Landings",
      "EventID": "01...",
      "Sub_event_Name": "Utah Beach assault",
      "Sub_eventID": "01...",
      "role_in_event": "landing site",
      "original_text": "Normandy beaches"
    }
  ]
}
```

### 2. Alias Management (YAML)
```yaml
aliases:
  - canonical: "United Kingdom"
    aliases: ["UK", "Great Britain", "Britain"]
    PlaceID: "01UKGBR000000000000000000"
    
  - canonical: "Gdańsk"
    aliases: ["Danzig"]
    PlaceID: "01GDANSK0000000000000000"
    historical_context: "Called Danzig under German control (1939-1945)"
```

### 3. Hierarchical Relationships
```yaml
hierarchies:
  - path: "Europe > United Kingdom > England > London"
    PlaceID: "01LONDON00000000000000000"
    parent_id: "01ENGLAND0000000000000000"
    
  - path: "Europe > France > Normandy > Caen"
    PlaceID: "01CAEN0000000000000000000"
    parent_id: "01NORMANDY000000000000000"
```

### 4. Historical Names
```json
{
  "current_name": "Saint Petersburg",
  "historical_names": [
    {
      "name": "Leningrad",
      "language": "Russian",
      "date_range": "1924-1991"
    }
  ]
}
```

### 5. Related Places
```json
{
  "related_places": [
    {
      "PlaceID": "01FRANCE00000000000000000",
      "relationship": "part_of"
    },
    {
      "PlaceID": "01PARIS000000000000000000",
      "relationship": "near",
      "distance_km": 230
    }
  ]
}
```

---

## Files Created

1. ✅ **`contextmanagement/Specs/place_v3_central.json`**
   - Central repository schema
   - Event mentions structure
   - Hierarchical relationships
   - Related places

2. ✅ **`place_aliases.yaml`** (root level)
   - Alias definitions (UK → United Kingdom)
   - Hierarchies (UK > England > London)
   - Historical context
   - Merge rules

3. ✅ **`docs/current/PLACE_CENTRAL_REPOSITORY.md`**
   - Complete design documentation
   - Implementation guide
   - Example queries

---

## Benefits

| Feature | v2.0.0 | v3.0.0 |
|---------|--------|--------|
| **Cross-book tracking** | ❌ | ✅ See all mentions across books |
| **Deduplication** | ❌ Manual | ✅ Automated with aliases |
| **Hierarchies** | ❌ | ✅ UK > England > London |
| **Historical names** | Single field | ✅ Array with date ranges |
| **Event mentions** | ❌ | ✅ Like people tracking |
| **Related places** | ❌ | ✅ Contains, near, part_of |
| **Alias management** | ❌ | ✅ YAML-based |

---

## Implementation Plan

### Phase 1: Code Updates (Day 1-2)
- [ ] Rewrite `src/extraction/places.py` for central repository
- [ ] Add place index management
- [ ] Add event mention tracking

### Phase 2: Deduplication Tools (Day 2-3)
- [ ] Create `scripts/find_duplicate_places.py`
- [ ] Create `scripts/merge_duplicate_places.py`
- [ ] Create `scripts/consolidate_places.py`
- [ ] Create `scripts/suggest_place_aliases.py`

### Phase 3: Extraction (Day 3)
- [ ] Create `output/places/` directory
- [ ] Run `python3 phase2_extract.py`
- [ ] Places extracted to central repository

### Phase 4: Cleanup (Day 3)
- [ ] Run deduplication scripts
- [ ] Populate `place_aliases.yaml`
- [ ] Validate all place files

---

## Example Usage

### Find all places in France
```python
places_in_france = [
    p for p in places 
    if p.hierarchy.country == "France"
]
```

### Find places mentioned in multiple books
```python
cross_book_places = [
    p for p in places 
    if len(set(m.book for m in p.event_mentions)) > 1
]
```

### Get full hierarchy path
```python
def get_full_path(place):
    path = [place.current_name]
    while place.hierarchy.parent_place_id:
        parent = get_place(place.hierarchy.parent_place_id)
        path.insert(0, parent.current_name)
        place = parent
    return " > ".join(path)

# "Europe > France > Normandy > Caen"
```

### Resolve alias
```python
# User searches for "UK"
canonical = resolve_alias("UK")  # Returns "United Kingdom"
place_file = index[canonical.lower()]  # Get place file
```

---

## Schema Structure

```json
{
  "PlaceID": "01...",
  "current_name": "Warsaw",
  "historical_names": [...],
  "aliases": ["Warszawa"],
  "source_language": "English",
  "geography_type": "city",
  "coordinates": {
    "latitude": 52.2297,
    "longitude": 21.0122,
    "precision": "exact",
    "confidence": 1.0
  },
  "bounding_box_100km": {...},
  "map_urls": {...},
  "hierarchy": {
    "continent": "Europe",
    "country": "Poland",
    "region": "Masovian Voivodeship",
    "parent_place_id": "01POLAND00..."
  },
  "event_mentions": [
    {
      "MentionID": "01...",
      "Event_Name": "...",
      "EventID": "01...",
      "Sub_event_Name": "...",
      "Sub_eventID": "01...",
      "role_in_event": "target city",
      "original_text": "Warsaw"
    }
  ],
  "related_places": [...]
}
```

---

## Migration Strategy

**No migration needed** - Rerun extraction from scratch:

1. Update extraction code
2. Delete old `output/*/chapter*-places.json` files
3. Run `python3 phase2_extract.py`
4. New central repository created automatically

---

## Questions?

- **Schema:** See `contextmanagement/Specs/place_v3_central.json`
- **Aliases:** See `place_aliases.yaml`
- **Documentation:** See `docs/current/PLACE_CENTRAL_REPOSITORY.md`

---

**Status:** ✅ Ready for implementation  
**Estimated Effort:** 2-3 days  
**Breaking Changes:** Complete rewrite (no backward compatibility)

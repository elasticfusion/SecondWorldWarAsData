# Place Schema v3.0.0 - Central Repository Design

**Date:** 2026-02-23  
**Status:** Proposed  
**Pattern:** Matches people/people_groups architecture

---

## Overview

Redesigned place extraction to use a **central repository pattern** with:
- One JSON file per place (like people)
- Event mentions tracked in place files
- YAML-based alias management
- Hierarchical relationships (UK > England > London)

---

## Architecture Changes

### From: Per-Chapter Extraction (v2.0.0)
```
output/
├── BreakoutAndPursuit/
│   ├── chapter1a-places.json  # All places in chapter
│   └── chapter1b-places.json
└── Cross-Channel-Attack/
    └── chapter0a-places.json
```

### To: Central Repository (v3.0.0)
```
output/
├── places/
│   ├── Warsaw_01H8XYZ8AB123CD456EF789GH.json
│   ├── London_01LONDON00000000000000000.json
│   ├── Normandy_01NORMANDY000000000000000.json
│   ├── index.json
│   └── duplicate_report.json
└── place_aliases.yaml  # Root level
```

---

## File Structure

### Individual Place File
**`output/places/Warsaw_01H8XYZ8AB123CD456EF789GH.json`**

```json
{
  "PlaceID": "01H8XYZ8AB123CD456EF789GH",
  "current_name": "Warsaw",
  "historical_names": [
    {
      "name": "Warszawa",
      "language": "Polish"
    },
    {
      "name": "Warschau",
      "language": "German",
      "date_range": "1939-1945"
    }
  ],
  "aliases": ["Warszawa"],
  "source_language": "English",
  "geography_type": "city",
  "coordinates": {
    "latitude": 52.2297,
    "longitude": 21.0122,
    "precision": "exact",
    "confidence": 1.0
  },
  "bounding_box_100km": {
    "north": 53.1297,
    "south": 51.3297,
    "east": 22.4122,
    "west": 19.6122
  },
  "map_urls": {
    "google_maps": "https://www.google.com/maps?q=52.2297,21.0122",
    "openstreetmap": "https://www.openstreetmap.org/?mlat=52.2297&mlon=21.0122&zoom=12"
  },
  "hierarchy": {
    "continent": "Europe",
    "country": "Poland",
    "region": "Masovian Voivodeship",
    "parent_place_id": "01POLAND00000000000000000"
  },
  "event_mentions": [
    {
      "MentionID": "01H8XYZM1N456PQ789RS012TU",
      "Event_Name": "The Invasion of Poland",
      "EventID": "01H8XYZABC123DEF456GHJ789",
      "Sub_event_Name": "German forces cross the Polish border",
      "Sub_eventID": "01H8XYZ1MN456PQR789STU012",
      "book": "Cross-Channel Attack",
      "author": "Gordon A. Harrison",
      "series": "United States Army in World War II",
      "date_context": "1939-09-01",
      "DateMentionID": "01H8XYZD1E234FG567HI890JK",
      "role_in_event": "target city",
      "original_text": "Warsaw"
    }
  ],
  "related_places": [
    {
      "PlaceID": "01POLAND00000000000000000",
      "relationship": "part_of"
    }
  ]
}
```

### Index File
**`output/places/index.json`**

```json
{
  "warsaw": "Warsaw_01H8XYZ8AB123CD456EF789GH.json",
  "london": "London_01LONDON00000000000000000.json",
  "normandy": "Normandy_01NORMANDY000000000000000.json",
  "uk": "United_Kingdom_01UKGBR000000000000000000.json",
  "united kingdom": "United_Kingdom_01UKGBR000000000000000000.json",
  "great britain": "United_Kingdom_01UKGBR000000000000000000.json"
}
```

### Alias Management
**`place_aliases.yaml`** (root level)

```yaml
aliases:
  - canonical: "United Kingdom"
    aliases:
      - "UK"
      - "Great Britain"
      - "Britain"
    PlaceID: "01UKGBR000000000000000000"

hierarchies:
  - path: "Europe > United Kingdom > England > London"
    PlaceID: "01LONDON00000000000000000"
    parent_id: "01ENGLAND0000000000000000"
```

---

## Key Features

### 1. Event Mentions (Like People)
Each place tracks all events where it's mentioned:

```json
"event_mentions": [
  {
    "MentionID": "01...",
    "Event_Name": "D-Day Landings",
    "EventID": "01...",
    "Sub_event_Name": "Utah Beach assault",
    "Sub_eventID": "01...",
    "role_in_event": "landing site",
    "original_text": "Utah Beach"
  }
]
```

### 2. Hierarchical Relationships
```yaml
hierarchies:
  - path: "Europe > France > Normandy > Caen"
    PlaceID: "01CAEN0000000000000000000"
    parent_id: "01NORMANDY000000000000000"
```

### 3. Alias Management
```yaml
aliases:
  - canonical: "Gdańsk"
    aliases:
      - "Danzig"
    PlaceID: "01GDANSK0000000000000000"
    historical_context: "Called Danzig under German control (1939-1945)"
```

### 4. Historical Names
```json
"historical_names": [
  {
    "name": "Leningrad",
    "language": "Russian",
    "date_range": "1924-1991"
  }
]
```

### 5. Related Places
```json
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
```

---

## Extraction Workflow

### Phase 2: Extract Places
```python
# src/extraction/places.py

def extract_places(event_file, grok_client, places_dir):
    """Extract places and add to central repository"""
    
    # 1. Extract place mentions from event
    mentions = grok_client.extract_structured(...)
    
    # 2. For each mention, find or create place file
    for mention in mentions:
        place_name = mention.current_name
        place_file = find_or_create_place(place_name, places_dir)
        
        # 3. Add event mention to place file
        add_event_mention(place_file, mention, event_data)
        
        # 4. Update index
        update_place_index(place_name, place_file)
```

### Deduplication
```python
# scripts/find_duplicate_places.py

def find_duplicate_places():
    """Find potential duplicate places"""
    
    # Similar to people deduplication:
    # 1. Name similarity (Warsaw vs Warszawa)
    # 2. Coordinate proximity (<1km apart)
    # 3. Check aliases in place_aliases.yaml
    # 4. Same geography_type
```

### Alias Resolution
```python
# scripts/consolidate_places.py

def consolidate_places():
    """Merge places based on place_aliases.yaml"""
    
    # Load aliases
    aliases = load_yaml("place_aliases.yaml")
    
    # Merge "UK" → "United Kingdom"
    # Merge "Danzig" → "Gdańsk"
```

---

## Benefits

### 1. Cross-Book Tracking
- See all mentions of "Normandy" across all books
- Track place importance by mention count

### 2. Deduplication
- "UK" and "United Kingdom" → same place
- "Danzig" and "Gdańsk" → same place with historical context

### 3. Hierarchical Queries
```python
# Find all places in France
places_in_france = [p for p in places if p.hierarchy.country == "France"]

# Find all cities in Normandy
cities_in_normandy = [p for p in places 
                      if p.hierarchy.region == "Normandy" 
                      and p.geography_type == "city"]
```

### 4. Relationship Mapping
```python
# Find all places near London (within 100km)
nearby = [p for p in london.related_places 
          if p.relationship == "near" and p.distance_km <= 100]
```

### 5. Historical Context
```python
# What was this place called in 1944?
name_in_1944 = get_historical_name(place, "1944")
# "Leningrad" (not "Saint Petersburg")
```

---

## Implementation Files

### Created
1. ✅ **`contextmanagement/Specs/place_v3_central.json`** - New schema
2. ✅ **`place_aliases.yaml`** - Alias and hierarchy management
3. ✅ **`docs/current/PLACE_CENTRAL_REPOSITORY.md`** - This document

### To Create
4. **`src/extraction/places.py`** - Rewrite for central repository
5. **`scripts/find_duplicate_places.py`** - Place deduplication
6. **`scripts/merge_duplicate_places.py`** - Interactive merge tool
7. **`scripts/consolidate_places.py`** - Apply aliases from YAML
8. **`scripts/suggest_place_aliases.py`** - AI-powered alias suggestions

---

## Schema Comparison

| Feature | v2.0.0 (Per-Chapter) | v3.0.0 (Central) |
|---------|---------------------|------------------|
| Storage | Per-chapter files | One file per place |
| Event tracking | No | Yes (event_mentions array) |
| Aliases | No | Yes (YAML managed) |
| Hierarchies | No | Yes (YAML managed) |
| Deduplication | Manual | Automated |
| Cross-book | No | Yes |
| Historical names | Single field | Array with dates |
| Related places | No | Yes |

---

## Migration Strategy

**Note:** No migration needed - rerun extraction from scratch

1. Update `src/extraction/places.py` to use v3 schema
2. Create `output/places/` directory
3. Run `python3 phase2_extract.py`
4. Places extracted to central repository
5. Run deduplication scripts
6. Apply aliases from YAML

---

## Example Queries

### Find all battle locations
```python
battles = [p for p in places if p.geography_type == "battlefield"]
```

### Find places mentioned in multiple books
```python
cross_book = [p for p in places 
              if len(set(m.book for m in p.event_mentions)) > 1]
```

### Get place hierarchy
```python
def get_full_path(place):
    path = [place.current_name]
    while place.hierarchy.parent_place_id:
        parent = get_place(place.hierarchy.parent_place_id)
        path.insert(0, parent.current_name)
        place = parent
    return " > ".join(path)

# Result: "Europe > France > Normandy > Caen"
```

---

## Next Steps

1. ✅ Review and approve v3.0.0 schema
2. ✅ Review place_aliases.yaml structure
3. Rewrite `src/extraction/places.py`
4. Create deduplication scripts
5. Rerun Phase 2 extraction
6. Populate place_aliases.yaml as duplicates found

---

**Status:** Ready for implementation  
**Breaking Changes:** Complete rewrite (no migration)  
**Estimated Effort:** 2-3 days

# MongoDB Import Plan for WWII Historical Data

## Data Overview

**Total JSON Files:** ~1,511 files across 15 collections

### Collections Summary

| Collection | Count | Description |
|------------|-------|-------------|
| logistics | 645 | Supply, transport, capacity constraints |
| places | 248 | Geographic locations with coordinates |
| dates | 176 | Temporal entities with context |
| people_groups | 139 | Organizations, military units, alliances |
| people | 90 | Individual biographical profiles |
| casualties | 85 | Casualty reports (killed, wounded, POW) |
| equipment | 48 | Military equipment with specifications |
| BreakoutAndPursuit | 41 | Book-specific event data |
| Cross-Channel-Attack | 13 | Book-specific event data |
| maps | 9 | Maps from source material |
| weather | 3 | Weather conditions with API data |
| supplemental | 1 | Supplemental materials |

---

## Database Schema Design

### Database: `wwii_history`

### Collections Structure

#### 1. **people** (90 documents)
```javascript
{
  _id: ObjectId(),
  PersonID: "01KK5C0YJTAZ...",  // ULID (indexed)
  name: "Dwight D. Eisenhower",
  source_language: "English",
  biographical_profile: {
    birth_date: "1890-10-14",
    death_date: "1969-03-28",
    nationality: "USA",
    role_type: "military_leader",
    ranks: [...],
    units_served: [...],
    awards: [...]
  },
  event_mentions: [...]
}
```

**Indexes:**
- `PersonID` (unique)
- `name` (text)
- `biographical_profile.nationality`

#### 2. **people_groups** (139 documents)
```javascript
{
  _id: ObjectId(),
  GroupID: "01KK52XHGA...",  // ULID (indexed)
  group_name: "Allies",
  group_type: "alliance",
  country_of_origin: "USA",
  members: [...],
  event_mentions: [...]
}
```

**Indexes:**
- `GroupID` (unique)
- `group_name` (text)
- `group_type`

#### 3. **places** (248 documents)
```javascript
{
  _id: ObjectId(),
  PlaceID: "01KHYP2M...",  // ULID (indexed)
  current_name: "Normandy",
  geography_type: "region",
  coordinates: {
    latitude: 49.1829,
    longitude: -0.3707
  },
  event_mentions: [...]
}
```

**Indexes:**
- `PlaceID` (unique)
- `current_name` (text)
- `coordinates` (2dsphere for geospatial queries)

#### 4. **dates** (176 documents)
```javascript
{
  _id: ObjectId(),
  DateID: "01KK5GFW...",  // ULID (indexed)
  date_start: "1944-07-01",
  date_end: null,
  date_precision: "exact",
  event_mentions: [...]
}
```

**Indexes:**
- `DateID` (unique)
- `date_start` (ascending)
- `date_precision`

#### 5. **events** (combined from book chapters)
```javascript
{
  _id: ObjectId(),
  EventID: "01KK5BEX...",  // ULID (indexed)
  book: "Breakout and Pursuit",
  chapter: "The Allies",
  sub_events: [...],
  related_people: ["PersonID1", "PersonID2"],
  related_places: ["PlaceID1", "PlaceID2"],
  related_dates: ["DateID1", "DateID2"]
}
```

**Indexes:**
- `EventID` (unique)
- `book`
- `chapter`

#### 6. **equipment** (48 documents)
```javascript
{
  _id: ObjectId(),
  EquipmentID: "01KK5B99...",  // ULID (indexed)
  common_name: "tanks",
  category: "armor",
  specifications: {...},
  variants: [...],
  event_mentions: [...]
}
```

**Indexes:**
- `EquipmentID` (unique)
- `common_name` (text)
- `category`

#### 7. **weather** (3 documents)
```javascript
{
  _id: ObjectId(),
  WeatherID: "01KK6440...",  // ULID (indexed)
  date: "1944-07-28",
  location: {
    place_name: "Monthuchon",
    coordinates: {
      latitude: 48.9667,
      longitude: -1.6167
    }
  },
  extracted_data: {...},
  api_data: {...}
}
```

**Indexes:**
- `WeatherID` (unique)
- `date`
- `location.coordinates` (2dsphere)

#### 8. **logistics** (645 documents)
```javascript
{
  _id: ObjectId(),
  LogisticsID: "01KK76YV...",  // ULID (indexed)
  type: "supply_shortage",
  category: "equipment",
  event_context: {...},
  impact: "..."
}
```

**Indexes:**
- `LogisticsID` (unique)
- `type`
- `category`

#### 9. **casualties** (85 documents)
```javascript
{
  _id: ObjectId(),
  CasualtyID: "01KK6T7K...",  // ULID (indexed)
  type: "casualties",
  count: 1147,
  unit: "...",
  event_context: {...}
}
```

**Indexes:**
- `CasualtyID` (unique)
- `type`

#### 10. **maps** (9 documents)
```javascript
{
  _id: ObjectId(),
  MapID: "01KK64SMS9...",  // ULID (indexed)
  title: "...",
  map_type: "tactical",
  image_path: "filestore/maps/...",
  related_events: [...],
  related_places: [...]
}
```

**Indexes:**
- `MapID` (unique)
- `map_type`

---

## Import Strategy

### Phase 1: Core Entities (Independent Collections)
Import collections with no dependencies first:

1. **people** (90 docs)
2. **people_groups** (139 docs)
3. **places** (248 docs)
4. **dates** (176 docs)
5. **equipment** (48 docs)
6. **weather** (3 docs)

### Phase 2: Event Data (Dependent Collections)
Import event data that references core entities:

7. **events** (from BreakoutAndPursuit + Cross-Channel-Attack)
8. **logistics** (645 docs)
9. **casualties** (85 docs)
10. **maps** (9 docs)

### Phase 3: Supplemental Data
11. **supplemental** (1 doc)

---

## Import Script Structure

### Python Script: `import_to_mongodb.py`

```python
#!/usr/bin/env python3
"""Import WWII historical data to MongoDB."""

import json
from pathlib import Path
from pymongo import MongoClient, ASCENDING, GEOSPHERE, TEXT
from pymongo.errors import BulkWriteError

# Configuration
MONGO_URI = "mongodb://localhost:27017/"
DATABASE = "wwii_history"
OUTPUT_DIR = Path("output")

# Collection mappings
COLLECTIONS = {
    "people": "people/*.json",
    "people_groups": "people_groups/*.json",
    "places": "places/*.json",
    "dates": "dates/*.json",
    "equipment": "equipment/*.json",
    "weather": "weather/*.json",
    "logistics": "logistics/*.json",
    "casualties": "casualties/*.json",
    "maps": "maps/*.json",
}

def create_indexes(db):
    """Create indexes for all collections."""
    
    # People indexes
    db.people.create_index([("PersonID", ASCENDING)], unique=True)
    db.people.create_index([("name", TEXT)])
    db.people.create_index([("biographical_profile.nationality", ASCENDING)])
    
    # People groups indexes
    db.people_groups.create_index([("GroupID", ASCENDING)], unique=True)
    db.people_groups.create_index([("group_name", TEXT)])
    db.people_groups.create_index([("group_type", ASCENDING)])
    
    # Places indexes
    db.places.create_index([("PlaceID", ASCENDING)], unique=True)
    db.places.create_index([("current_name", TEXT)])
    db.places.create_index([("coordinates", GEOSPHERE)])
    
    # Dates indexes
    db.dates.create_index([("DateID", ASCENDING)], unique=True)
    db.dates.create_index([("date_start", ASCENDING)])
    
    # Equipment indexes
    db.equipment.create_index([("EquipmentID", ASCENDING)], unique=True)
    db.equipment.create_index([("common_name", TEXT)])
    db.equipment.create_index([("category", ASCENDING)])
    
    # Weather indexes
    db.weather.create_index([("WeatherID", ASCENDING)], unique=True)
    db.weather.create_index([("date", ASCENDING)])
    db.weather.create_index([("location.coordinates", GEOSPHERE)])
    
    # Logistics indexes
    db.logistics.create_index([("LogisticsID", ASCENDING)], unique=True)
    db.logistics.create_index([("type", ASCENDING)])
    
    # Casualties indexes
    db.casualties.create_index([("CasualtyID", ASCENDING)], unique=True)
    db.casualties.create_index([("type", ASCENDING)])
    
    # Maps indexes
    db.maps.create_index([("MapID", ASCENDING)], unique=True)

def import_collection(db, collection_name, pattern):
    """Import JSON files into a collection."""
    files = list(OUTPUT_DIR.glob(pattern))
    
    # Skip index and processed files
    files = [f for f in files if f.name not in ["index.json", ".processed_events.json"]]
    
    if not files:
        print(f"  No files found for {collection_name}")
        return 0
    
    documents = []
    for file in files:
        try:
            with open(file, 'r', encoding='utf-8') as f:
                data = json.load(f)
                documents.append(data)
        except Exception as e:
            print(f"  Error reading {file}: {e}")
    
    if documents:
        try:
            result = db[collection_name].insert_many(documents, ordered=False)
            return len(result.inserted_ids)
        except BulkWriteError as e:
            # Some duplicates expected
            return len(e.details.get('nInserted', 0))
    
    return 0

def main():
    """Main import function."""
    client = MongoClient(MONGO_URI)
    db = client[DATABASE]
    
    print(f"Importing to MongoDB: {DATABASE}")
    print("=" * 60)
    
    # Create indexes
    print("\n1. Creating indexes...")
    create_indexes(db)
    print("   ✓ Indexes created")
    
    # Import collections
    print("\n2. Importing collections...")
    total = 0
    for collection_name, pattern in COLLECTIONS.items():
        print(f"\n   {collection_name}:")
        count = import_collection(db, collection_name, pattern)
        print(f"   ✓ Imported {count} documents")
        total += count
    
    print("\n" + "=" * 60)
    print(f"✓ Import complete: {total} documents")
    print(f"\nDatabase: {DATABASE}")
    print(f"Collections: {len(COLLECTIONS)}")
    
    # Show collection stats
    print("\nCollection Statistics:")
    for collection_name in COLLECTIONS.keys():
        count = db[collection_name].count_documents({})
        print(f"  {collection_name}: {count}")

if __name__ == "__main__":
    main()
```

---

## Usage

### 1. Install Dependencies
```bash
pip install pymongo
```

### 2. Start MongoDB
```bash
# Local MongoDB
mongod --dbpath /path/to/data

# Or Docker
docker run -d -p 27017:27017 --name mongodb mongo:latest
```

### 3. Run Import
```bash
python3 import_to_mongodb.py
```

### 4. Verify Import
```bash
mongosh wwii_history
> db.people.countDocuments()
> db.places.find({current_name: "Normandy"})
> db.dates.find({date_start: {$gte: "1944-06-01", $lte: "1944-06-30"}})
```

---

## Query Examples

### Find all people from USA
```javascript
db.people.find({"biographical_profile.nationality": "USA"})
```

### Find places near coordinates (within 50km)
```javascript
db.places.find({
  coordinates: {
    $near: {
      $geometry: {type: "Point", coordinates: [-0.3707, 49.1829]},
      $maxDistance: 50000
    }
  }
})
```

### Find events in June 1944
```javascript
db.dates.find({
  date_start: {$gte: "1944-06-01", $lte: "1944-06-30"}
})
```

### Find equipment by category
```javascript
db.equipment.find({category: "armor"})
```

### Full-text search for people
```javascript
db.people.find({$text: {$search: "Eisenhower"}})
```

---

## Data Relationships

### Cross-Collection References

All entities use **ULID identifiers** for cross-referencing:

- `PersonID` → people collection
- `GroupID` → people_groups collection
- `PlaceID` → places collection
- `DateID` → dates collection
- `EventID` → events collection
- `EquipmentID` → equipment collection

### Example: Find all events mentioning a person
```javascript
// 1. Get PersonID
const person = db.people.findOne({name: "Dwight D. Eisenhower"})

// 2. Find events
db.events.find({"related_people": person.PersonID})
```

---

## Performance Considerations

1. **Indexes:** All ULID fields indexed for fast lookups
2. **Geospatial:** 2dsphere indexes for location queries
3. **Text Search:** Full-text indexes on name fields
4. **Batch Size:** Import in batches of 1000 for large collections

---

## Next Steps

1. ✅ Review this plan
2. ⬜ Create `import_to_mongodb.py` script
3. ⬜ Test import on sample data
4. ⬜ Run full import
5. ⬜ Create query examples
6. ⬜ Build API endpoints (optional)
7. ⬜ Create visualization dashboard (optional)

---

## Notes

- **ULID Preservation:** Keep original ULIDs for cross-referencing
- **Denormalization:** Event mentions are embedded for performance
- **Normalization:** Core entities (people, places) are separate collections
- **Scalability:** Can shard by book/date range if needed
- **Backup:** Export to JSON before import for safety

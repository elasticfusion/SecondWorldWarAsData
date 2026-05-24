#!/usr/bin/env python3
"""Import WWII historical data to MongoDB."""

import json
from pathlib import Path

from pymongo import ASCENDING, GEOSPHERE, MongoClient, TEXT
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
    print("   Creating indexes...")

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

    # Logistics indexes
    db.logistics.create_index([("type", ASCENDING)])

    # Casualties indexes
    db.casualties.create_index([("type", ASCENDING)])

    # Maps indexes
    db.maps.create_index([("MapID", ASCENDING)], unique=True)

    print("   ✓ Indexes created")


def import_collection(db, collection_name, pattern):
    """Import JSON files into a collection."""
    files = list(OUTPUT_DIR.glob(pattern))

    # Skip index and processed files
    files = [f for f in files if f.name not in ["index.json", ".processed_events.json"]]

    if not files:
        return 0

    documents = []
    for file in files:
        try:
            with open(file, "r", encoding="utf-8") as f:
                data = json.load(f)
                documents.append(data)
        except Exception as e:
            print(f"     Error reading {file.name}: {e}")

    if documents:
        try:
            result = db[collection_name].insert_many(documents, ordered=False)
            return len(result.inserted_ids)
        except BulkWriteError as e:
            # Some duplicates expected
            inserted = e.details.get("nInserted", 0)
            return inserted

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

    client.close()


if __name__ == "__main__":
    main()

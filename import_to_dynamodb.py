#!/usr/bin/env python3
"""Import WWII historical data to DynamoDB.

Mirrors import_to_mongodb.py but targets DynamoDB tables.

Usage:
    python3 import_to_dynamodb.py [--region REGION] [--prefix PREFIX]
"""

import argparse
import json
from decimal import Decimal
from pathlib import Path

OUTPUT_DIR = Path("output")

SKIP_FILES = frozenset(
    [
        "index.json",
        "duplicate_report.json",
        ".processed_events.json",
        "not_duplicates.json",
        "related_groups_report.json",
        "review_queue.json",
    ]
)

# (glob pattern, table suffix, partition key)
COLLECTIONS = {
    "people": ("people/*.json", "people", "PersonID"),
    "people_groups": ("people_groups/*.json", "groups", "GroupID"),
    "places": ("places/*.json", "places", "PlaceID"),
    "dates": ("dates/*.json", "dates", "DateID"),
    "equipment": ("equipment/*.json", "equipment", "EquipmentID"),
    "weather": ("weather/*.json", "weather", "WeatherID"),
    "logistics": ("logistics/*.json", "logistics", "LogisticsID"),
    "casualties": ("casualties/*.json", "casualties", "CasualtyID"),
    "maps": ("maps/*.json", "maps", "MapID"),
    "bibliography": ("bibliography/*.json", "bibliography", "BibliographyID"),
}


def import_collection(table, pattern: str, pk: str) -> int:
    """Import JSON files into a DynamoDB table. Returns count."""
    files = [
        f
        for f in sorted(OUTPUT_DIR.glob(pattern))
        if f.is_file() and f.name not in SKIP_FILES
    ]
    if not files:
        return 0

    count = 0
    with table.batch_writer() as batch:
        for file in files:
            try:
                data = json.loads(file.read_text(encoding="utf-8"))
                if pk not in data:
                    continue
                batch.put_item(Item=_sanitize_for_dynamo(data))
                count += 1
            except Exception as e:
                print(f"     Error: {file.name}: {e}")
    return count


def _sanitize_for_dynamo(data: dict) -> dict:  # type: ignore[type-arg]
    """Remove empty strings (DynamoDB doesn't allow them as attribute values)."""
    cleaned: dict = {}  # type: ignore[type-arg]
    for k, v in data.items():
        sanitized = _sanitize_value(v)
        if sanitized is not _SKIP:
            cleaned[k] = sanitized
    return cleaned


_SKIP = object()


def _sanitize_value(v):  # type: ignore[no-untyped-def]
    """Sanitize a single value for DynamoDB."""
    if isinstance(v, str) and v == "":
        return _SKIP
    if isinstance(v, dict):
        result = _sanitize_for_dynamo(v)
        return result if result else _SKIP
    if isinstance(v, list):
        return [
            _sanitize_for_dynamo(item) if isinstance(item, dict) else item
            for item in v
            if not (isinstance(item, str) and item == "")
        ]
    if isinstance(v, float):
        return Decimal(str(v))
    return v


def main():
    """Main import function."""
    parser = argparse.ArgumentParser(description="Import data to DynamoDB")
    parser.add_argument("--region", default="us-east-1", help="AWS region")
    parser.add_argument("--prefix", default="wwii-", help="Table name prefix")
    args = parser.parse_args()

    import boto3

    dynamo = boto3.resource("dynamodb", region_name=args.region)

    print(f"Importing to DynamoDB (prefix: {args.prefix})")
    print("=" * 60)

    total = 0
    for name, (pattern, table_suffix, pk) in COLLECTIONS.items():
        table = dynamo.Table(f"{args.prefix}{table_suffix}")
        print(f"\n   {name}:")
        count = import_collection(table, pattern, pk)
        print(f"   ✓ Imported {count} items")
        total += count

    print("\n" + "=" * 60)
    print(f"✓ Import complete: {total} items")


if __name__ == "__main__":
    main()

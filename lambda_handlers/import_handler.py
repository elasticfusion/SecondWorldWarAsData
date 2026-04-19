"""Lambda handler for importing entity data to DynamoDB.

Triggered manually or on schedule after pipeline completes.
"""

import logging
import os

logger = logging.getLogger(__name__)
logger.setLevel(os.getenv("LOG_LEVEL", "INFO"))

# Entity type → (S3 prefix, DynamoDB table suffix, partition key)
ENTITY_MAP = {
    "people": ("output/people", "people", "PersonID"),
    "people_groups": ("output/people_groups", "groups", "GroupID"),
    "places": ("output/places", "places", "PlaceID"),
    "dates": ("output/dates", "dates", "DateID"),
    "equipment": ("output/equipment", "equipment", "EquipmentID"),
    "weather": ("output/weather", "weather", "WeatherID"),
    "logistics": ("output/logistics", "logistics", "LogisticsID"),
    "casualties": ("output/casualties", "casualties", "CasualtyID"),
    "maps": ("output/maps", "maps", "MapID"),
    "bibliography": ("output/bibliography", "bibliography", "BibliographyID"),
}

SKIP_FILES = {
    "index.json",
    "duplicate_report.json",
    ".processed_events.json",
    "not_duplicates.json",
    "related_groups_report.json",
    "review_queue.json",
}


def handler(event, _context):
    """Import all entities from S3 to DynamoDB."""
    from pathlib import Path

    from src.utils.backends import create_storage
    from src.utils.config import load_config

    config = load_config()
    storage = create_storage(config, Path("."))
    aws = config.get("aws", {})
    table_prefix = aws.get("database", {}).get("dynamodb_table_prefix", "wwii-")
    region = aws.get("region", "us-east-1")

    import boto3

    dynamo = boto3.resource("dynamodb", region_name=region)

    total = 0
    for entity_type, (prefix, table_suffix, pk) in ENTITY_MAP.items():
        table = dynamo.Table(f"{table_prefix}{table_suffix}")
        files = [
            f
            for f in storage.list_files(prefix, "*.json")
            if f.split("/")[-1] not in SKIP_FILES
        ]
        if not files:
            continue

        count = _import_to_table(storage, files, table, pk)
        logger.info("%s: imported %d items", entity_type, count)
        total += count

    return {"imported": total}


def _import_to_table(storage, files: list, table, pk: str) -> int:
    """Batch-write files to a DynamoDB table. Returns count."""
    count = 0
    with table.batch_writer() as batch:
        for path in files:
            try:
                data = storage.read_json(path)
                if pk not in data:
                    continue
                batch.put_item(Item=data)
                count += 1
            except Exception as e:
                logger.warning("Failed to import %s: %s", path, e)
    return count

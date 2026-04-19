"""Lambda handler for Phase 3: Enrich entities with external data.

Triggered by S3 event (via SNS) when entity files appear in output/people/, output/places/, etc.
"""

import json
import logging
import os
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)
logger.setLevel(os.getenv("LOG_LEVEL", "INFO"))

# Entity types that can be enriched
ENRICHABLE = {
    "people": "enrich_person",
    "people_groups": "enrich_group",
    "places": "enrich_place",
    "bibliography": "enrich_bibliography_entry",
}


def handler(event, _context):
    """Process SNS → S3 event: enrich an entity file."""
    from src.grok_client import GrokClient
    from src.utils.backends import create_cache_backend, create_storage
    from src.utils.config import load_config

    config = load_config()
    storage = create_storage(config, Path("."))
    cache = create_cache_backend(config, Path("cache/api"))
    api_key = _get_api_key(config)
    grok_client = GrokClient(cache, api_key=api_key)

    records = _extract_s3_records(event)
    results = {"enriched": 0, "skipped": 0, "failed": 0}

    for _bucket, key in records:
        entity_type = _get_entity_type(key)
        if entity_type not in ENRICHABLE:
            results["skipped"] += 1
            continue
        try:
            logger.info("Enriching: %s", key)
            _enrich_entity(key, entity_type, storage, grok_client, config)
            results["enriched"] += 1
        except Exception as e:
            logger.error("Failed to enrich %s: %s", key, e)
            results["failed"] += 1

    return results


def _extract_s3_records(event: dict) -> list[tuple[str, str]]:
    """Extract (bucket, key) pairs from SNS → S3 event."""
    records = []
    for record in event.get("Records", []):
        message = record.get("Sns", {}).get("Message", "{}")
        s3_event = json.loads(message) if isinstance(message, str) else message
        for s3_record in s3_event.get("Records", []):
            bucket = s3_record["s3"]["bucket"]["name"]
            key = s3_record["s3"]["object"]["key"]
            records.append((bucket, key))
    return records


def _get_entity_type(key: str) -> str:
    """Extract entity type from S3 key. e.g. output/people/foo.json → people."""
    parts = key.split("/")
    return parts[1] if len(parts) >= 3 else ""


def _get_api_key(config: dict) -> str:
    """Get Grok API key from Secrets Manager or environment."""
    aws = config.get("aws", {})
    secrets_id = aws.get("secrets_id")
    if secrets_id:
        import boto3

        sm = boto3.client("secretsmanager", region_name=aws.get("region", "us-east-1"))
        resp = sm.get_secret_value(SecretId=secrets_id)
        return resp["SecretString"]
    return os.getenv("GROK_API_KEY", "")


def _enrich_entity(
    key: str, entity_type: str, storage, grok_client, config: dict
) -> None:
    """Enrich a single entity file and write back to storage."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)
        data = storage.read_json(key)
        entity_file = tmpdir / Path(key).name
        entity_file.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

        if entity_type == "people":
            from src.extraction.enrich_biographies import enrich_person_file

            enrich_person_file(entity_file, grok_client)
        elif entity_type == "people_groups":
            from src.extraction.enrich_groups import enrich_group_file

            enrich_group_file(entity_file, grok_client)
        elif entity_type == "places":
            from src.extraction.enrich_places import enrich_place_file

            enrich_place_file(entity_file, grok_client)
        elif entity_type == "bibliography":
            from src.extraction.supplemental_advanced import enrich_bibliography_entry

            enrich_bibliography_entry(entity_file, grok_client, config)

        # Upload enriched file back
        enriched = json.loads(entity_file.read_text(encoding="utf-8"))
        storage.write_json(key, enriched)
        logger.info("Enriched: %s", key)

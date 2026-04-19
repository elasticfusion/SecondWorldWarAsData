"""Lambda handler for Phase 2: Extract entities from parsed chapters.

Triggered by S3 event (via SNS) when *-parsed.json files appear in output/.
"""

import json
import logging
import os
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)
logger.setLevel(os.getenv("LOG_LEVEL", "INFO"))


def handler(event, _context):
    """Process SNS → S3 event: extract entities from a parsed chapter."""
    from src.grok_client import GrokClient
    from src.utils.backends import create_cache_backend, create_storage
    from src.utils.config import load_config
    from src.utils.openserp_client import get_openserp_url

    config = load_config()
    storage = create_storage(config, Path("."))
    cache = create_cache_backend(config, Path("cache/api"))

    # Get API key from Secrets Manager or env
    api_key = _get_api_key(config)
    grok_client = GrokClient(cache, api_key=api_key)

    # Ensure OpenSERP is available if needed
    openserp_url = get_openserp_url(config)
    config["external_maps"]["openserp_url"] = openserp_url

    records = _extract_s3_records(event)
    results = {"processed": 0, "failed": 0}

    for bucket, key in records:
        if not key.endswith("-parsed.json"):
            continue
        try:
            logger.info("Extracting: %s", key)
            _extract_chapter(key, storage, grok_client, config)
            results["processed"] += 1
        except Exception as e:
            logger.error("Failed to extract %s: %s", key, e)
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


def _extract_chapter(key: str, storage, grok_client, config: dict) -> None:
    """Extract entities from a single parsed chapter."""
    from src.extraction.events import extract_events

    # Download parsed file to temp
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)
        data = storage.read_json(key)
        parsed_file = tmpdir / Path(key).name
        parsed_file.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

        # Extract events
        book_name = key.split("/")[1]  # output/{BookName}/chapter*-parsed.json
        output_dir = tmpdir / "output" / book_name
        output_dir.mkdir(parents=True, exist_ok=True)

        result = extract_events(parsed_file, grok_client, output_dir)

        # Upload results to storage
        if result and result.exists():
            dest = f"output/{book_name}/{result.name}"
            storage.write_json(dest, json.loads(result.read_text(encoding="utf-8")))
            logger.info("Uploaded: %s", dest)

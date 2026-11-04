"""Shared helpers for the phase Lambda handlers (phase1/phase2/phase3).

extract_s3_records (SNS → S3 event unwrap) was byte-identical in all three phase handlers, and
get_api_key(config) (Grok key from Secrets Manager, else GROK_API_KEY env) was byte-identical in the
phase2 and phase3 handlers. Single source of truth here. (batch_poller has a DISTINCT env-only
_get_api_key() with no config arg — intentionally not routed through this.)
"""

import json
import os
from typing import List, Tuple


def extract_s3_records(event: dict) -> List[Tuple[str, str]]:
    """Extract (bucket, key) pairs from an SNS → S3 event."""
    records: List[Tuple[str, str]] = []
    for record in event.get("Records", []):
        # SNS wraps S3 events in Message
        message = record.get("Sns", {}).get("Message", "{}")
        s3_event = json.loads(message) if isinstance(message, str) else message
        for s3_record in s3_event.get("Records", []):
            bucket = s3_record["s3"]["bucket"]["name"]
            key = s3_record["s3"]["object"]["key"]
            records.append((bucket, key))
    return records


def get_api_key(config: dict) -> str:
    """Get the Grok API key from Secrets Manager (``aws.secrets_id``), else the GROK_API_KEY env."""
    aws = config.get("aws", {})
    secrets_id = aws.get("secrets_id")
    if secrets_id:
        import boto3

        sm = boto3.client("secretsmanager", region_name=aws.get("region", "us-east-1"))
        resp = sm.get_secret_value(SecretId=secrets_id)
        return resp["SecretString"]
    return os.getenv("GROK_API_KEY", "")

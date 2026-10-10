"""S3-event validation backstop (block b-1).

Validates every object written under ``output/`` at the STORAGE PERIMETER using the SAME
``file_lock._validate_entity`` the write path uses (NEVER a reimplementation — the repo's ``src/``
is bundled into this Lambda, and a deploy-time drift check keeps it in sync). This catches a
schema-invalid record that reached S3 by ANY path — a raw-write bypass, a future un-guarded writer,
a partial deploy — that the in-process guard did not stop.

On an INVALID record: fail LOUD — tag the object ``quarantine=true`` IN PLACE (never move it: a bad
fragment holds unique cross-reference mentions that a move would orphan) and alert the operator via
the ``{env}-wwii-phase2-complete`` SNS topic. On a VALID record: no-op (no alert — a clean bulk
write is silent). Non-entity objects (index/metrics/parsed/report/already-quarantined) are skipped.
Validation is READ-ONLY: a deep copy is validated with ``record_stats=False`` (the backstop DETECTS,
it does not FIX or mutate the stored object).
"""

import copy
import json
import logging
import os
from pathlib import Path
from typing import Any, Dict
from urllib.parse import unquote_plus

import boto3

logger = logging.getLogger()
logger.setLevel(os.getenv("LOG_LEVEL", "INFO"))

REGION = os.getenv("AWS_REGION", "us-east-1")
ENV_NAME = os.getenv("ENV_NAME", "dev")
# No fallback ARN: a malformed/placeholder topic would make sns:Publish fail silently and defeat
# the fail-LOUD contract. If unset, _alert logs an error (visible in CloudWatch) instead.
TOPIC_ARN = os.environ.get("NOTIFICATION_TOPIC_ARN", "")
_OUTPUT_PREFIX = "output/"


def _rel_key(key: str) -> str:
    """Strip the ``output/`` prefix so the key matches the shape the guard's entity resolver
    expects (``<entity>/<file>.json`` / ``content/<book>/<chapter>-event.json``)."""
    return key[len(_OUTPUT_PREFIX) :] if key.startswith(_OUTPUT_PREFIX) else key


def _already_quarantined(s3: Any, bucket: str, key: str) -> bool:
    try:
        tags = s3.get_object_tagging(Bucket=bucket, Key=key).get("TagSet", [])
        return any(
            t.get("Key") == "quarantine" and t.get("Value") == "true" for t in tags
        )
    except Exception:  # noqa: BLE001
        return False


def _quarantine(s3: Any, bucket: str, key: str, reason: str) -> None:
    """Tag the object in place — NEVER move it (a move orphans the unique mentions it holds)."""
    s3.put_object_tagging(
        Bucket=bucket,
        Key=key,
        Tagging={
            "TagSet": [
                {"Key": "quarantine", "Value": "true"},
                {"Key": "quarantine_reason", "Value": reason[:256]},
            ]
        },
    )


def _alert(key: str, reason: str) -> None:
    """Publish an aggregate-safe alert (key + validator keyword only — no record values/PII)."""
    if not TOPIC_ARN:
        logger.error(
            "backstop: NOTIFICATION_TOPIC_ARN unset — cannot alert on invalid record %s",
            key,
        )
        return
    try:
        boto3.client("sns", region_name=REGION).publish(
            TopicArn=TOPIC_ARN,
            Subject="WWII Pipeline: DATA QUALITY — invalid record reached S3",
            Message=(
                "The S3 validation backstop found a SCHEMA-INVALID record written under output/ "
                "(it bypassed the in-process write guard). It has been quarantined (tagged in "
                f"place).\n\n  object: {key}\n  reason: {reason}\n\n"
                "Action: a raw/un-guarded writer or a stale deploy let this through — fix the "
                "upstream writer; the record stays quarantined until reprocessed."
            ),
        )
    except Exception as e:  # noqa: BLE001
        logger.error("backstop alert publish failed: %s", e)


def _validate_record(key: str, data: Dict[str, Any]) -> Any:
    """Run the SAME write guard, READ-ONLY (deep copy + record_stats=False). Returns the did-pass
    bool. ``_is_non_entity_file`` short-circuits non-records to True (skip)."""
    from src.utils.file_lock import _is_non_entity_file, _validate_entity

    rel = Path(_rel_key(key))
    if _is_non_entity_file(rel):
        return True
    return _validate_entity(rel, copy.deepcopy(data), record_stats=False)


def _handle_object(s3: Any, bucket: str, key: str) -> None:
    if not key.startswith(_OUTPUT_PREFIX) or not key.endswith(".json"):
        return
    if _already_quarantined(s3, bucket, key):
        return
    body = s3.get_object(Bucket=bucket, Key=key)["Body"].read()
    data = json.loads(body)
    if not isinstance(data, dict):
        return  # non-record artifact (e.g. a top-level list file)
    if _validate_record(key, data):
        return  # valid (or non-entity) — no-op, no alert
    reason = "schema-invalid entity record (failed _validate_entity)"
    logger.error("BACKSTOP: invalid record at s3://%s/%s — quarantining", bucket, key)
    _quarantine(s3, bucket, key, reason)
    _alert(f"s3://{bucket}/{key}", reason)


def _iter_bucket_keys(event: Dict[str, Any]):
    """Yield (bucket, key) from either an EventBridge 'Object Created' event (the delivery path:
    detail.bucket.name / detail.object.key) or a raw S3 notification (Records[].s3.*).
    """
    detail = event.get("detail")
    if isinstance(detail, dict) and detail.get("bucket"):
        bucket = detail.get("bucket", {}).get("name")
        key = detail.get("object", {}).get("key", "")
        if bucket and key:
            yield bucket, unquote_plus(key)
        return
    for rec in event.get("Records", []):
        s3rec = rec.get("s3", {})
        bucket = s3rec.get("bucket", {}).get("name")
        key = unquote_plus(s3rec.get("object", {}).get("key", ""))
        if bucket and key:
            yield bucket, key


def handler(event: Dict[str, Any], _context: Any = None) -> Dict[str, Any]:
    """EventBridge 'Object Created' entrypoint (also accepts raw S3 notifications). Fail-LOUD on an
    invalid record (quarantine+alert). On an INTERNAL error, re-raise so it surfaces in CloudWatch
    and the bounded async retry/DLQ applies — it must never silently pass."""
    s3 = boto3.client("s3", region_name=REGION)
    checked = 0
    for bucket, key in _iter_bucket_keys(event):
        _handle_object(s3, bucket, key)
        checked += 1
    return {"checked": checked}

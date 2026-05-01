"""Lambda handler for Phase 3 gate — blocks enrichment until dedup review is complete.

Sits between Phase 2 completion and Phase 3. When an entity file is created,
this handler checks if dedup review is done. If yes, forwards to Phase 3.
If no, the event is silently dropped (Phase 3 will process all entities
once the review is marked complete).
"""

import json
import logging
import os

logger = logging.getLogger(__name__)
logger.setLevel(os.getenv("LOG_LEVEL", "INFO"))

STATUS_KEY = "dedup/review_status.json"


def handler(event, _context):
    """Check dedup gate and conditionally invoke Phase 3."""
    import os

    from src.utils.config import load_config
    from src.utils.storage import S3Storage

    config = load_config()
    bucket = os.environ.get("S3_BUCKET", "")
    region = config.get("aws", {}).get("region", "us-east-1")
    storage = S3Storage(bucket=bucket, region=region) if bucket else None

    # Check if dedup review is complete
    try:
        status = storage.read_json(STATUS_KEY)
        is_complete = status.get("complete", False)
    except Exception:
        # No status file = review not started yet, block Phase 3
        is_complete = False

    if not is_complete:
        logger.info("Dedup review not complete — Phase 3 blocked")
        return {"action": "blocked", "reason": "dedup review pending"}

    # Review is complete — forward all entity files to Phase 3
    logger.info("Dedup review complete — forwarding to Phase 3")

    aws = config.get("aws", {})
    if not aws.get("enabled"):
        return {"action": "passed", "reason": "dedup complete"}

    # In AWS mode, invoke Phase 3 for all pending entity files
    import boto3

    lambda_client = boto3.client("lambda", region_name=aws.get("region", "us-east-1"))
    phase3_function = os.getenv("PHASE3_FUNCTION_NAME", "")

    # Check if this is a dedup-complete signal (from UI) or an entity event
    for record in event.get("Records", []):
        message = record.get("Sns", {}).get("Message", "{}")
        msg = json.loads(message) if isinstance(message, str) else message

        if msg.get("dedup_complete"):
            # Trigger Phase 3 for all entity types
            count = _trigger_phase3_for_all(
                storage, lambda_client, phase3_function, aws
            )
            return {"action": "triggered_all", "entities": count}

    # Regular entity event — forward directly to Phase 3
    if phase3_function:
        lambda_client.invoke(
            FunctionName=phase3_function,
            InvocationType="Event",
            Payload=json.dumps(event),
        )
    return {"action": "forwarded"}


def _trigger_phase3_for_all(storage, lambda_client, phase3_function, aws):
    """Invoke Phase 3 for all entity files that need enrichment."""
    count = 0
    for prefix in [
        "output/people",
        "output/people_groups",
        "output/places",
        "output/bibliography",
    ]:
        files = storage.list_files(prefix, "*.json")
        for path in files:
            name = path.split("/")[-1]
            if name in (
                "index.json",
                "duplicate_report.json",
                "not_duplicates.json",
                "related_groups_report.json",
                "not_related.json",
                "review_queue.json",
            ):
                continue
            # Build a synthetic S3 event for Phase 3
            s3_event = {
                "Records": [
                    {
                        "Sns": {
                            "Message": json.dumps(
                                {
                                    "Records": [
                                        {
                                            "s3": {
                                                "bucket": {
                                                    "name": aws.get("s3_bucket", "")
                                                },
                                                "object": {"key": path},
                                            }
                                        }
                                    ]
                                }
                            )
                        }
                    }
                ]
            }
            lambda_client.invoke(
                FunctionName=phase3_function,
                InvocationType="Event",
                Payload=json.dumps(s3_event),
            )
            count += 1
    return count

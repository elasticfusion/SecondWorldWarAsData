#!/usr/bin/env python3
"""Phase-3 OpenSERP enqueue pass (SQS scaling design, step 3 — producer).

Enumerates entity files that still need OpenSERP enrichment and sends ONE SQS message per
entity to the OpenSERP work queue. Does NO searching — just enqueues pointers. Stateless
sidecar workers (openserp_worker.py) drain the queue and do the actual search/verify/apply.

A message is a pointer to the entity file (the result + done store), not a copy of its data:
    {"entity_path": "people/bruce c clarke.json", "entity_type": "people",
     "book": "<book>", "enqueued_at": "<iso>", "schema": 1}

Idempotent: entities already marked ``openserp_searched`` within the 90-day window are skipped
(the same gate the batch driver + worker honor), so re-running the producer never double-enqueues
completed work.
"""

import argparse
import datetime as _dt
import json
import logging
import os
from pathlib import Path
from typing import Any, List

logger = logging.getLogger("phase3_enqueue_openserp")

# Entity types that OpenSERP enriches, mapped to their output subdirectory.
ENTITY_TYPES = ["people", "equipment", "people_groups", "places", "source_section"]

_RECHECK_SECONDS = 90 * 86400  # mirrors openserp_searched gate


def _needs_search(data: dict) -> bool:
    """True if this entity still needs OpenSERP search (not searched within the window)."""
    if not data.get("openserp_searched"):
        return True
    import time as _time

    searched_at = data.get("openserp_searched_at", 0)
    return not (searched_at and (_time.time() - searched_at) < _RECHECK_SECONDS)


def build_messages(storage: Any, book: str) -> List[dict]:
    """Scan every OpenSERP entity type and return a work message for each entity needing search."""
    messages: List[dict] = []
    now = _dt.datetime.now(_dt.timezone.utc).isoformat()
    for etype in ENTITY_TYPES:
        for rel_path in storage.list_files(etype, "*.json"):
            try:
                data = storage.read_json(rel_path)
            except Exception as e:  # noqa: BLE001 - skip unreadable, keep going
                logger.debug("skip unreadable %s: %s", rel_path, e)
                continue
            if not isinstance(data, dict) or not _needs_search(data):
                continue
            messages.append(
                {
                    "entity_path": rel_path,
                    "entity_type": etype,
                    "book": book,
                    "enqueued_at": now,
                    "schema": 1,
                }
            )
    return messages


def enqueue(sqs_client: Any, queue_url: str, messages: List[dict]) -> int:
    """Send messages to SQS in batches of 10 (SendMessageBatch). Returns count sent."""
    sent = 0
    for i in range(0, len(messages), 10):
        chunk = messages[i : i + 10]
        entries = [
            {"Id": str(j), "MessageBody": json.dumps(m)} for j, m in enumerate(chunk)
        ]
        resp = sqs_client.send_message_batch(QueueUrl=queue_url, Entries=entries)
        sent += len(resp.get("Successful", []))
        for fail in resp.get("Failed", []):
            logger.warning("SQS enqueue failed: %s", fail)
    return sent


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    ap = argparse.ArgumentParser(description="Enqueue OpenSERP work to SQS")
    ap.add_argument("--output-dir", type=Path, default=Path("output"))
    ap.add_argument(
        "--queue-url",
        default=os.environ.get("OPENSERP_WORK_QUEUE_URL", ""),
        help="SQS work queue URL (or env OPENSERP_WORK_QUEUE_URL)",
    )
    ap.add_argument("--book", default=os.environ.get("BOOK_NAME", ""))
    ap.add_argument(
        "--dry-run", action="store_true", help="count only; do not send to SQS"
    )
    args = ap.parse_args()

    from src.utils.config import load_config

    config = load_config()
    s3_bucket = os.environ.get("S3_BUCKET", "")
    if s3_bucket:
        from src.utils.storage import S3Storage

        region = config.get("aws", {}).get("region", "us-east-1")
        storage: Any = S3Storage(bucket=s3_bucket, prefix="output", region=region)
    else:
        from src.utils.backends import create_storage

        storage = create_storage(config, args.output_dir)

    messages = build_messages(storage, args.book)
    logger.info("OpenSERP enqueue: %d entities need search", len(messages))

    if args.dry_run:
        return 0
    if not args.queue_url:
        logger.error("no --queue-url / OPENSERP_WORK_QUEUE_URL; nothing enqueued")
        return 2

    import boto3

    sqs = boto3.client(
        "sqs", region_name=config.get("aws", {}).get("region", "us-east-1")
    )
    sent = enqueue(sqs, args.queue_url, messages)
    logger.info("OpenSERP enqueue: %d/%d messages sent", sent, len(messages))
    return 0 if sent == len(messages) else 1


if __name__ == "__main__":
    raise SystemExit(main())

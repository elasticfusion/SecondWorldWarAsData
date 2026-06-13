"""Pipeline trigger Lambda — orchestrates ECS task launches.

Triggered by: SQS (S3 notifications via SNS), EventBridge (hourly lock check, spot recovery).
Manages: content queuing, Phase 1/2/3 launches, lock management, dedup reconciliation.
"""

import json
import logging
import os
import time

import boto3

logger = logging.getLogger()
logger.setLevel(os.getenv("LOG_LEVEL", "INFO"))

# Environment variables (set by CloudFormation)
CLUSTER = os.environ.get("ECS_CLUSTER", "")
SUBNETS = [
    s.strip() for s in os.environ.get("PRIVATE_SUBNET_IDS", "").split(",") if s.strip()
]
SG = os.environ.get("SECURITY_GROUP_ID", "")
BUCKET = os.environ.get("S3_BUCKET", "")
CACHE_TABLE = os.environ.get("CACHE_TABLE", "")
NOTIFY_TOPIC = os.environ.get("NOTIFICATION_TOPIC_ARN", "")
ENV_NAME = os.environ.get("ENV_NAME", "dev")
NAT_MANAGER_FN = os.environ.get("NAT_MANAGER_FN", f"{ENV_NAME}-wwii-nat-manager")

PHASE1_TASK_DEF = os.environ.get("PHASE1_TASK_DEF", f"{ENV_NAME}-wwii-phase1-parse")
PHASE2_TASK_DEF = os.environ.get("PHASE2_TASK_DEF", f"{ENV_NAME}-wwii-phase2-extract")
PHASE3_TASK_DEF = os.environ.get("PHASE3_TASK_DEF", f"{ENV_NAME}-wwii-phase3-enrich")

CONTENT_TOPIC = f"{ENV_NAME}-wwii-content-uploaded"
PARSED_TOPIC = f"{ENV_NAME}-wwii-chapter-parsed"
DEDUP_COMPLETE_TOPIC = f"{ENV_NAME}-wwii-dedup-complete"
ENTITY_TOPIC = f"{ENV_NAME}-wwii-entity-created"

TASK_FAMILIES = {
    PHASE1_TASK_DEF: f"{ENV_NAME}-wwii-phase1-parse",
    PHASE2_TASK_DEF: f"{ENV_NAME}-wwii-phase2-extract",
    PHASE3_TASK_DEF: f"{ENV_NAME}-wwii-phase3-enrich",
}

ecs = boto3.client("ecs")
s3 = boto3.client("s3")
dynamo = boto3.resource("dynamodb").Table(CACHE_TABLE)


def handler(event, _context):
    """Main entry point."""
    # Manual invocation: {"source": "manual", "book": "BookName"}
    if event.get("source") == "manual":
        book = event.get("book", "")
        phase = event.get("phase", "1")
        task_map = {"1": PHASE1_TASK_DEF, "2": PHASE2_TASK_DEF, "3": PHASE3_TASK_DEF}
        task_def = task_map.get(phase, PHASE1_TASK_DEF)
        logger.info("Manual trigger for book=%s phase=%s", book or "all", phase)
        _run_task(task_def, "manual", book_name=book)
        return {"status": "launched", "book": book, "phase": phase}

    # Scheduled stale lock check
    if event.get("source") == "scheduled":
        return _handle_scheduled_check()

    # Extract topics and S3 keys from SQS/SNS records
    topics, s3_keys = _extract_records(event)
    logger.info("Trigger topics: %s, keys: %d", topics, len(s3_keys))

    # Write manifest for incremental processing
    if s3_keys:
        _update_manifest(s3_keys)

    # Route by topic
    for topic_name in topics:
        if topic_name == CONTENT_TOPIC:
            _queue_pending(s3_keys)
            _launch_phase1_if_idle()
        elif topic_name == PARSED_TOPIC:
            _queue_parsed(s3_keys)
            _launch_phase2_if_idle()
        elif topic_name == ENTITY_TOPIC:
            if _review_complete():
                _stop_phase2_tasks()
                _run_task(PHASE3_TASK_DEF, topic_name)
            else:
                logger.info("Dedup review not complete, skipping phase3")
        elif topic_name == DEDUP_COMPLETE_TOPIC:
            logger.info("Dedup complete, launching phase3")
            _stop_phase2_tasks()
            _run_task(PHASE3_TASK_DEF, topic_name)
        else:
            logger.warning("Unknown topic: %s", topic_name)


def _handle_scheduled_check():
    """Hourly lock check + dedup reconciliation."""
    logger.info("Scheduled lock check")
    for task_def, family in TASK_FAMILIES.items():
        lock_key = f"lock#{family}"
        try:
            existing = dynamo.get_item(Key={"cache_key": lock_key}).get("Item")
            if existing:
                running = ecs.list_tasks(
                    cluster=CLUSTER, family=family, desiredStatus="RUNNING"
                ).get("taskArns", [])
                if not running:
                    dynamo.delete_item(Key={"cache_key": lock_key})
                    logger.info("Cleared stale lock: %s", family)
        except Exception as e:
            logger.warning("Lock check failed for %s: %s", family, e)

    # Reconcile: if dedup complete but Phase 3 never ran, trigger it
    try:
        phase3_family = f"{ENV_NAME}-wwii-phase3-enrich"
        phase3_lock = dynamo.get_item(Key={"cache_key": f"lock#{phase3_family}"}).get(
            "Item"
        )
        phase3_running = ecs.list_tasks(
            cluster=CLUSTER, family=phase3_family, desiredStatus="RUNNING"
        ).get("taskArns", [])
        if not phase3_lock and not phase3_running:
            resp = s3.get_object(Bucket=BUCKET, Key="dedup/review_status.json")
            status = json.loads(resp["Body"].read())
            if status.get("complete"):
                logger.info("Dedup complete but Phase 3 never ran — triggering")
                _run_task(PHASE3_TASK_DEF, "reconciliation")
    except Exception as e:
        logger.debug("Dedup reconciliation check: %s", e)

    # Reconcile: if pending queues have items but no tasks are running, trigger
    try:
        any_running = any(
            ecs.list_tasks(cluster=CLUSTER, family=fam, desiredStatus="RUNNING").get(
                "taskArns", []
            )
            for fam in TASK_FAMILIES.values()
        )
        if not any_running:
            pending_content = dynamo.get_item(Key={"cache_key": "pending#content"}).get(
                "Item", {}
            )
            if pending_content.get("keys"):
                logger.info(
                    "Pending content queue has %d items, launching Phase 1",
                    len(pending_content["keys"]),
                )
                books = set()
                for k in pending_content["keys"]:
                    parts = k.split("/")
                    if len(parts) >= 2 and parts[0] == "contentrepository":
                        books.add(parts[1])
                book_name = books.pop() if len(books) == 1 else ""
                _run_task(
                    PHASE1_TASK_DEF, "pending-reconciliation", book_name=book_name
                )
            else:
                pending_parsed = dynamo.get_item(
                    Key={"cache_key": "pending#parsed"}
                ).get("Item", {})
                if pending_parsed.get("keys"):
                    logger.info(
                        "Pending parsed queue has %d items, launching Phase 2",
                        len(pending_parsed["keys"]),
                    )
                    _launch_phase2_if_idle()
    except Exception as e:
        logger.debug("Pending queue reconciliation: %s", e)

    return {"action": "lock_check_complete"}


def _extract_records(event):
    """Extract topic names and S3 keys from SQS/SNS records."""
    topics = set()
    s3_keys = []
    for record in event.get("Records", []):
        if "body" in record:
            # SQS format
            try:
                sns_msg = json.loads(record["body"])
                topic_arn = sns_msg.get("TopicArn", "")
                msg = sns_msg.get("Message", "{}")
                if isinstance(msg, str):
                    try:
                        s3_event = json.loads(msg)
                        for s3_rec in s3_event.get("Records", []):
                            s3_keys.append(s3_rec["s3"]["object"]["key"])
                    except Exception as e:
                        logger.warning("Failed to parse S3 event: %s", e)
            except Exception:
                topic_arn = ""
        else:
            # Direct SNS format
            topic_arn = record.get("Sns", {}).get("TopicArn", "")
        if topic_arn:
            topics.add(topic_arn.rsplit(":", 1)[-1])
    return topics, s3_keys


def _update_manifest(s3_keys):
    """Merge S3 keys into pending manifest."""
    manifest_key = "manifests/pending.json"
    try:
        resp = s3.get_object(Bucket=BUCKET, Key=manifest_key)
        existing = json.loads(resp["Body"].read())
    except Exception:
        existing = []
    merged = list(set(existing + s3_keys))
    s3.put_object(Bucket=BUCKET, Key=manifest_key, Body=json.dumps(merged).encode())
    logger.info("Manifest: %d keys", len(merged))


def _review_complete():
    """Check if dedup review is marked complete."""
    try:
        resp = s3.get_object(Bucket=BUCKET, Key="dedup/review_status.json")
        return json.loads(resp["Body"].read()).get("complete", False)
    except Exception:
        return False


def _stop_phase2_tasks():
    """Stop running Phase 2 tasks before launching Phase 3."""
    try:
        family = f"{ENV_NAME}-wwii-phase2-extract"
        tasks = ecs.list_tasks(cluster=CLUSTER, family=family, desiredStatus="RUNNING")
        for arn in tasks.get("taskArns", []):
            ecs.stop_task(cluster=CLUSTER, task=arn, reason="Phase 3 starting")
            logger.info("Stopped phase2 task: %s", arn.split("/")[-1])
    except Exception as e:
        logger.warning("Failed to stop phase2 tasks: %s", e)


def _cancel_delayed_teardown():
    """Cancel any pending delayed networking teardown."""
    schedule_name = f"{ENV_NAME}-wwii-delayed-teardown"
    try:
        scheduler = boto3.client("scheduler")
        scheduler.delete_schedule(Name=schedule_name)
        logger.info("Cancelled delayed teardown")
    except Exception:
        pass  # Schedule may not exist


def _run_task(task_def, source, book_name=""):
    """Create networking, acquire lock, launch ECS task."""
    # Cancel any pending delayed teardown
    _cancel_delayed_teardown()

    # Ensure networking
    try:
        boto3.client("lambda").invoke(
            FunctionName=NAT_MANAGER_FN,
            InvocationType="Event",
            Payload=json.dumps({"action": "create"}).encode(),
        )
    except Exception as e:
        logger.warning("NAT create invoke failed: %s", e)
    _wait_for_networking()

    # Atomic lock
    family = TASK_FAMILIES.get(task_def, "unknown")
    lock_key = f"lock#{family}"
    try:
        dynamo.put_item(
            Item={
                "cache_key": lock_key,
                "response": str(int(time.time())),
                "ttl": int(time.time()) + 7200,
            },
            ConditionExpression="attribute_not_exists(cache_key)",
        )
    except dynamo.meta.client.exceptions.ConditionalCheckFailedException:
        running = ecs.list_tasks(
            cluster=CLUSTER, family=family, desiredStatus="RUNNING"
        ).get("taskArns", [])
        if not running:
            logger.info("Stale lock for %s (no running task), clearing", family)
            dynamo.delete_item(Key={"cache_key": lock_key})
            try:
                dynamo.put_item(
                    Item={
                        "cache_key": lock_key,
                        "response": str(int(time.time())),
                        "ttl": int(time.time()) + 7200,
                    },
                    ConditionExpression="attribute_not_exists(cache_key)",
                )
            except dynamo.meta.client.exceptions.ConditionalCheckFailedException:
                logger.info("Another invocation claimed lock for %s, skipping", family)
                return
        else:
            logger.info("Task %s already locked and running, skipping", family)
            return

    # Launch task
    logger.info(
        "Launching ECS task %s from %s (book=%s)", family, source, book_name or "all"
    )
    overrides = {}
    if book_name:
        overrides = {
            "containerOverrides": [
                {
                    "name": "pipeline",
                    "environment": [{"name": "BOOK_NAME", "value": book_name}],
                }
            ]
        }
    ecs.run_task(
        cluster=CLUSTER,
        taskDefinition=task_def,
        count=1,
        capacityProviderStrategy=[
            {"capacityProvider": "FARGATE_SPOT", "weight": 4, "base": 0},
            {"capacityProvider": "FARGATE", "weight": 1, "base": 0},
        ],
        networkConfiguration={
            "awsvpcConfiguration": {
                "subnets": SUBNETS,
                "securityGroups": [SG],
                "assignPublicIp": "DISABLED",
            }
        },
        overrides=overrides,
    )
    # Notify operator that a task was launched
    _notify_launch(family, book_name, source)


def _notify_launch(family: str, book_name: str, source: str) -> None:
    """Send SNS notification that a pipeline task was launched."""
    topic_arn = os.environ.get("NOTIFICATION_TOPIC_ARN", NOTIFY_TOPIC)
    if not topic_arn:
        return
    try:
        phase = family.split("-")[-1] if "-" in family else family
        msg = f"Pipeline task launched: {phase}\nBook: {book_name or 'all'}\nSource: {source}"
        boto3.client("sns").publish(
            TopicArn=topic_arn,
            Subject=f"WWII Pipeline: {phase} started",
            Message=msg,
        )
    except Exception as e:
        logger.warning("Failed to send notification: %s", e)


def _wait_for_networking():
    """Poll for NAT gateway to be available."""
    max_seconds = int(os.environ.get("NAT_WAIT_SECONDS", "180"))
    ec2 = boto3.client("ec2")
    for _ in range(max_seconds // 10):
        try:
            resp = ec2.describe_nat_gateways(
                Filters=[
                    {"Name": "tag:Name", "Values": [f"{ENV_NAME}-nat"]},
                    {"Name": "state", "Values": ["available", "pending"]},
                ]
            )
            gateways = resp.get("NatGateways", [])
            if any(g["State"] == "available" for g in gateways):
                logger.info("Networking ready (NAT available)")
                return
            if gateways:
                logger.info("NAT gateway pending, waiting...")
        except Exception as e:
            logger.debug("Networking check error: %s", e)
        time.sleep(10)
    logger.warning("NAT not available after %ds — launching task anyway", max_seconds)


def _queue_pending(keys):
    """Queue content keys for Phase 1 processing (atomic, race-safe)."""
    try:
        dynamo.update_item(
            Key={"cache_key": "pending#content"},
            UpdateExpression="SET #k = list_append(if_not_exists(#k, :empty), :new)",
            ExpressionAttributeNames={"#k": "keys"},
            ExpressionAttributeValues={":new": keys, ":empty": []},
        )
        logger.info("Queued %d content keys (atomic append)", len(keys))
    except Exception as e:
        logger.error("Failed to queue pending content: %s", e)


def _launch_phase1_if_idle():
    """Launch Phase 1 only if no pipeline tasks are running."""
    for fam in TASK_FAMILIES.values():
        running = ecs.list_tasks(
            cluster=CLUSTER, family=fam, desiredStatus="RUNNING"
        ).get("taskArns", [])
        if running:
            logger.info(
                "Pipeline busy (%s running), Phase 1 will run after completion", fam
            )
            try:
                boto3.client("sns").publish(
                    TopicArn=NOTIFY_TOPIC,
                    Subject="WWII Pipeline: Content queued",
                    Message="Pipeline is busy. Content queued for processing when current run completes.",
                )
            except Exception as e:
                logger.warning("Failed to send queued notification: %s", e)
            return
    logger.info("Pipeline idle, launching Phase 1 to process queued content")
    book_name = ""
    try:
        resp = dynamo.get_item(Key={"cache_key": "pending#content"})
        keys = resp.get("Item", {}).get("keys", [])
        books = set()
        for k in keys:
            parts = k.split("/")
            if len(parts) >= 2 and parts[0] == "contentrepository":
                books.add(parts[1])
        if len(books) == 1:
            book_name = books.pop()
    except Exception as e:
        logger.warning("Failed to extract book name from pending queue: %s", e)
    _run_task(PHASE1_TASK_DEF, "pending-queue", book_name=book_name)


def _queue_parsed(keys):
    """Queue parsed files, skipping those with existing event files."""
    new_keys = []
    for key in keys:
        if not key.endswith("-parsed.json"):
            continue
        event_key = key.replace("-parsed.json", "-event.json")
        try:
            s3.head_object(Bucket=BUCKET, Key=event_key)
            logger.info("Skipping %s (event file exists)", key.split("/")[-1])
        except Exception:
            new_keys.append(key)
    if not new_keys:
        logger.info("All parsed files already processed, nothing to queue")
        return
    try:
        dynamo.update_item(
            Key={"cache_key": "pending#parsed"},
            UpdateExpression="SET #k = list_append(if_not_exists(#k, :empty), :new)",
            ExpressionAttributeNames={"#k": "keys"},
            ExpressionAttributeValues={":new": new_keys, ":empty": []},
        )
        logger.info("Queued %d parsed keys (atomic append)", len(new_keys))
    except Exception as e:
        logger.error("Failed to queue parsed keys: %s", e)


def _launch_phase2_if_idle():
    """Launch Phase 2 only if no pipeline tasks are running."""
    for fam in TASK_FAMILIES.values():
        running = ecs.list_tasks(
            cluster=CLUSTER, family=fam, desiredStatus="RUNNING"
        ).get("taskArns", [])
        if running:
            logger.info(
                "Pipeline busy (%s running), Phase 2 will run after Phase 1 completes",
                fam,
            )
            return
    # Extract book name from pending parsed keys
    book_name = ""
    try:
        resp = dynamo.get_item(Key={"cache_key": "pending#parsed"})
        keys = resp.get("Item", {}).get("keys", [])
        books = set()
        for k in keys:
            parts = k.split("/")
            if len(parts) >= 3 and parts[0] == "output" and parts[1] == "content":
                books.add(parts[2])
        if len(books) == 1:
            book_name = books.pop()
    except Exception as e:
        logger.debug("Could not extract book name from pending keys: %s", e)
    logger.info("Pipeline idle, launching Phase 2 to process queued parsed files")
    _run_task(PHASE2_TASK_DEF, "pending-parsed", book_name=book_name)

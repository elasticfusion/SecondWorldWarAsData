#!/usr/bin/env python3
"""SQS-driven OpenSERP sidecar worker (SQS scaling design, step 3 — consumer).

Runs alongside its own OpenSERP/Chromium sidecar (reached at localhost:7001). Drains the
OpenSERP work queue one entity at a time:

    receive (entity invisible for VisibilityTimeout)
      -> start heartbeat (ChangeMessageVisibility) so a slow entity is never prematurely redelivered
      -> read entity file; if already openserp_searched within TTL -> delete + skip (idempotent)
      -> enrich_one_<type>(data, localhost:7001, grok)  (search + verify + apply)
      -> mark openserp_searched + persist the entity file (atomic, schema-guarded)
      -> DELETE the message  (authoritative "done")

Crash / Spot interruption before the delete -> visibility lapses -> the message reappears and
another worker retries (already-done URLs replay free from the 90-day cache). After
maxReceiveCount redeliveries SQS routes the message to the DLQ. A SIGTERM (Spot reclaim) stops
the heartbeat and exits WITHOUT deleting the in-flight message so it is redelivered cleanly.
"""

import json
import logging
import os
import signal
import threading
import time
from typing import Any, Dict, Optional

logger = logging.getLogger("openserp_worker")

# entity_type -> (enrich_one callable name, write-guard entity label)
_DISPATCH = {
    "people": ("enrich_one_person", "people"),
    "equipment": ("enrich_one_equipment", "equipment"),
    "people_groups": ("enrich_one_group", "people_groups"),
    "places": ("enrich_one_place", "places"),
    "source_section": ("enrich_one_source_section", "source_section"),
}

_RECHECK_SECONDS = 90 * 86400
_HEARTBEAT_FRACTION = 3  # extend visibility every VT/3 seconds
_MAX_ENTITY_SECONDS = (
    45 * 60
)  # hard cap: stop heartbeating a stuck entity -> redeliver/DLQ

_shutdown = threading.Event()


def _safe_entity_path(entity_path: str, entity_label: str) -> bool:
    """Validate an UNTRUSTED entity_path from an SQS message against path-traversal.

    Must be a relative, normalized path under the dispatched type's own subdirectory and end
    in .json — e.g. 'people/bruce c clarke.json' for entity_label 'people'. Rejects absolute
    paths, '..' segments, backslashes, and anything that would escape output/<type>/.
    """
    import posixpath

    if not entity_path or os.path.isabs(entity_path) or "\\" in entity_path:
        return False
    if ".." in entity_path.split("/"):
        return False
    norm = posixpath.normpath(entity_path)
    if norm != entity_path:  # any normalization change => suspicious
        return False
    return norm.startswith(entity_label + "/") and norm.endswith(".json")


def _install_sigterm() -> None:
    """On SIGTERM (Spot reclaim / ECS stop), request a clean drain without deleting in-flight work."""

    def _handler(_signum, _frame):
        logger.info(
            "SIGTERM received — draining; in-flight message will be redelivered"
        )
        _shutdown.set()

    signal.signal(signal.SIGTERM, _handler)


class _Heartbeat:
    """Background visibility extender for one in-flight message."""

    def __init__(self, sqs: Any, queue_url: str, receipt: str, visibility: int):
        self._sqs = sqs
        self._queue_url = queue_url
        self._receipt = receipt
        self._visibility = visibility
        self._stop = threading.Event()
        self._started = time.monotonic()
        self._thread: Optional[threading.Thread] = None

    def __enter__(self) -> "_Heartbeat":
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        return self

    def _run(self) -> None:
        interval = max(5, self._visibility // _HEARTBEAT_FRACTION)
        while not self._stop.wait(interval):
            if time.monotonic() - self._started > _MAX_ENTITY_SECONDS:
                logger.warning(
                    "entity exceeded %ds — stop heartbeat (will redeliver)",
                    _MAX_ENTITY_SECONDS,
                )
                return
            try:
                self._sqs.change_message_visibility(
                    QueueUrl=self._queue_url,
                    ReceiptHandle=self._receipt,
                    VisibilityTimeout=self._visibility,
                )
            except (
                Exception
            ) as e:  # noqa: BLE001 - best-effort; redelivery is the fallback
                logger.debug("heartbeat failed: %s", e)
                return

    def __exit__(self, *_exc: Any) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)


def process_message(
    body: dict,
    storage: Any,
    openserp_url: str,
    grok_client: Any,
) -> str:
    """Process one work message. Returns 'done' (delete), 'skip' (delete), or 'retry' (do NOT delete)."""
    entity_type = body.get("entity_type", "")
    entity_path = body.get("entity_path", "")
    dispatch = _DISPATCH.get(entity_type)
    if not dispatch or not entity_path:
        logger.warning(
            "unknown/empty message (type=%s path=%s) -> skip", entity_type, entity_path
        )
        return "skip"  # malformed: delete so it doesn't loop to DLQ forever
    fn_name, entity_label = dispatch

    # SECURITY: entity_path is UNTRUSTED (from the SQS body). Bind it to the dispatched type's
    # own subdirectory and reject anything that could escape the output tree (absolute paths,
    # '..', backslashes, non-.json). Prevents path-traversal read/overwrite of arbitrary files.
    if not _safe_entity_path(entity_path, entity_label):
        logger.warning(
            "rejecting out-of-tree/invalid entity_path=%s (type=%s) -> skip",
            entity_path,
            entity_type,
        )
        return "skip"

    try:
        data = storage.read_json(entity_path)
    except Exception as e:  # noqa: BLE001
        logger.warning("cannot read %s (%s) -> retry", entity_path, e)
        return "retry"

    # Idempotent backstop: already searched within the window -> nothing to do.
    searched_at = data.get("openserp_searched_at", 0)
    if (
        data.get("openserp_searched")
        and searched_at
        and (time.time() - searched_at) < _RECHECK_SECONDS
    ):
        return "skip"

    import src.enrichment.openserp_enrichment as oe

    enrich_one = getattr(oe, fn_name)
    try:
        changed = enrich_one(data, openserp_url, grok_client)
    except (
        Exception
    ) as e:  # noqa: BLE001 - a transient OpenSERP/Grok error -> redeliver
        logger.warning("enrich failed for %s (%s) -> retry", entity_path, e)
        return "retry"

    # Mark + persist (write-then-delete: the message is deleted only after a successful write).
    data["openserp_searched"] = True
    data["openserp_searched_at"] = int(time.time())
    oe._metric("entities_searched")
    if not oe._validate_before_write(data, entity_label):
        # Never silently drop a record that fails the schema guard: do NOT delete the message.
        # It redelivers and, after maxReceiveCount, lands in the DLQ for operator inspection.
        logger.warning(
            "schema validation FAILED for %s (%s) -> retry (will DLQ)",
            entity_path,
            entity_label,
        )
        return "retry"
    storage.write_json(entity_path, data)
    if changed:
        oe._metric("entities_enriched")
        logger.info("  ✓ OpenSERP enriched: %s", entity_path)
    return "done"


def run_worker(
    sqs: Any,
    queue_url: str,
    storage: Any,
    openserp_url: str,
    grok_client: Any,
    visibility: int = 900,
    idle_exit_polls: int = 5,
) -> int:
    """Receive-process-delete loop. Returns the number of messages completed (done+skip)."""
    completed = 0
    idle = 0
    while not _shutdown.is_set():
        resp = sqs.receive_message(
            QueueUrl=queue_url,
            MaxNumberOfMessages=1,
            WaitTimeSeconds=20,
            VisibilityTimeout=visibility,
        )
        msgs = resp.get("Messages", [])
        if not msgs:
            idle += 1
            if idle >= idle_exit_polls:
                logger.info("queue empty for %d polls — exiting", idle)
                break
            continue
        idle = 0
        m = msgs[0]
        receipt = m.get("ReceiptHandle")
        if not receipt:
            logger.warning("message missing ReceiptHandle -> skip envelope")
            continue
        try:
            body = json.loads(m.get("Body", ""))
        except Exception:  # noqa: BLE001
            logger.warning("undecodable message body -> delete")
            sqs.delete_message(QueueUrl=queue_url, ReceiptHandle=receipt)
            continue

        with _Heartbeat(sqs, queue_url, receipt, visibility):
            verdict = process_message(body, storage, openserp_url, grok_client)

        if _shutdown.is_set() and verdict != "done":
            # Interrupted mid-flight: leave the message for redelivery.
            break
        if verdict in ("done", "skip"):
            sqs.delete_message(QueueUrl=queue_url, ReceiptHandle=receipt)
            completed += 1
        # 'retry' -> do NOT delete; visibility lapses -> redelivery (-> DLQ after maxReceiveCount)
    return completed


def main() -> int:
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"), format="%(message)s")
    _install_sigterm()

    from pathlib import Path

    from src.grok_client import GrokClient
    from src.utils.backends import create_storage
    from src.utils.config import load_config

    config = load_config()
    queue_url = os.environ.get("OPENSERP_WORK_QUEUE_URL", "")
    if not queue_url:
        logger.error("OPENSERP_WORK_QUEUE_URL not set")
        return 2
    openserp_url = os.environ.get("OPENSERP_URL", "http://localhost:7001")

    # The worker runs as a bare container command (it does NOT go through ecs_entrypoint's
    # runtime config patching), so derive storage from the env the task def provides: when
    # S3_BUCKET is set, read/write entities in S3 under the 'output' prefix; otherwise local.
    region = config.get("aws", {}).get(
        "region", os.environ.get("AWS_DEFAULT_REGION", "us-east-1")
    )
    s3_bucket = os.environ.get("S3_BUCKET", "")
    if s3_bucket:
        from src.utils.storage import S3Storage

        storage: Any = S3Storage(bucket=s3_bucket, prefix="output", region=region)
        logger.info("storage: S3 s3://%s/output", s3_bucket)
    else:
        storage = create_storage(config, Path("output"))
        logger.info("storage: local output/")

    # The worker bypasses ecs_entrypoint, which normally resolves SECRETS_ID -> GROK_API_KEY.
    # Do that fetch here so GrokClient can authenticate.
    if not os.environ.get("GROK_API_KEY") and os.environ.get("SECRETS_ID"):
        try:
            import boto3

            sm = boto3.client("secretsmanager", region_name=region)
            os.environ["GROK_API_KEY"] = sm.get_secret_value(
                SecretId=os.environ["SECRETS_ID"]
            )["SecretString"]
            logger.info("loaded GROK_API_KEY from Secrets Manager")
        except Exception as e:  # noqa: BLE001
            logger.error(
                "failed to load SECRETS_ID %s: %s", os.environ.get("SECRETS_ID"), e
            )

    grok_client = GrokClient(Path(os.environ.get("CACHE_DIR", ".cache")))

    import src.enrichment.openserp_enrichment as oe

    oe.reset_metrics()

    import boto3

    sqs = boto3.client(
        "sqs", region_name=config.get("aws", {}).get("region", "us-east-1")
    )
    vt = int(config.get("openserp", {}).get("worker_visibility_seconds", 900))

    logger.info(
        "OpenSERP worker starting (queue=%s, openserp=%s)", queue_url, openserp_url
    )
    completed = run_worker(
        sqs, queue_url, storage, openserp_url, grok_client, visibility=vt
    )
    oe.write_metrics(Path("output"))
    logger.info("OpenSERP worker done: %d messages completed", completed)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

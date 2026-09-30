"""Pipeline trigger Lambda — orchestrates ECS task launches.

Triggered by: SQS (S3 notifications via SNS), EventBridge (hourly lock check, spot recovery).
Manages: content queuing, Phase 1/2/3 launches, lock management, dedup reconciliation.
"""

import json
import logging
import os
import time
import urllib.parse

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
# OCR (Chandra GPU) Batch queue + job def — raw PDFs route here (Option B).
OCR_JOB_QUEUE = os.environ.get("OCR_JOB_QUEUE", f"{ENV_NAME}-wwii-chandra-gpu")
OCR_JOB_DEF = os.environ.get("OCR_JOB_DEF", f"{ENV_NAME}-wwii-chandra")

PHASE1_TASK_DEF = os.environ.get("PHASE1_TASK_DEF", f"{ENV_NAME}-wwii-phase1-parse")
PHASE2_TASK_DEF = os.environ.get("PHASE2_TASK_DEF", f"{ENV_NAME}-wwii-phase2-extract")
PHASE3_TASK_DEF = os.environ.get("PHASE3_TASK_DEF", f"{ENV_NAME}-wwii-phase3-enrich")

CONTENT_TOPIC = f"{ENV_NAME}-wwii-content-uploaded"
PARSED_TOPIC = f"{ENV_NAME}-wwii-chapter-parsed"
DEDUP_COMPLETE_TOPIC = f"{ENV_NAME}-wwii-dedup-complete"
ENTITY_TOPIC = f"{ENV_NAME}-wwii-entity-created"

# M4-final: kill-switch (§15/§17.3). When on, triggers start the SFN dispatcher
# (concurrent) instead of the serial _launch_phase*_if_idle path. Off by default
# so behavior is unchanged until an operator opts in.
MULTI_DOC_ENABLED = os.environ.get("MULTI_DOC_ENABLED", "false").lower() == "true"
DISPATCHER_STATE_MACHINE_ARN = os.environ.get("DISPATCHER_STATE_MACHINE_ARN", "")


def _multi_doc_active() -> bool:
    """True if concurrency dispatch is switched on AND a state machine is wired."""
    return MULTI_DOC_ENABLED and bool(DISPATCHER_STATE_MACHINE_ARN)


def _lock_key(family: str, book_name: str) -> str:
    """Per-document lock key under multi-doc, singleton otherwise (G1 fix).

    Serial (multi_doc OFF): byte-identical to the legacy singleton `lock#{family}`
    so pool=1 behaves exactly like today. Multi-doc ON + book set: per-document
    `lock#{family}#{book}` so different books hold the same phase concurrently
    while the same book+phase still serializes (matches ecs_entrypoint._lock_key).
    """
    if MULTI_DOC_ENABLED and book_name:
        return f"lock#{family}#{book_name}"
    return f"lock#{family}"


# Content suffixes the pipeline processes (Option B). Zips are IGNORED — the
# pre-stage expands them locally; the archive never reaches processing. Anything
# not in this set (e.g. .zip, .rar, sidecar files) is filtered out before queuing.
_CONTENT_SUFFIXES = (
    ".md",
    ".pdf",
    ".jpg",
    ".jpeg",
    ".png",
    ".tif",
    ".tiff",
    ".docx",
    ".epub",
    ".html",
    ".txt",
)


# Compressed/archive suffixes — ALL ignored. The pre-stage expands archives
# locally; a compressed file never reaches processing. Covers common formats.
_COMPRESSED_SUFFIXES = (
    ".zip",
    ".rar",
    ".7z",
    ".tar",
    ".tar.gz",
    ".tgz",
    ".gz",
    ".bz2",
    ".tar.bz2",
    ".xz",
    ".tar.xz",
    ".z",
    ".lz",
    ".lzma",
    ".cab",
    ".arj",
)


def _content_keys(keys: list) -> list:
    """Keep only processable content keys; drop compressed files and non-content."""
    out = []
    for k in keys:
        low = k.lower()
        if low.endswith(_COMPRESSED_SUFFIXES):
            logger.info("Ignoring compressed file (pre-stage expands these): %s", k)
            continue
        if low.endswith(_CONTENT_SUFFIXES):
            out.append(k)
        else:
            logger.info("Skipping non-content upload: %s", k)
    return out


def _split_by_media(keys: list) -> tuple:
    """Split content keys into (ocr_keys, parse_keys).

    OCR (Chandra GPU, Phase 0): PDFs AND images (.jpg/.png/.tif/... incl scanned
    maps) — Chandra reads both; `_ocr_chunks` handles the single-image case. OCR
    output is later promoted to contentrepository/ and re-triggers parse.

    Parse: already-textual content (.md/.txt/.html) goes straight to the parse
    path. NOTE: .epub/.docx are text-bearing but BINARY — they need a pandoc
    conversion step (Phase-0 ingestion task) before parse and are handled
    separately (not returned here as parse_keys, which would feed the markdown
    parser a binary blob). Until that convert task is wired they are left for the
    Phase-0 path; see the multi-format routing follow-up.
    """
    ocr_suffixes = (".pdf",) + _IMAGE_SUFFIXES
    ocr_keys = [k for k in keys if k.lower().endswith(ocr_suffixes)]
    convert_suffixes = (".epub", ".docx")
    parse_keys = [
        k
        for k in keys
        if not k.lower().endswith(ocr_suffixes)
        and not k.lower().endswith(convert_suffixes)
    ]
    return ocr_keys, parse_keys


def _batch_client():
    """AWS Batch client (patchable in tests — avoids global boto3 patching)."""
    return boto3.client("batch")


_OCR_CHUNK_PAGES = int(os.environ.get("OCR_CHUNK_PAGES", "50"))
_IMAGE_SUFFIXES = (".jpg", ".jpeg", ".png", ".tif", ".tiff", ".webp", ".bmp")


def _pdf_page_count(pdf_key: str) -> int:
    """Best-guess page count for a PDF (0 if unreadable). Downloads to /tmp and
    reads with pypdf (pure-Python, Lambda-safe). A failure returns 0 so the caller
    falls back to a safe whole-PDF job rather than erroring."""
    import tempfile

    local = None
    try:
        from pypdf import PdfReader

        fd, local = tempfile.mkstemp(suffix=".pdf")
        os.close(fd)
        s3.download_file(BUCKET, pdf_key, local)
        return len(PdfReader(local).pages)
    except Exception as e:
        logger.warning(
            "Could not read page count for %s (%s) — whole-PDF fallback", pdf_key, e
        )
        return 0
    finally:
        if local and os.path.exists(local):
            try:
                os.remove(local)
            except OSError:
                pass


def _ocr_chunks(pdf_key: str) -> list:
    """Best-guess chunk plan for an OCR job (operator spec — the code guesses from
    the actual file at submit). Returns a list of page-range args:
      - image (single page) -> [""]  (one job, never chunked)
      - PDF <= OCR_CHUNK_PAGES pages -> [""]  (one whole-PDF job)
      - PDF > OCR_CHUNK_PAGES pages -> ["1-50","51-100",...]  (bounded reclaim loss)
      - unreadable/unknown -> [""]  (safe whole-job fallback)
    An empty string means 'no --page-range' (whole input)."""
    low = pdf_key.lower()
    if low.endswith(_IMAGE_SUFFIXES):
        return [""]  # a scan is one page — never chunk
    if not low.endswith(".pdf"):
        return [""]  # unknown media -> safe whole-job
    pages = _pdf_page_count(pdf_key)
    if pages <= 0 or pages <= _OCR_CHUNK_PAGES:
        return [""]  # small or unreadable -> whole PDF (cheap restart on reclaim)
    ranges = []
    start = 1
    while start <= pages:
        end = min(start + _OCR_CHUNK_PAGES - 1, pages)
        ranges.append(f"{start}-{end}")
        start = end + 1
    return ranges


def _ensure_nat_for_ocr() -> None:
    """Bring the dynamic NAT up so GPU Batch OCR instances have egress (register
    with ECS + pull the Chandra image + S3). Best-effort + fire-and-forget: NAT
    takes ~2min but the Batch job sits RUNNABLE until instances register, so we
    don't block here. nat_manager's demand check keeps NAT up while OCR jobs run
    (it now counts in-flight OCR Batch jobs)."""
    try:
        boto3.client("lambda").invoke(
            FunctionName=NAT_MANAGER_FN,
            InvocationType="Event",  # async — don't block the trigger
            Payload=json.dumps({"action": "create"}).encode(),
        )
        logger.info("Requested NAT create for OCR egress")
    except Exception as e:
        logger.warning("Failed to request NAT for OCR: %s", e)


def _submit_ocr(pdf_key: str) -> bool:
    """Submit Chandra GPU OCR for a raw PDF/image (Option B: -> Phase 0), with
    best-guess page-range chunking so a SPOT reclaim loses one chunk, not the whole
    doc. One atomic per-book claim (ocr#{book}) guards the WHOLE set of chunk jobs.
    Output lands at ocr-output/{book}/[chunk-{range}/] for the downstream merge."""
    try:
        pdf_name = pdf_key.split("/")[-1].rsplit(".", 1)[0]
        # G4 (§8) — deny redundant submissions AT INTAKE (per book, covering all its
        # chunks). A duplicate ObjectCreated must not re-launch the chunk set.
        claim_key = f"ocr#{pdf_name}"
        try:
            dynamo.put_item(
                Item={
                    "cache_key": claim_key,
                    "response": str(int(time.time())),
                    "ttl": int(time.time()) + 86400,
                },
                ConditionExpression="attribute_not_exists(cache_key)",
            )
        except dynamo.meta.client.exceptions.ConditionalCheckFailedException:
            logger.info(
                "OCR intake denied: %s already claimed (in-flight or recent) — skipping",
                pdf_name,
            )
            return False
        s3_input = f"s3://{BUCKET}/{pdf_key}"
        chunks = _ocr_chunks(pdf_key)
        try:
            batch = _batch_client()
            for rng in chunks:
                if rng:
                    a, b = rng.split("-")
                    out = f"s3://{BUCKET}/ocr-output/{pdf_name}/chunk-p{int(a):04d}-{int(b):04d}/"
                    job_name = f"chandra-{pdf_name}-p{a}-{b}"[:128].replace(" ", "_")
                    cmd = [s3_input, out, "--page-range", rng]
                else:
                    out = f"s3://{BUCKET}/ocr-output/{pdf_name}/"
                    job_name = f"chandra-{pdf_name}"[:128].replace(" ", "_")
                    cmd = [s3_input, out]
                batch.submit_job(
                    jobName=job_name,
                    jobQueue=OCR_JOB_QUEUE,
                    jobDefinition=OCR_JOB_DEF,
                    containerOverrides={"command": cmd},
                )
                logger.info("Submitted OCR job %s (%s)", job_name, rng or "whole")
        except Exception:
            dynamo.delete_item(Key={"cache_key": claim_key})  # release for retry
            raise
        logger.info(
            "Submitted %d OCR job(s) for %s (chunked=%s)",
            len(chunks),
            pdf_key,
            len(chunks) > 1,
        )
        return True
    except Exception as e:
        logger.error("Failed to submit OCR job for %s: %s", pdf_key, e)
        return False


def _seed_docs(keys: list, next_phase: str) -> None:
    """Seed doc# lifecycle records so the dispatcher's enumerate_pending finds them.

    The trigger writes pending#* queues, but the SFN dispatcher enumerates doc#
    records (§8) — without seeding, a multi-doc dispatch would find 0 dispatchable
    docs and exit. One doc per book (derived from contentrepository/{book}/...),
    status held_unprocessed, at the given next_phase. Idempotent upsert."""
    from src.ingestion import doc_lifecycle

    books: dict = {}
    for k in keys:
        parts = k.split("/")
        if len(parts) >= 2 and parts[0] == "contentrepository":
            books[parts[1]] = k  # book -> a representative source key
    for book, src_key in books.items():
        try:
            doc_lifecycle.upsert(
                book,
                status="held_unprocessed",
                book=book,
                next_phase=next_phase,
                source_path=src_key,
            )
        except Exception as e:
            logger.warning("Failed to seed doc# for %s: %s", book, e)


def _start_dispatcher(reason: str) -> bool:
    """Start one SFN dispatcher drain execution (idempotent-ish: skip if running).

    Returns True if it started (or one is already running), False on error — the
    caller falls back to the serial path so a dispatcher misconfig never strands
    work.
    """
    try:
        sfn = boto3.client("stepfunctions")
        # Don't pile up executions — if one is already draining, let it continue.
        running = sfn.list_executions(
            stateMachineArn=DISPATCHER_STATE_MACHINE_ARN,
            statusFilter="RUNNING",
            maxResults=1,
        ).get("executions", [])
        if running:
            logger.info(
                "Dispatcher already draining — not starting another (%s)", reason
            )
            return True
        sfn.start_execution(
            stateMachineArn=DISPATCHER_STATE_MACHINE_ARN,
            input=json.dumps(
                {
                    "source": reason,
                    # Adaptive pool bounds (§5.0). Operator-tunable via env; clamp_pool
                    # clamps pool_max down to the live Fargate vCPU quota and floors
                    # at pool_min. Defaults match clamp_pool's own defaults.
                    "pool_min": int(os.environ.get("POOL_MIN", "2")),
                    "pool_max": int(os.environ.get("POOL_MAX", "8")),
                }
            ),
        )
        logger.info("Started dispatcher drain execution (%s)", reason)
        return True
    except Exception as e:
        logger.error(
            "Failed to start dispatcher (%s) — falling back to serial: %s", reason, e
        )
        return False


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

    # Phase-complete event: a phase's ECS task finished and invoked us to drive the
    # NEXT phase immediately (event-driven chain — no waiting for the 15-min poll).
    # This is what resumes work parked in pending#* while the pipeline was busy.
    if event.get("source") == "phase-complete":
        completed = str(event.get("phase", ""))
        logger.info("Phase-complete event: phase=%s -> driving next phase", completed)
        return _drive_next_phase(completed)

    # Extract topics and S3 keys from SQS/SNS records
    topics, s3_keys = _extract_records(event)
    logger.info("Trigger topics: %s, keys: %d", topics, len(s3_keys))

    # Write manifest for incremental processing
    if s3_keys:
        _update_manifest(s3_keys)

    # Route by topic
    for topic_name in topics:
        if topic_name == CONTENT_TOPIC:
            # Option B: fire on ALL contentrepository/ uploads; route by media.
            content = _content_keys(s3_keys)
            if not content:
                logger.info("No processable content in upload batch — nothing to do")
                continue
            pdfs, parse_keys = _split_by_media(content)
            # Raw PDFs -> Chandra OCR (Phase 0). OCR output later re-triggers the
            # parse path via its own upload.
            if pdfs:
                # GPU Batch instances launch into the private GPU subnets whose
                # 0.0.0.0/0 route points at the dynamic NAT — they need egress to
                # register with ECS + pull the Chandra image + read/write S3.
                # Without NAT up, instances boot but never join the cluster and
                # jobs sit RUNNABLE forever. Ensure NAT is up at OCR submit.
                _ensure_nat_for_ocr()
            for pdf in pdfs:
                _submit_ocr(pdf)
            if not parse_keys:
                continue
            _queue_pending(parse_keys)
            if _multi_doc_active():
                _seed_docs(parse_keys, "phase1")
                if _start_dispatcher("content-uploaded"):
                    continue
            _launch_phase1_if_idle()
        elif topic_name == PARSED_TOPIC:
            _queue_parsed(s3_keys)
            if _multi_doc_active():
                _seed_docs(s3_keys, "phase2")
                if _start_dispatcher("chapter-parsed"):
                    continue
            _launch_phase2_if_idle()
        elif topic_name == ENTITY_TOPIC:
            pass  # Dead path — Phase 3 triggered via dedup-complete or auto-trigger
        elif topic_name == DEDUP_COMPLETE_TOPIC:
            logger.info("Dedup complete, launching phase3")
            _stop_phase2_tasks()
            _run_task(PHASE3_TASK_DEF, topic_name)
        else:
            logger.warning("Unknown topic: %s", topic_name)


def _drive_next_phase(completed_phase: str) -> dict:
    """Event-driven phase chaining: a completed phase invokes this to launch the
    NEXT phase immediately from parked pending#* queues (instead of relying on the
    15-min scheduled poll). Idempotent — only launches when the cluster is idle, so
    a duplicate phase-complete event cannot double-launch.

    completed_phase: "1" (parse done -> drive Phase 2), "2" (extract done -> drive
    Phase 3 if enrich queued, else drain any content parked while busy), or "" to
    just reconcile all pending queues. Delegates to _reconcile_pending so the
    event-driven path and the scheduled backstop share ONE drain implementation.
    """
    launched = _reconcile_pending(reason=f"phase-{completed_phase}-complete")
    return {
        "action": "drive_next_phase",
        "completed": completed_phase,
        "launched": launched,
    }


def _reconcile_pending(reason: str) -> list:
    """Launch the next phase from parked pending#* queues IF the cluster is idle.

    Shared by the event-driven phase-complete path and the scheduled backstop.
    Returns the list of phases launched (for observability). Errors are logged at
    WARNING (not debug) so a silent drain failure — the B460 strand root cause —
    is visible.
    """
    launched: list = []
    try:
        any_running = any(
            ecs.list_tasks(cluster=CLUSTER, family=fam, desiredStatus="RUNNING").get(
                "taskArns", []
            )
            for fam in TASK_FAMILIES.values()
        )
        if any_running:
            logger.info("Reconcile (%s): cluster busy, deferring drain", reason)
            return launched

        # 1) Content parked for Phase 1 (parse).
        pending_content = dynamo.get_item(Key={"cache_key": "pending#content"}).get(
            "Item", {}
        )
        if pending_content.get("keys"):
            books = set()
            for k in pending_content["keys"]:
                parts = k.split("/")
                if len(parts) >= 2 and parts[0] == "contentrepository":
                    books.add(parts[1])
            book_name = books.pop() if len(books) == 1 else ""
            logger.info(
                "Reconcile (%s): %d content key(s) parked -> launching Phase 1 (book=%s)",
                reason,
                len(pending_content["keys"]),
                book_name or "all",
            )
            _run_task(PHASE1_TASK_DEF, f"reconcile-{reason}", book_name=book_name)
            launched.append("1")
            return launched

        # 2) Parsed books parked for Phase 2 (extract).
        pending_books = _get_pending_books()
        if pending_books:
            logger.info(
                "Reconcile (%s): parsed queue for %d book(s) -> launching Phase 2 (%s)",
                reason,
                len(pending_books),
                pending_books[0],
            )
            _launch_phase2_if_idle(book_name=pending_books[0])
            launched.append("2")
            return launched

        # 3) Books parked for Phase 3 (enrich).
        pending_enrich = _get_pending_books_for_enrich()
        if pending_enrich:
            logger.info(
                "Reconcile (%s): enrich queue for %s -> launching Phase 3",
                reason,
                pending_enrich[0],
            )
            _run_task(
                PHASE3_TASK_DEF, f"reconcile-{reason}", book_name=pending_enrich[0]
            )
            launched.append("3")
            return launched

        logger.info("Reconcile (%s): no pending work to launch", reason)
    except Exception as e:
        # WAS logger.debug -> silently swallowed the B460 strand. Now WARNING.
        logger.warning("Reconcile (%s) failed: %s", reason, e)
    return launched


def _handle_scheduled_check():
    """Hourly lock check + dedup reconciliation."""
    logger.info("Scheduled lock check")
    for _task_def, family in TASK_FAMILIES.items():
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

    # Backstop reconciliation: the event-driven phase-complete chain is primary,
    # but if a phase-complete invoke was lost, this scheduled poll drains any
    # parked pending#* queue when the cluster is idle. Shared drain implementation
    # (WARNING-level errors) — this is the B460-strand fix (was a silent block).
    _reconcile_pending(reason="scheduled-backstop")

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
                            # S3 event notifications URL-encode the object key
                            # (space -> '+', other chars -> %XX). Decode so the
                            # real key (e.g. "B 400-499/...") is used downstream —
                            # otherwise OCR/parse download the wrong (nonexistent)
                            # path. unquote_plus handles both '+' and %XX.
                            raw_key = s3_rec["s3"]["object"]["key"]
                            s3_keys.append(urllib.parse.unquote_plus(raw_key))
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

    # Atomic lock. Per-book under multi-doc (G1) so concurrent books don't share
    # one phase lock; singleton in serial mode (unchanged).
    family = TASK_FAMILIES.get(task_def, "unknown")
    lock_key = _lock_key(family, book_name)
    try:
        dynamo.put_item(
            Item={
                "cache_key": lock_key,
                "book": book_name or "all",
                "response": str(int(time.time())),
                "ttl": int(time.time()) + 7200,
            },
            ConditionExpression="attribute_not_exists(cache_key)",
        )
    except dynamo.meta.client.exceptions.ConditionalCheckFailedException:
        # Lock exists. A task that is PROVISIONING/PENDING (NAT cold-start) is NOT
        # stale — treating "not RUNNING" as stale is the G1 race that let a 2nd
        # doc clear the lock and double-launch. Only a genuinely dead lock (no
        # task in ANY live state) may be reclaimed.
        live = []
        for status in ("PROVISIONING", "PENDING", "RUNNING"):
            live += ecs.list_tasks(
                cluster=CLUSTER, family=family, desiredStatus=status
            ).get("taskArns", [])
        if not live:
            logger.info("Stale lock for %s (no live task), clearing", lock_key)
            dynamo.delete_item(Key={"cache_key": lock_key})
            try:
                dynamo.put_item(
                    Item={
                        "cache_key": lock_key,
                        "book": book_name or "all",
                        "response": str(int(time.time())),
                        "ttl": int(time.time()) + 7200,
                    },
                    ConditionExpression="attribute_not_exists(cache_key)",
                )
            except dynamo.meta.client.exceptions.ConditionalCheckFailedException:
                logger.info(
                    "Another invocation claimed lock for %s, skipping", lock_key
                )
                return
        else:
            logger.info("Task %s already locked and live, skipping", lock_key)
            # Queue per-book requests for later processing
            if book_name:
                if task_def == PHASE3_TASK_DEF:
                    _queue_pending_enrich(book_name)
                elif task_def == PHASE2_TASK_DEF:
                    _queue_pending_parsed_book(book_name)
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
    """Launch Phase 1 only if no pipeline tasks are active (RUNNING/PENDING/
    PROVISIONING — a starting task counts as busy; the G1 race was treating a
    PROVISIONING task as idle and double-launching)."""
    for fam in TASK_FAMILIES.values():
        active = []
        for status in ("PROVISIONING", "PENDING", "RUNNING"):
            active += ecs.list_tasks(
                cluster=CLUSTER, family=fam, desiredStatus=status
            ).get("taskArns", [])
        if active:
            logger.info(
                "Pipeline busy (%s active), Phase 1 will run after completion", fam
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
    """Queue parsed files per book, skipping those with existing event files."""
    # Group keys by book
    by_book = {}
    for key in keys:
        if not key.endswith("-parsed.json"):
            continue
        event_key = key.replace("-parsed.json", "-event.json")
        try:
            s3.head_object(Bucket=BUCKET, Key=event_key)
            logger.info("Skipping %s (event file exists)", key.split("/")[-1])
        except Exception:
            # Extract book name from key: output/content/{BookName}/chapter*-parsed.json
            parts = key.split("/")
            book = parts[2] if len(parts) >= 4 else "unknown"
            by_book.setdefault(book, []).append(key)

    if not by_book:
        logger.info("All parsed files already processed, nothing to queue")
        return

    # Write per-book queues
    for book, book_keys in by_book.items():
        try:
            dynamo.update_item(
                Key={"cache_key": f"pending#parsed#{book}"},
                UpdateExpression="SET #k = list_append(if_not_exists(#k, :empty), :new)",
                ExpressionAttributeNames={"#k": "keys"},
                ExpressionAttributeValues={":new": book_keys, ":empty": []},
            )
            logger.info(
                "Queued %d parsed keys for %s (atomic append)", len(book_keys), book
            )
        except Exception as e:
            logger.error("Failed to queue parsed keys for %s: %s", book, e)


def _get_pending_books() -> list:
    """Return list of book names with pending parsed queues."""
    try:
        resp = dynamo.scan(
            FilterExpression="begins_with(cache_key, :prefix)",
            ExpressionAttributeValues={":prefix": "pending#parsed#"},
            ProjectionExpression="cache_key",
            Limit=1000,  # Cap scan cost; pending queue will never exceed this
        )
        books = []
        for item in resp.get("Items", []):
            # pending#parsed#BookName → BookName
            key = item["cache_key"]
            book = key.split("#", 2)[2] if key.count("#") >= 2 else ""
            if book:
                books.append(book)
        return sorted(books)
    except Exception as e:
        logger.warning("Failed to scan pending books: %s", e)
        return []


def _launch_phase2_if_idle(book_name=""):
    """Launch Phase 2 only if no pipeline tasks are running."""
    for fam in TASK_FAMILIES.values():
        running = ecs.list_tasks(
            cluster=CLUSTER, family=fam, desiredStatus="RUNNING"
        ).get("taskArns", [])
        if running:
            logger.info(
                "Pipeline busy (%s running), Phase 2 will run after current task completes",
                fam,
            )
            return
    # If no book specified, pick first from pending queues
    if not book_name:
        pending = _get_pending_books()
        book_name = pending[0] if pending else ""
    logger.info("Pipeline idle, launching Phase 2 for book=%s", book_name)
    _run_task(PHASE2_TASK_DEF, "pending-parsed", book_name=book_name)


def _queue_pending_enrich(book_name: str) -> None:
    """Queue a book for Phase 3 enrichment when the lock is held."""
    try:
        dynamo.put_item(
            Item={"cache_key": f"pending#enrich#{book_name}", "book": book_name}
        )
        logger.info("Queued Phase 3 enrichment for %s", book_name)
    except Exception as e:
        logger.error("Failed to queue enrichment for %s: %s", book_name, e)


def _get_pending_books_for_enrich() -> list:
    """Return list of book names with pending enrichment queues."""
    try:
        resp = dynamo.scan(
            FilterExpression="begins_with(cache_key, :prefix)",
            ExpressionAttributeValues={":prefix": "pending#enrich#"},
            ProjectionExpression="cache_key",
            Limit=100,
        )
        books = []
        for item in resp.get("Items", []):
            key = item["cache_key"]
            book = key.split("#", 2)[2] if key.count("#") >= 2 else ""
            if book:
                books.append(book)
        return sorted(books)
    except Exception as e:
        logger.warning("Failed to scan pending enrich: %s", e)
        return []


def _queue_pending_parsed_book(book_name: str) -> None:
    """Queue a book for Phase 2 extraction when the lock is held (no file keys, just marks book as pending)."""
    try:
        dynamo.update_item(
            Key={"cache_key": f"pending#parsed#{book_name}"},
            UpdateExpression="SET book = if_not_exists(book, :b)",
            ExpressionAttributeValues={":b": book_name},
        )
        logger.info("Queued Phase 2 extraction for %s", book_name)
    except Exception as e:
        logger.error("Failed to queue extraction for %s: %s", book_name, e)

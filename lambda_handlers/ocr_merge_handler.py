"""OCR -> parse handoff (UNATTENDED_READINESS #1).

When a Chandra OCR Batch job SUCCEEDS, its markdown lands at
`ocr-output/{book}/[chunk-*/]input/input.md` — but nothing promotes it to
`contentrepository/`, so the parse phase never triggers and raw PDFs dead-end
after OCR (verified 2026-09-28).

This Lambda is invoked by an EventBridge rule on AWS Batch job-state-change
(status SUCCEEDED, chandra job queue). It merges the OCR markdown into
`contentrepository/{book}/{book}.md`, whose ObjectCreated event then triggers the
existing parse path via the S3 notification. Self-contained (no heavy imports);
mirrors submit_ocr_job.merge_outputs but handles the whole-PDF (no-chunk) case.
"""

import logging
import os

import boto3


from src.ingestion.book_slug import book_slug
from src.utils.observe import observe_error, observe_warn

logger = logging.getLogger()
logger.setLevel(os.getenv("LOG_LEVEL", "INFO"))

REGION = os.getenv("AWS_REGION", "us-east-1")
# Match the convention used by every other handler (empty default + guard) so a
# missing S3_BUCKET fails loud rather than silently writing to a wrong bucket.
BUCKET = os.environ.get("S3_BUCKET", "")
CHANDRA_QUEUE_SUFFIX = "chandra"
ENV_NAME = os.getenv("ENV_NAME", "dev")
CLUSTER = os.getenv("ECS_CLUSTER", f"{ENV_NAME}-wwii-pipeline")
PHASE0_INGEST_TASK_DEF = os.getenv(
    "PHASE0_INGEST_TASK_DEF", f"{ENV_NAME}-wwii-phase0-ingest"
)
PHASE0_CROSSCHECK_TASK_DEF = os.getenv(
    "PHASE0_CROSSCHECK_TASK_DEF", f"{ENV_NAME}-wwii-phase0-crosscheck"
)


def _is_structured(s3, book: str) -> bool:
    """True if the trigger tagged this book structured-reference (OOB track):
    an ``ocr-output/{book}/.structured`` sidecar marker exists."""
    try:
        s3.head_object(Bucket=BUCKET, Key=f"ocr-output/{book}/.structured")
        return True
    except Exception:  # noqa: BLE001 - absent marker (404) = not structured
        return False


def _route_to_oob(s3, book: str, merged: str) -> str:
    """Structured-reference routing: place the OCR markdown where the OOB parser
    (phase0_ingest.discover_oob_markdown) consumes it (``{book}/ocr_output/*.md``)
    and launch the phase0-ingest ECS task. Returns the OOB input key on a confirmed
    launch, or ``""`` if the phase0-ingest task FAILED to launch (#44: a launch
    failure must NOT masquerade as a successful promote — the OOB markdown is in S3
    but nothing will parse it, so the caller must see the drop). Does NOT promote to
    contentrepository/ (so it never enters narrative parse)."""
    oob_key = f"contentrepository/{book}/ocr_output/{book}.md"
    s3.put_object(Bucket=BUCKET, Key=oob_key, Body=merged.encode("utf-8"))
    logger.info(
        "STRUCTURED route: OCR markdown -> s3://%s/%s (OOB parser track)",
        BUCKET,
        oob_key,
    )
    # #44: a launch failure must NOT return a success key — the OOB markdown is in S3
    # but nothing will parse it. _launch_phase0_ingest emits the canonical OBSERV at the
    # point of loss and returns False, so we surface the drop instead of a bogus promote.
    if not _launch_phase0_ingest(book):
        return ""
    return oob_key


def _launch_phase0_ingest(book: str) -> bool:
    """Launch the phase0-ingest (OOB parser) ECS task for ``book`` (see ``_launch_task``)."""
    return bool(
        _launch_task(
            book, PHASE0_INGEST_TASK_DEF, "oob_launch_error", "OOB book will NOT parse"
        )
    )


def _retry_memory() -> str:
    """MiB for the relaunched cross-check task: Fargate takes 2048-8192 in steps of 1024 at 1 vCPU
    (the task's own is 2048); anything else would fail every retry, so it falls back to 4096.
    """
    raw = os.getenv("CROSSCHECK_RETRY_MEMORY", "4096")
    if raw.isdigit() and 2048 <= int(raw) <= 8192 and int(raw) % 1024 == 0:
        return raw
    logger.warning("OBSERV ocr_merge.bad_retry_memory value=%r — using 4096", raw)
    return "4096"


CROSSCHECK_RETRY_MEMORY = _retry_memory()


def _launch_crosscheck(book: str, submission: str, memory: str = "") -> str:
    """Launch the Phase 0 cross-check task (phase0_ocr_crosscheck.py) for ``book``'s OCR
    ``submission``: it corrects the OCR against the upload's validation URL, then publishes the
    chapters. Returns the task's ARN, or "" if it did not launch."""
    return _launch_task(
        book,
        PHASE0_CROSSCHECK_TASK_DEF,
        "crosscheck_launch_error",
        "OCR'd book will NOT be cross-checked or parsed",
        extra_env=[{"name": "SUBMISSION_ID", "value": submission}],
        memory=memory,
    )


def _launch_task(
    book: str,
    task_def: str,
    event: str,
    consequence: str,
    extra_env=None,
    memory: str = "",
) -> str:
    """Launch ``task_def`` (an ECS task taking BOOK_NAME) for ``book``. Returns the task's ARN on a
    confirmed launch, "" on ANY launch failure (exception OR an ECS placement that placed no
    task). On failure emits a canonical, greppable, Slack-escalating
    ``OBSERV ocr_merge.<event> status=error`` at the point of loss (#44) so a book that will
    never be processed is loud, not a silent success. Never raises."""
    try:
        resp = _run_task(book, task_def, extra_env, memory)
    except Exception as e:  # noqa: BLE001 - surface loud, don't crash the merge
        _launch_failed(book, task_def, event, consequence, type(e).__name__, str(e))
        return ""
    if not resp.get("tasks") or resp.get("failures"):
        # ECS placement failure (capacity/quota) - run_task does NOT raise.
        _launch_failed(
            book,
            task_def,
            event,
            consequence,
            "no_task_placed",
            str(resp.get("failures")),
        )
        return ""
    logger.info("Launched %s for %s", task_def, book)
    return str(resp["tasks"][0].get("taskArn") or "launched")


def _run_task(book: str, task_def: str, extra_env, memory: str) -> dict:
    subnets = [x for x in os.getenv("PRIVATE_SUBNET_IDS", "").split(",") if x]
    sg = os.getenv("SECURITY_GROUP_ID", "")
    environment = [{"name": "BOOK_NAME", "value": book}] + list(extra_env or [])
    return (
        boto3.client("ecs", region_name=REGION).run_task(
            cluster=CLUSTER,
            taskDefinition=task_def,
            count=1,
            launchType="FARGATE",
            networkConfiguration={
                "awsvpcConfiguration": {
                    "subnets": subnets,
                    "securityGroups": [sg] if sg else [],
                    "assignPublicIp": "DISABLED",
                }
            },
            overrides={
                **({"memory": memory} if memory else {}),
                "containerOverrides": [
                    {"name": "pipeline", "environment": environment}
                ],
            },
        )
        or {}
    )


def _launch_failed(
    book: str, task_def: str, event: str, consequence: str, reason: str, detail: str
) -> None:
    observe_error(
        "ocr_merge",
        event,
        note=f"{task_def} did not launch ({consequence}); fix the cause, then re-drive: "
        + _redrive(book),
        book=book,
        reason=reason,
    )
    logger.error("Failed to launch %s for %s: %s %s", task_def, book, reason, detail)


def _redrive(book: str) -> str:
    from src.ingestion.ocr_chunk_manifest import redrive_command

    return redrive_command(book)


def _s3():
    return boto3.client("s3", region_name=REGION)


def _book_from_event(detail: dict) -> str:
    """Extract the book/pdf name from a Batch job-state-change event, converged on
    the shared SAFE slug (P1-S4/P1-C2).

    jobName is 'chandra-{book}' (set by the trigger's _submit_ocr). Prefer the
    job's S3 input path if present, else parse the jobName. Either candidate is
    run through :func:`src.ingestion.book_slug.book_slug` (filename stem,
    spaces->_, ``[A-Za-z0-9._-]+`` or loud 'unknown') so the OCR-merge derivation
    matches the convert / video paths and a crafted jobName/path cannot inject a
    traversal or unsafe char into the S3/review keys it builds.
    """
    job_name = detail.get("jobName", "")
    # Try the container command's input path first (most reliable). Handle any
    # OCR-able input extension (PDF or an image — Chandra OCRs scanned maps/plates
    # too), not just .pdf.
    _ocr_exts = (".pdf", ".jpg", ".jpeg", ".png", ".tif", ".tiff", ".webp", ".bmp")
    cmd = (detail.get("container", {}) or {}).get("command", []) or []
    for arg in cmd:
        low = arg.lower()
        if arg.startswith("s3://") and low.endswith(_ocr_exts):
            # book_slug strips the path + extension and validates the stem.
            return book_slug(arg)
    if job_name.startswith("chandra-"):
        # Validate the jobName-derived stem the same way (no extension to strip).
        return book_slug(job_name[len("chandra-") :])
    return ""


class _ChunkPlan:
    """What to merge now: ``status`` is ``ready`` (merge ``keys``), ``waiting`` (other chunks of
    the submission still running) or ``skip`` (already claimed, or the manifest is unusable).
    """

    def __init__(self, status: str, keys=None, manifest=None):
        self.status = status
        self.keys = list(keys or [])
        self.manifest = manifest


def _chunk_plan(s3, book: str, force: bool = False) -> "_ChunkPlan":
    """The markdown keys to merge, in page order, once the submission is complete.

    With a chunk manifest (written by the trigger before submitting): ``waiting`` until EVERY
    chunk of the submission has its markdown, then only those chunks, claimed once
    (``.merged``) so two last chunks finishing together promote the book once. Without one (an
    OCR submitted before manifests existed, or by the operator CLI): every ``.../input/input.md``
    under the book, the former behaviour.
    """
    from src.ingestion import ocr_chunk_manifest as cm

    try:
        manifest = cm.read_manifest(s3, BUCKET, book)
    except ValueError as e:
        observe_error(
            "ocr_merge",
            "manifest_invalid",
            note="OCR chunk manifest unreadable — book NOT promoted; delete ocr-output/<book>/.chunks.json "
            "and re-upload the document to re-submit its OCR",
            book=book,
            reason=str(e)[:200],
        )
        return _ChunkPlan("skip")
    if manifest is None:
        logger.info(
            "OBSERV ocr_merge.no_manifest book=%s (legacy: merge what exists)", book
        )
        return _ChunkPlan("ready", _listed_markdown_keys(s3, book))
    missing = cm.missing_chunks(s3, BUCKET, book, manifest)
    if missing:
        logger.info(
            "OBSERV ocr_merge.waiting book=%s done=%d total=%d missing=%s",
            book,
            len(manifest.chunks) - len(missing),
            len(manifest.chunks),
            missing[:5],
        )
        return _ChunkPlan("waiting", manifest=manifest)
    if not cm.claim_merge(s3, BUCKET, book, manifest, force):
        _report_claimed(s3, book, manifest)
        return _ChunkPlan("skip", manifest=manifest)
    keys = [cm.chunk_markdown_key(book, c) for c in manifest.chunks]
    return _ChunkPlan("ready", keys, manifest)


def _report_claimed(s3, book: str, manifest) -> None:
    """Another merge holds the claim. Normal while it works; a warning once it has been held for
    long enough that it probably died (the next attempt after the TTL takes it over, or an
    operator re-drive with force)."""
    from src.ingestion import ocr_chunk_manifest as cm

    held, _etag = cm.read_claim(s3, BUCKET, book)
    age = cm.claim_age(held) if held else 0
    logger.info(
        "OBSERV ocr_merge.already_claimed book=%s submission=%s age_s=%d",
        book,
        manifest.submission,
        age,
    )
    if age > cm.STALE_CLAIM_WARN:
        observe_warn(
            "ocr_merge",
            "claim_stale",
            note=(
                "this book's OCR merge/cross-check was claimed over 30 min ago and has not "
                "finished: if its task died, re-drive with force (only when none is running): "
                + cm.redrive_command(book)
            ),
            book=book,
            age_s=int(age),
        )


def _listed_markdown_keys(s3, book: str) -> list:
    md_keys = []
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=BUCKET, Prefix=f"ocr-output/{book}/"):
        for obj in page.get("Contents", []):
            if obj["Key"].endswith("/input/input.md"):
                md_keys.append(obj["Key"])
    return sorted(md_keys)


def merge_ocr_output(book: str, force: bool = False) -> str:
    """Merge the book's OCR and hand it on. Returns the first key written for the next step,
    or ``""`` when nothing was handed on (waiting for other chunks, empty OCR, a failed launch).

    Waits until EVERY chunk of the OCR submission is in (``_chunk_plan``), then:

    * empty / near-empty OCR -> needs-review;
    * a structured-reference doc (``.structured``) -> the OOB parser track (phase0-ingest);
    * otherwise the raw merge is kept (``ocr-output/{book}/merged/raw.md``) and the book is
      published as its chapters (src/ingestion/ocr_promote): here when the upload has no
      validation URL, by the Phase 0 cross-check task when it has one.
    """
    s3 = _s3()
    if not BUCKET:
        logger.error("S3_BUCKET not set — cannot promote OCR output for %s", book)
        raise RuntimeError("S3_BUCKET env var not configured")
    plan = _chunk_plan(s3, book, force)
    if plan.status != "ready":
        return ""  # waiting for the submission's other chunks, or nothing to do
    try:
        return _merge_ready(s3, book, plan)
    except Exception:
        # Let the event's retry (or a re-drive) merge again: an unfinished merge must not
        # leave the claim that makes every later attempt skip.
        if plan.manifest is not None:
            from src.ingestion import ocr_chunk_manifest as cm

            cm.release_merge(s3, BUCKET, book, plan.manifest.submission)
        raise


def _merge_ready(s3, book: str, plan: "_ChunkPlan") -> str:
    from src.ingestion import ocr_promote

    prefix = f"ocr-output/{book}/"
    if not plan.keys:
        # OCR produced no markdown at all — do NOT silently drop. Off-ramp to
        # needs-review (e.g. a photo/map sent to OCR, a blank scan, or a job that
        # wrote nothing) for more extensive evaluation.
        _reject(s3, book, "OCR produced no markdown output (empty result)", "ocr-empty")
        return ""
    parts = ocr_promote.read_parts(s3, BUCKET, plan.keys, _first_pages(book, plan))
    merged = "\n\n".join(text for _, text in parts)
    # Near-empty OCR = effectively failed (blank scan, non-text image, or a
    # born-digital PDF Chandra couldn't read). Promoting it would feed garbage to
    # parse/extract. Threshold is deliberately low (whitespace-stripped) so a
    # legitimately short page still passes.
    min_chars = int(os.environ.get("OCR_MIN_USABLE_CHARS", "40"))
    if len(merged.strip()) < min_chars:
        _reject(
            s3,
            book,
            f"OCR output near-empty ({len(merged.strip())} usable chars < "
            f"{min_chars}) — likely a non-text image or unreadable scan",
            "ocr-near-empty",
        )
        return ""
    # STRUCTURED-REFERENCE fork: if the trigger tagged this doc structured (ETO
    # OOB etc.), its OCR markdown goes to the deterministic OOB parser track, NOT
    # narrative parse.
    if _is_structured(s3, book):
        return _route_to_oob(s3, book, merged)
    raw = ocr_promote.keep_raw(s3, BUCKET, book, merged)
    if plan.manifest is not None and plan.manifest.validation_url:
        return _hand_to_crosscheck(s3, book, plan.manifest, raw)
    return _publish(s3, book, plan, parts)


def _hand_to_crosscheck(s3, book: str, manifest, raw: str) -> str:
    """The Phase 0 cross-check task corrects the OCR against the URL, then publishes it. It is
    told which submission it publishes, so a re-OCR meanwhile is never published as this one;
    the claim records which task is live, so only THAT task's stop event is acted on."""
    from src.ingestion import ocr_chunk_manifest as cm

    logger.info(
        "OBSERV ocr_merge.crosscheck book=%s submission=%s validation_url=%s",
        book,
        manifest.submission,
        manifest.validation_url,
    )
    task = _launch_crosscheck(book, manifest.submission)
    if task:
        cm.record_task(s3, BUCKET, book, manifest.submission, task)
        return raw
    cm.release_merge(
        s3, BUCKET, book, manifest.submission
    )  # a re-drive can launch again
    return ""


def _publish(s3, book: str, plan: "_ChunkPlan", parts: list) -> str:
    from src.ingestion import ocr_promote

    prefix = f"ocr-output/{book}/"
    manifest = plan.manifest
    # Narrative track: normalize to English before promote-to-parse. Chandra emits
    # physical-page separators, so detect/translate PER PAGE (a scanned roll can
    # mix languages). Fail-safe: English/disabled/error -> unchanged. (The OOB
    # fork above translates in phase0_ingest, so it is intentionally skipped here.)
    from src.ingestion.translation import normalize_markdown_to_english

    keys = ocr_promote.promote(
        s3,
        BUCKET,
        book,
        parts,
        source_key=(manifest.source_key if manifest else "") or prefix,
        translate=lambda text: normalize_markdown_to_english(text, per_page=True),
    )
    logger.info(
        "OCR->parse handoff: merged %d md file(s) -> %d chapter(s) of s3://%s/contentrepository/%s/",
        len(plan.keys),
        len(keys),
        BUCKET,
        book,
    )
    return keys[0] if keys else ""


def _first_pages(book: str, plan: "_ChunkPlan") -> list:
    """The first PDF page of each part (None when its chunk directory has no page range)."""
    from src.ingestion import ocr_chunk_manifest as cm

    if plan.manifest is not None:
        return [cm.first_page(c) for c in plan.manifest.chunks]
    whole = cm.chunk_markdown_key(book, cm.WHOLE)
    prefix = f"ocr-output/{book}/"
    return [
        1 if k == whole else cm.first_page(k[len(prefix) :].split("/input/")[0])
        for k in plan.keys
    ]


def _reject(s3, book: str, reason: str, category: str) -> None:
    from src.ingestion.review_reject import reject_to_review

    reject_to_review(
        s3,
        BUCKET,
        f"ocr-output/{book}/",
        book,
        reason,
        category=category,
        region=REGION,
    )


def _stop_environment(detail: dict) -> dict:
    """{name: value} of the stopped task's container environment overrides (BOOK_NAME ...)."""
    out = {}
    for container in (detail.get("overrides") or {}).get("containerOverrides") or []:
        for item in container.get("environment") or []:
            out[item.get("name")] = item.get("value")
    return out


def _stop_summary(detail: dict) -> str:
    codes = [c.get("exitCode") for c in detail.get("containers") or []]
    reason = " ".join(str(detail.get("stoppedReason", "")).split())[:120]
    return f"exit={codes} stop_code={detail.get('stopCode')} reason={reason!r}"


def handle_crosscheck_stopped(event: dict) -> dict:
    """An ECS ``phase0-crosscheck`` task STOPPED (EventBridge rule in compute.yaml).

    Acted on only if it is the task the claim says is live (EventBridge delivers at least once,
    and an earlier task of the same submission may stop late): a clean exit, an operator stop,
    a bad-input exit or a failure the task reported itself (it released the claim) needs
    nothing. A task that died holding its claim (killed for memory, a crash, a pre-flight exit,
    a failure to start) is relaunched ONCE with more memory; if that also dies, the book is
    released and a single actionable alert names the re-drive."""
    detail = event.get("detail", {}) or {}
    env = _stop_environment(detail)
    book, submission = env.get("BOOK_NAME", ""), env.get("SUBMISSION_ID", "")
    ignore = _no_recovery_needed(book, detail)
    if ignore:
        return {"action": "none", "reason": ignore}
    s3 = _s3()
    ignore = _not_the_live_task(s3, book, submission, detail)
    if ignore:
        return {"action": "none", "reason": ignore, "book": book}
    return _recover_crosscheck(s3, book, submission, detail)


def _no_recovery_needed(book: str, detail: dict) -> str:
    """Why a stop event needs nothing whatever the claim says ("" if it may): finished, bad
    input (a retry changes nothing), stopped by an operator, or not one of ours."""
    codes = [c.get("exitCode") for c in detail.get("containers") or []]
    if not book or codes in ([0], [2]) or detail.get("stopCode") == "UserInitiated":
        return "finished, bad input, operator stop or unknown"
    return ""


def _not_the_live_task(s3, book: str, submission: str, detail: dict) -> str:
    """Why the stop event is not the live task's ("" if it is): the claim was released (the task
    handled its own failure) or belongs to another submission, or names another task (a
    duplicate delivery, or an earlier task stopping late)."""
    from src.ingestion import ocr_chunk_manifest as cm

    held, _etag = cm.read_claim(s3, BUCKET, book)
    if held is None or held.get("submission") != submission:
        return "claim released (handled) or superseded"
    live = held.get("task")  # absent only in the instant between launch and record_task
    if live and live != detail.get("taskArn"):
        return "not the live task (duplicate or late event)"
    return ""


def _recover_crosscheck(s3, book: str, submission: str, detail: dict) -> dict:
    from src.ingestion import ocr_chunk_manifest as cm

    summary = _stop_summary(detail)
    outcome = cm.note_retry(s3, BUCKET, book, submission)
    if (
        outcome == cm.LOST
    ):  # released, taken over, or another event won: nothing here is ours
        return {"action": "none", "reason": "claim moved on", "book": book}
    if outcome != cm.EXHAUSTED:
        return _relaunch(s3, book, submission, int(outcome), summary)
    observe_error(
        "ocr_merge",
        "crosscheck_died",
        note=(
            "the cross-check task died again after a retry (%s): the book is NOT published; the "
            "raw merge is kept. Find the cause in the task's log (out of memory? raise the task "
            "memory), then re-drive: " % summary
        )
        + cm.redrive_command(book),
        book=book,
    )
    cm.release_merge(s3, BUCKET, book, submission)
    return {"action": "alerted", "book": book}


def _relaunch(s3, book: str, submission: str, retry: int, summary: str) -> dict:
    from src.ingestion import ocr_chunk_manifest as cm

    logger.warning(
        "OBSERV ocr_merge.crosscheck_retry status=warn book=%s retry=%d %s — relaunching with %s MiB",
        book,
        retry,
        summary,
        CROSSCHECK_RETRY_MEMORY,
    )
    task = _launch_crosscheck(book, submission, CROSSCHECK_RETRY_MEMORY)
    if task:
        cm.record_task(s3, BUCKET, book, submission, task)
        return {"action": "retried", "book": book, "retry": retry}
    cm.release_merge(
        s3, BUCKET, book, submission
    )  # the launch alerted; a re-drive can run
    return {"action": "error", "reason": "retry launch failed", "book": book}


def handler(event, _context):
    """EventBridge: Batch job-state-change (SUCCEEDED) -> promote OCR output; ECS task stopped
    (``phase0-crosscheck``) -> recover it."""
    if event.get("source") == "aws.ecs":
        return handle_crosscheck_stopped(event)
    detail = event.get("detail", {})
    status = detail.get("status", "")
    if status != "SUCCEEDED":
        logger.info("Ignoring Batch event status=%s", status)
        return {"action": "none", "reason": f"status {status}"}

    # Only act on Chandra OCR jobs.
    job_queue = detail.get("jobQueue", "")
    if CHANDRA_QUEUE_SUFFIX not in job_queue:
        logger.info("Ignoring non-chandra job queue: %s", job_queue)
        return {"action": "none", "reason": "not chandra"}

    book = _book_from_event(detail)
    if not book:
        # #11: a paid Chandra OCR job SUCCEEDED but we cannot derive its book from the
        # event — the OCR output would dead-end silently. Emit a canonical, greppable,
        # Slack-escalating anomaly at the point of loss (not a generic-only error).
        job_name = detail.get("jobName")
        observe_error(
            "ocr_merge",
            "book_underivable",
            note="Chandra OCR SUCCEEDED but book undetectable — paid OCR output will dead-end",
            job_name=job_name,
            job_queue=job_queue,
        )
        logger.error("Could not determine book from event: %s", job_name)
        return {"action": "error", "reason": "no book"}

    out = merge_ocr_output(book, force=bool(event.get("force")))
    if not out:
        return {
            "action": "none",
            "reason": "nothing handed on (waiting for chunks, empty OCR or a failed launch)",
            "book": book,
        }
    return {"action": "promoted", "book": book, "output_key": out}

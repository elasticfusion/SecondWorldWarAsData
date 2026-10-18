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
PHASE0_TASK_DEF = os.environ.get("PHASE0_TASK_DEF", f"{ENV_NAME}-wwii-phase0-convert")

# AV scanning (demand-launched, binary-only). Gate is opt-in via AV_SCAN_ENABLED
# so behavior is unchanged until an operator turns it on + deploys the engine.
AV_SCAN_ENABLED = os.environ.get("AV_SCAN_ENABLED", "false").lower() == "true"
AV_SCAN_TASK_DEF = os.environ.get("AV_SCAN_TASK_DEF", f"{ENV_NAME}-wwii-av-scan")

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


import re as _re_title

# A content title must be a plain safe token (no path separators, no '..') since
# it becomes a filesystem path segment, a subprocess --book arg, and a DynamoDB key.
_SAFE_TITLE_RE = _re_title.compile(r"[A-Za-z0-9._-]+")


def _title_registry_get(title: str):
    """Return the registered content checksum for a title, or None if unused."""
    try:
        item = dynamo.get_item(Key={"cache_key": f"title#{title}"}).get("Item")
        return item.get("checksum") if item else None
    except Exception as e:  # noqa: BLE001 - fail-open: treat as unused on error
        logger.warning("title registry lookup failed for %s: %s", title, e)
        return None


# How long a title's "claimed" placeholder is considered part of the SAME upload
# wave (concurrent partial S3->SNS deliveries of one submission). A sibling claim
# within this window is NOT a collision — it's the same submission arriving in
# pieces. After it, the title is treated as a prior submission.
_TITLE_CLAIM_WAVE_S = 1800  # 30 min


def _title_claim(title: str):
    """Subset-INDEPENDENT title claim (the fix for concurrent partial deliveries).

    Returns one of:
      ("claimed", None)       -> THIS invocation won a fresh claim (placeholder
                                 written); it owns identity finalization.
      ("inflight", None)      -> a sibling invocation of the SAME upload wave
                                 already holds a recent placeholder -> NOT a
                                 collision; proceed (same submission in pieces).
      ("final", marker)       -> a PRIOR, finalized submission already holds this
                                 title (compare identity against `marker`).

    The claim is title-only (no content fingerprint), so it is identical no matter
    which partial file-subset an invocation sees -> concurrent invocations of one
    submission converge instead of self-colliding.
    """
    import datetime as _dt
    import time as _t

    now = int(_t.time())
    try:
        dynamo.put_item(
            Item={
                "cache_key": f"title#{title}",
                "state": "claimed",
                "claimed_at": now,
                "registered_at": _dt.datetime.now(_dt.timezone.utc).isoformat(),
            },
            ConditionExpression="attribute_not_exists(cache_key)",
        )
        return "claimed", None
    except dynamo.meta.client.exceptions.ConditionalCheckFailedException:
        item = dynamo.get_item(Key={"cache_key": f"title#{title}"}).get("Item") or {}
        state = item.get("state")
        claimed_at = int(item.get("claimed_at", 0) or 0)
        same_wave = (now - claimed_at) <= _TITLE_CLAIM_WAVE_S if claimed_at else False
        if state == "final":
            if claimed_at == 0:
                logger.warning(
                    "title %r is 'final' without claimed_at (legacy/foreign entry) "
                    "— treating as a prior submission",
                    title,
                )
            # A finalized entry from THIS wave (the claim winner finalized just
            # ahead of us) is the SAME submission — treat as in-flight, never
            # compare our partial fingerprint against it (the finalize-race that
            # caused the self-collision). Only a GENUINELY PRIOR final entry
            # (outside the wave) is a real identity comparison.
            if same_wave:
                return "inflight", None
            return "final", item.get("checksum")
        # A 'claimed' placeholder (sibling still finalizing) -> same submission.
        return "inflight", None
    except Exception as e:  # noqa: BLE001 - fail-open: proceed, don't false-collide
        logger.warning("title claim failed for %s: %s — proceeding", title, e)
        return "inflight", None


def _title_finalize(title: str, marker: str) -> None:
    """Promote a claimed title to its FINAL identity marker (checksum or 'llm:'fp)
    once the full set is known. UPDATE (not full put) so claimed_at is PRESERVED —
    a later same-wave sibling that observes 'final' must still recognize the wave
    (see _title_claim) and NOT false-collide against its own submission."""
    import datetime as _dt

    try:
        dynamo.update_item(
            Key={"cache_key": f"title#{title}"},
            UpdateExpression="SET #s = :final, checksum = :m, registered_at = :t",
            ExpressionAttributeNames={"#s": "state"},
            ExpressionAttributeValues={
                ":final": "final",
                ":m": marker,
                ":t": _dt.datetime.now(_dt.timezone.utc).isoformat(),
            },
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("title finalize failed for %s: %s", title, e)


def _title_checksum(s3, keys: list) -> str:
    """Stable sha256 over ALL of a title's content keys (single-file identity:
    exact byte identity of the one file; also used as the stored marker). Streams
    each body in 1MiB chunks (bounded memory) and closes the stream."""
    import contextlib as _cl
    import hashlib as _hl

    h = _hl.sha256()
    for key in sorted(keys):
        obj = s3.get_object(Bucket=BUCKET, Key=key)
        with _cl.closing(obj["Body"]) as body:
            while True:
                chunk = body.read(1 << 20)
                if not chunk:
                    break
                h.update(chunk)
    return "sha256:" + h.hexdigest()


def _multi_fingerprint(s3, keys: list) -> str:
    """Compact human/LLM-readable fingerprint of a multi-file submission: sorted
    filenames + sizes + a small head sample of each, for the LLM sameness judge."""
    parts = []
    for key in sorted(keys):
        try:
            obj = s3.get_object(Bucket=BUCKET, Key=key, Range="bytes=0-600")
            head = obj["Body"].read().decode("utf-8", "replace")
        except Exception:  # noqa: BLE001
            head = ""
        parts.append(f"{key.rsplit('/', 1)[-1]}:\n{head.strip()[:400]}")
    return "\n---\n".join(parts)


def _grok_api_key() -> str:
    secret_id = os.environ.get("SECRETS_ID", "")
    if not secret_id:
        return os.getenv("GROK_API_KEY", "")
    try:
        sm = boto3.client(
            "secretsmanager",
            region_name=os.environ.get("AWS_REGION", "us-east-1"),
        )
        return sm.get_secret_value(SecretId=secret_id)["SecretString"]
    except Exception as e:  # noqa: BLE001
        logger.warning("grok key fetch failed: %s", e)
        return ""


def _llm_same_work(title: str, existing_fp: str, new_fp: str) -> bool:
    """Ask Grok whether two multi-file submissions under the same title are the
    SAME work. Fail-safe: on ANY error return False (treat as DIFFERENT -> rename,
    never silently merge two different works under one title)."""
    key = _grok_api_key()
    if not key:
        logger.warning("no grok key for multi-file sameness — treating as different")
        return False
    try:
        import json as _json
        import urllib.request as _ur

        prompt = (
            "Two document submissions share the title "
            f"'{title}'. Decide if they are the SAME underlying work (same book/"
            "document, possibly re-uploaded) or DIFFERENT works that merely share a "
            "title. Answer with ONLY 'SAME' or 'DIFFERENT'.\n\n"
            f"=== EXISTING ===\n{existing_fp[:4000]}\n\n=== NEW ===\n{new_fp[:4000]}"
        )
        body = _json.dumps(
            {
                "model": os.getenv("GROK_MODEL", "grok-2-latest"),
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0,
                "max_tokens": 4,
            }
        ).encode()
        req = _ur.Request(
            "https://api.x.ai/v1/chat/completions",
            data=body,
            headers={
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
            },
        )
        with _ur.urlopen(req, timeout=30) as resp:  # nosec B310 - fixed https host
            out = _json.loads(resp.read().decode("utf-8"))
        answer = out["choices"][0]["message"]["content"].strip().upper()
        return answer.startswith("SAME")
    except Exception as e:  # noqa: BLE001 - fail-safe: different
        logger.warning("multi-file sameness LLM failed (%s) — treating as different", e)
        return False


def _resolve_title_identity(s3, title: str, tkeys: list):
    """Decide one title's identity and return (proceed, reject_reason, category).

    Uses a subset-INDEPENDENT title claim so concurrent partial deliveries of ONE
    submission converge instead of self-colliding:
      * claimed  -> we won the claim; finalize the identity marker + proceed.
      * inflight -> a sibling of the SAME upload wave holds the claim -> proceed
                    (same submission arriving in pieces; NOT a collision).
      * final    -> a PRIOR finalized submission holds this title -> compare
                    identity (single=checksum, multi=LLM) -> repeat or collision.
    """
    from src.ingestion.title_uniqueness import content_checksum  # noqa: F401

    single = len(tkeys) == 1

    state, existing_marker = _title_claim(title)

    if state in ("claimed", "inflight"):
        # Our submission (fresh claim or a same-wave sibling). Finalize the marker
        # once (only the claim winner), computed over THIS invocation's subset —
        # which is fine because identity is only ever COMPARED against a 'final'
        # entry from a DIFFERENT prior submission, never against our own wave.
        if state == "claimed":
            try:
                marker = (
                    _title_checksum(s3, tkeys)
                    if single
                    else "llm:" + _multi_fingerprint(s3, tkeys)[:4000]
                )
                _title_finalize(title, marker)
            except Exception as e:  # noqa: BLE001 - best-effort finalize
                logger.warning("finalize marker failed for %r: %s", title, e)
        return True, "", ""

    # state == "final": a prior submission owns this title. Compare identity.
    try:
        if single:
            same = _title_checksum(s3, tkeys) == existing_marker
        else:
            new_fp = _multi_fingerprint(s3, tkeys)
            same = _llm_same_work(title, (existing_marker or "")[4:], new_fp)
    except Exception as e:  # noqa: BLE001 - fail-open: proceed rather than block
        logger.warning("identity compare failed for %r (%s) — proceeding", title, e)
        return True, "", ""

    if same:
        logger.info("Duplicate submission (same work), rejecting title %r", title)
        return False, "duplicate: same title + same work already ingested", "duplicate"

    from src.ingestion.title_uniqueness import _rand_suffix

    suggested = f"{title}-{_rand_suffix()}"
    logger.warning(
        "Title collision (different work) %r -> suggest %r (method=%s)",
        title,
        suggested,
        "checksum" if single else "llm",
    )
    reason = (
        f"title collision: '{title}' already holds a different work; "
        f"re-upload under a unique title (suggested: '{suggested}')"
    )
    return False, reason, "title-collision"


def _enforce_title_uniqueness_at_launch(keys: list) -> list:
    """Title-uniqueness gate run at Phase-1 LAUNCH, over the FULL accumulated key
    set for each title (so concurrent partial S3->SNS deliveries don't cause false
    collisions). Title is the first duplicate indicator; identity method matches
    the submission SHAPE: single-file -> checksum, multi-file -> LLM.

    Returns the keys to PROCEED with (repeats/collisions dropped + rejected-to-review).
    Forward-only registry (dev: a legacy title reprocesses once on first re-upload).
    """
    from src.ingestion.review_reject import reject_to_review

    s3 = boto3.client("s3")
    by_title: dict = {}
    proceed: list = []
    for k in keys:
        title = _title_from_key(k)
        if not title:
            proceed.append(k)
            continue
        by_title.setdefault(title, []).append(k)

    for title, tkeys in by_title.items():
        try:
            ok, reason, category = _resolve_title_identity(s3, title, tkeys)
        except Exception as e:  # noqa: BLE001 - fail-open: let the title proceed
            logger.warning("identity resolve failed for %r (%s) — proceeding", title, e)
            proceed.extend(tkeys)
            continue
        if ok:
            proceed.extend(tkeys)
            continue
        for k in tkeys:
            try:
                reject_to_review(s3, BUCKET, k, title, reason, category=category)
            except Exception as e:  # noqa: BLE001
                logger.warning("reject_to_review failed for %s: %s", k, e)
    return proceed


# Content-source path scheme (see src/utils/content_source.py — mirrored here because the
# Lambda is deployed standalone and cannot import src). Primary sources carry a SOURCE id in
# the path: contentrepository/{SOURCE_ID}/{TITLE}/... . The reserved id for platform/operator
# material is "_system"; contributor uploads use user-/contrib-/org- namespaces. Legacy
# unprefixed paths (contentrepository/{TITLE}/...) are treated as system-provided.
SYSTEM_SOURCE_ID = "_system"
_SOURCE_PREFIXES = ("user-", "contrib-", "org-")


def _is_source_segment(segment: str) -> bool:
    """True if a path segment denotes a SOURCE id (reserved system id or contributor
    namespace) rather than a legacy title segment."""
    return segment == SYSTEM_SOURCE_ID or segment.lower().startswith(_SOURCE_PREFIXES)


def _source_from_key(key: str) -> str:
    """The SOURCE identifier for a content key: an explicit source segment when present,
    else the reserved system id (legacy/operator-ingested)."""
    parts = [p for p in (key or "").split("/") if p]
    if (
        len(parts) >= 2
        and parts[0] == "contentrepository"
        and _is_source_segment(parts[1])
    ):
        return parts[1]
    return SYSTEM_SOURCE_ID


def _title_from_key(key: str) -> str:
    """Derive a content item's TITLE from an S3 key, with a filename fallback.

    Not everything is a 'book', but every content item has a title. Prefer the
    content-repository folder: ``contentrepository/{TITLE}/...`` -> TITLE, OR the
    source-prefixed form ``contentrepository/{SOURCE_ID}/{TITLE}/...`` -> TITLE (the
    leading SOURCE segment is skipped). If the item is a loose file with no title folder
    (``contentrepository/Foo.md`` or any other shape), fall back to the FILENAME STEM so
    the item still gets a stable, non-empty title and never falls through to a whole-corpus
    run.
    """
    parts = [p for p in key.split("/") if p]
    if parts and parts[0] == "contentrepository":
        rest = parts[1:]
        # Skip a leading SOURCE segment so the TITLE is parsed from the right position.
        if rest and _is_source_segment(rest[0]):
            rest = rest[1:]
        if len(rest) >= 2:
            candidate = rest[0]  # titled folder
        else:
            candidate = rest[-1].rsplit(".", 1)[0] if rest else ""
    else:
        # Loose file (or no title folder) -> filename stem.
        candidate = parts[-1].rsplit(".", 1)[0] if parts else ""
    candidate = candidate.replace(" ", "_")
    # SECURITY: a title flows into a filesystem path (per-title cache rmtree), a
    # subprocess --book arg, and a DynamoDB key. Reject path-traversal / unsafe
    # chars — a title must be a plain safe token or we treat it as untitled ("").
    if candidate in ("", ".", "..") or not _SAFE_TITLE_RE.fullmatch(candidate):
        return ""
    return candidate


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


# Stale-lock reclaim grace window. The trigger no longer waits on NAT (the phase
# task validates its own readiness via nat-manager); this budget now only sizes how
# long a just-acquired lock is presumed held by an invocation still spinning up its
# task before a sibling may reclaim it as "stale" (prevents a double-launch race).
NAT_WAIT_SECONDS = int(os.environ.get("NAT_WAIT_SECONDS", "180"))


def _lock_age_seconds(lock_key: str, now: int):
    """Age (seconds) of an existing lock from its stored acquire timestamp (`response`),
    or None if it can't be read. Used to avoid reclaiming a just-acquired lock whose holder
    is still waiting on NAT (the double-launch race)."""
    try:
        item = dynamo.get_item(Key={"cache_key": lock_key}).get("Item", {})
        ts = int(item.get("response", 0))
        return now - ts if ts else None
    except Exception as e:  # noqa: BLE001
        logger.warning("lock-age read failed for %s: %s", lock_key, e)
        return None


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
    ".mp4",
    ".mkv",
    ".mov",
    ".webm",
    ".avi",
    ".m4v",
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


def _validate_parse_keys(keys: list) -> list:
    """Filter standalone text keys (.md/.txt/.html) headed to parse: a file with
    no usable content is off-ramped to needs-review instead of queuing a doomed
    parse (previously it reached Phase 1 and only produced a parse error). Keeps
    usable files. Fail-open: a fetch/read error keeps the key (let parse try) —
    never block a legit doc on a transient S3 issue.

    Note: OCR/convert/video outputs re-enter parse via their OWN upload events and
    are already content-validated at their source; this guards only direct uploads.
    """
    import re as _re

    s3 = boto3.client("s3")
    min_chars = int(os.environ.get("PARSE_MIN_USABLE_CHARS", "20"))
    kept = []
    for k in keys:
        try:
            body = (
                s3.get_object(Bucket=BUCKET, Key=k)["Body"]
                .read()
                .decode("utf-8", errors="replace")
            )
            # Strip markdown structure (headings/list/table markers, whitespace)
            # to measure actual textual content, not formatting.
            stripped = _re.sub(r"[#>*_`|\-\s]", "", body)
            if len(stripped) < min_chars:
                book = k.rsplit("/", 1)[-1].rsplit(".", 1)[0] or "unknown"
                from src.ingestion.review_reject import reject_to_review

                reject_to_review(
                    s3,
                    BUCKET,
                    k,
                    book,
                    f"standalone markdown has no usable content "
                    f"({len(stripped)} content chars < {min_chars})",
                    category="bad-markdown",
                )
                continue
            kept.append(k)
        except Exception as e:  # noqa: BLE001 - fail-open: keep key, let parse try
            logger.warning("Parse-key validation skipped for %s: %s", k, e)
            kept.append(k)
    return kept


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
            # Unrecognized extension — do NOT silently drop. Off-ramp to
            # needs-review so an unexpected upload is surfaced, not lost.
            logger.warning("Unrecognized upload type, rejecting to review: %s", k)
            try:
                from src.ingestion.review_reject import reject_to_review

                book = k.rsplit("/", 1)[-1].rsplit(".", 1)[0] or "unknown"
                reject_to_review(
                    boto3.client("s3"),
                    BUCKET,
                    k,
                    book,
                    f"unrecognized file type (not a supported content extension): {k}",
                    category="unrecognized-type",
                )
            except Exception as e:  # noqa: BLE001 - never let a reject crash intake
                logger.warning("Could not reject-to-review %s: %s", k, e)
    return out


def _split_by_media(keys: list) -> tuple:
    """Split content keys into (ocr_keys, convert_keys, video_keys, parse_keys).

    OCR (Chandra GPU, Phase 0): PDFs AND images (.jpg/.png/.tif/... incl scanned
    maps) — Chandra reads both.
    Convert (pandoc, Phase-0 convert task): .epub/.docx — binary text documents
    that must be converted to markdown before parse.
    Video (Phase-0 video task): .mp4/.mkv/... — transcribe + speaker-id -> markdown.
    Parse: already-textual content (.md/.txt/.html) goes straight to parse.
    """
    ocr_suffixes = (".pdf",) + _IMAGE_SUFFIXES
    convert_suffixes = (".epub", ".docx")
    ocr_keys = [k for k in keys if k.lower().endswith(ocr_suffixes)]
    convert_keys = [k for k in keys if k.lower().endswith(convert_suffixes)]
    video_keys = [k for k in keys if k.lower().endswith(_VIDEO_SUFFIXES)]
    parse_keys = [
        k
        for k in keys
        if not k.lower().endswith(ocr_suffixes)
        and not k.lower().endswith(convert_suffixes)
        and not k.lower().endswith(_VIDEO_SUFFIXES)
    ]
    return ocr_keys, convert_keys, video_keys, parse_keys


def _batch_client():
    """AWS Batch client (patchable in tests — avoids global boto3 patching)."""
    return boto3.client("batch")


def _alert_anomaly(kind: str, detail: str) -> None:
    """Publish an anomaly alert (no-silent-failure principle): a transition that
    did not complete as expected is itself an anomaly, even when the cause is
    unknown. Fans out to email + Slack via the phase2-complete topic."""
    logger.error("ANOMALY [%s]: %s", kind, detail)
    topic_arn = os.environ.get("NOTIFICATION_TOPIC_ARN", NOTIFY_TOPIC)
    if not topic_arn:
        return
    try:
        boto3.client("sns").publish(
            TopicArn=topic_arn,
            Subject=f"WWII Pipeline ANOMALY: {kind}",
            Message=f"Anomaly [{kind}] at intake:\n\n{detail}",
        )
    except Exception as e:  # pragma: no cover
        logger.warning("Failed to publish anomaly alert: %s", e)


def _ocr_job_in_flight(book: str) -> bool:
    """True if an OCR job for this book is in a non-terminal state on any OCR
    queue. Used to distinguish a legitimate in-flight claim from a silent drop
    (claim written but no job launched). Fails SAFE: on error, assume in flight
    (do not reclaim on uncertain state)."""
    queues = [OCR_JOB_QUEUE, f"{ENV_NAME}-wwii-chandra-gpu-ondemand"]
    try:
        batch = _batch_client()
        for q in queues:
            for status in ("SUBMITTED", "PENDING", "RUNNABLE", "STARTING", "RUNNING"):
                jobs = batch.list_jobs(jobQueue=q, jobStatus=status).get(
                    "jobSummaryList", []
                )
                if any(book in j.get("jobName", "") for j in jobs):
                    return True
        return False
    except Exception as e:
        logger.warning("OCR in-flight check failed for %s (assume yes): %s", book, e)
        return True


_OCR_CHUNK_PAGES = int(os.environ.get("OCR_CHUNK_PAGES", "50"))
_IMAGE_SUFFIXES = (".jpg", ".jpeg", ".png", ".tif", ".tiff", ".webp", ".bmp")
_VIDEO_SUFFIXES = (".mp4", ".mkv", ".mov", ".webm", ".avi", ".m4v")
PHASE0_VIDEO_TASK_DEF = os.environ.get(
    "PHASE0_VIDEO_TASK_DEF", f"{ENV_NAME}-wwii-phase0-video"
)


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


def _ocr_media_mismatch(pdf_key: str, book: str) -> bool:
    """True (and off-ramped) if the file's bytes contradict its OCR-routed
    extension. Fetches only the header (sniffing needs <=16 bytes) via a ranged
    GET. Fail-open: any error returns False (proceed to OCR) — never block a
    legitimate doc on a transient read or an unsniffable file."""
    import tempfile
    from pathlib import Path as _Path

    try:
        from src.ingestion.media_detection import detect_media_mismatch
        from src.ingestion.review_reject import reject_to_review

        s3 = boto3.client("s3")
        head = s3.get_object(Bucket=BUCKET, Key=pdf_key, Range="bytes=0-4095")[
            "Body"
        ].read()
        suffix = _Path(pdf_key).suffix or ".bin"
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=True) as tmp:
            tmp.write(head)
            tmp.flush()
            verdict = detect_media_mismatch(_Path(tmp.name))
        if verdict.is_mismatch:
            reject_to_review(
                s3,
                BUCKET,
                pdf_key,
                book,
                f"OCR intake: {verdict.reason}",
                category="ocr-media-mismatch",
            )
            return True
        return False
    except Exception as e:  # noqa: BLE001 - fail-open: proceed to OCR on any error
        logger.warning("OCR media-mismatch check skipped for %s: %s", pdf_key, e)
        return False


def _is_structured_reference(key: str) -> bool:
    """Declarative structured-reference signal (narrative vs structured tracks,
    per project steering). True when the upload is under a reserved ``_oob/`` path
    segment OR its stem is in the configured structured-reference list. Structured
    docs (e.g. ETO Order of Battle) route to the deterministic OOB parser track
    after OCR, not narrative LLM extraction."""
    low = key.lower()
    if "/_oob/" in low or low.startswith("_oob/") or "/oob/" in low:
        return True
    try:
        from src.utils.config import load_config

        stems = [
            s.lower()
            for s in load_config().get("ingestion", {}).get("structured_stems", [])
        ]
        stem = key.rsplit("/", 1)[-1].rsplit(".", 1)[0].lower()
        return stem in stems
    except Exception:  # noqa: BLE001 - config optional
        return False


def _submit_ocr(pdf_key: str) -> bool:
    """Submit Chandra GPU OCR for a raw PDF/image (Option B: -> Phase 0), with
    best-guess page-range chunking so a SPOT reclaim loses one chunk, not the whole
    doc. One atomic per-book claim (ocr#{book}) guards the WHOLE set of chunk jobs.
    Output lands at ocr-output/{book}/[chunk-{range}/] for the downstream merge."""
    try:
        pdf_name = pdf_key.split("/")[-1].rsplit(".", 1)[0]
        # Reject a misassigned file (declared extension != actual bytes) BEFORE
        # spending a GPU OCR job: a .pdf/.jpg that is really a zip/html/garbage
        # OCRs to nothing. Off-ramp to needs-review for more extensive evaluation
        # instead. Only the header is fetched (sniffing needs <=16 bytes), so this
        # is cheap even for very large PDFs. Fail-open: any sniff error -> proceed
        # to OCR (don't block a legit doc on a transient S3 read).
        if _ocr_media_mismatch(pdf_key, pdf_name):
            return False
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
            # A claim exists. Per the no-silent-failure principle, verify a job is
            # ACTUALLY in flight. A claim with NO running/queued OCR job is a silent
            # drop (B401: the claim was written but submission never happened / the
            # invocation died before submit) — it must ALERT and be reclaimed, not
            # silently skipped forever.
            if _ocr_job_in_flight(pdf_name):
                logger.info(
                    "OCR intake denied: %s already claimed (job in flight) — skipping",
                    pdf_name,
                )
                return False
            _alert_anomaly(
                "ocr-claim-without-job",
                f"OCR claim for {pdf_name} exists but NO OCR job is in flight — "
                f"a prior submission was dropped (claim written, job never launched). "
                f"Reclaiming and re-submitting.",
            )
            # Reclaim: overwrite the stale claim and proceed to submit.
            dynamo.put_item(
                Item={
                    "cache_key": claim_key,
                    "response": str(int(time.time())),
                    "ttl": int(time.time()) + 86400,
                }
            )
        s3_input = f"s3://{BUCKET}/{pdf_key}"
        # Declarative structured-reference signal: a doc uploaded under the
        # reserved _oob/ path is a structured/tabular reference (e.g. the ETO
        # Order of Battle), NOT narrative prose. It still needs Chandra OCR (the
        # tables are scanned), but its OCR markdown must go to the deterministic
        # OOB parser track, not narrative LLM extraction. Drop a sidecar marker
        # the merge handler checks (survives the OCR->merge EventBridge hop
        # reliably, unlike the original upload's S3 tags).
        if _is_structured_reference(pdf_key):
            try:
                boto3.client("s3").put_object(
                    Bucket=BUCKET,
                    Key=f"ocr-output/{pdf_name}/.structured",
                    Body=b"oob",
                )
                logger.info("Tagged %s as structured-reference (OOB track)", pdf_name)
            except Exception as e:  # noqa: BLE001 - marker is best-effort
                logger.warning(
                    "Could not write .structured marker for %s: %s", pdf_name, e
                )
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
    PHASE0_TASK_DEF: f"{ENV_NAME}-wwii-phase0-convert",
    PHASE0_VIDEO_TASK_DEF: f"{ENV_NAME}-wwii-phase0-video",
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
        # Optional: authorize a known large-volume run by declaring the exact expected count.
        # Threaded as EXPECTED_COUNT env -> ecs_entrypoint adds --expected-count to Phase 2 so the
        # unoverridable run-scope guard is satisfied by an explicit matching declaration.
        extra_env = None
        exp = event.get("expected_count")
        if isinstance(exp, int) and not isinstance(exp, bool) and exp > 0:
            extra_env = [{"name": "EXPECTED_COUNT", "value": str(exp)}]
        _run_task(task_def, "manual", book_name=book, extra_env=extra_env)
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
            ocr_keys, convert_keys, video_keys, parse_keys = _split_by_media(content)
            # AV gate (demand-launched, binary-only): scan pdf/image/video/office
            # BEFORE any parser (fitz/ffmpeg/torch/pandoc) touches the bytes.
            # Infected/unscannable -> quarantine + alert + freeze-submitter hook
            # (fail-closed); only clean binaries proceed. Text/md skip AV entirely
            # (never launches the scan container). See AV_SCANNING_DESIGN.md.
            ocr_keys, convert_keys, video_keys = _av_gate(
                ocr_keys, convert_keys, video_keys
            )
            # Raw PDFs/images -> Chandra OCR (Phase 0). OCR output later
            # re-triggers the parse path via its own upload.
            if ocr_keys:
                # GPU Batch instances launch into the private GPU subnets whose
                # 0.0.0.0/0 route points at the dynamic NAT — they need egress to
                # register with ECS + pull the Chandra image + read/write S3.
                # Without NAT up, instances boot but never join the cluster and
                # jobs sit RUNNABLE forever. Ensure NAT is up at OCR submit.
                _ensure_nat_for_ocr()
            for k in ocr_keys:
                _submit_ocr(k)
            # EPUB/DOCX -> Phase-0 convert task (pandoc). It writes the chapter
            # structure, whose upload re-triggers parse (like OCR output).
            for k in convert_keys:
                _submit_convert(k)
            # Video -> Phase-0 video task (transcribe + speaker-id) -> chapter
            # structure -> parse. Demand-launched; never silently dropped.
            for k in video_keys:
                _submit_video(k)
            if not parse_keys:
                continue
            parse_keys = _validate_parse_keys(parse_keys)
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
            # NOTE (fail-closed strand, dev-acceptable): this launches enrichment for
            # ALL dedup-passed books (no single book_name), so there is no per-book
            # pending#enrich# queue to seed for a retry. If _run_task fails closed here
            # (NAT not routable at dedup-approval time — rare, NAT is usually warm by
            # now), the lock is released but reconcile's per-book phase3 branch won't
            # auto-retry; the operator simply re-triggers "dedup complete" in the UI.
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


def _av_gate(ocr_keys, convert_keys, video_keys):
    """Scan binary keys before any parser touches them; return the clean subsets.

    Demand-launched + binary-only: all three track lists are binaries, so when AV
    is enabled and any are present, launch the ClamAV scan task over them.
    Infected/unscannable keys are quarantined + alerted + freeze-hooked inside the
    gate (fail-closed); only clean keys are returned to proceed. When AV is
    disabled, the lists pass through unchanged. Fail-safe: on any gate error we
    fail closed by dropping the binaries from this batch (they stay in S3 for a
    retry) rather than advancing unscanned bytes.
    """
    binaries = list(ocr_keys) + list(convert_keys) + list(video_keys)
    if not AV_SCAN_ENABLED or not binaries:
        return ocr_keys, convert_keys, video_keys
    try:
        from src.ingestion.av_scan import scan_and_gate

        outcome = scan_and_gate(
            s3,
            BUCKET,
            binaries,
            EcsClamavScanner(),
            region=os.getenv("AWS_DEFAULT_REGION", "us-east-1"),
        )
        clean = set(outcome.clean_keys)
        if outcome.quarantined_keys:
            logger.warning(
                "AV gate quarantined %d of %d binaries",
                len(outcome.quarantined_keys),
                len(binaries),
            )
        return (
            [k for k in ocr_keys if k in clean],
            [k for k in convert_keys if k in clean],
            [k for k in video_keys if k in clean],
        )
    except Exception as e:  # noqa: BLE001 - fail closed: do not advance unscanned
        logger.error("AV gate failed (failing closed, binaries held): %s", e)
        return [], [], []


class EcsClamavScanner:
    """Scanner that demand-launches the ClamAV Fargate task over the binary keys
    and reads back the verdict JSON it writes to S3. Never raises — returns
    Verdict.ERROR per key on any failure so the gate fails closed.

    The task (cloudformation AvScanTaskDef) syncs ClamAV signatures from S3,
    clamscan's each key, writes av-scan/results/{run}.json = {key: {verdict,
    signature}}, and exits. Fargate handles any size (incl. 2.4 GB video) with no
    Lambda /tmp cliff.
    """

    def scan(self, bucket: str, keys: list[str]):
        from src.ingestion.av_scan import ScanResult, Verdict

        run_id = f"{int(time.time())}-{abs(hash(tuple(keys))) % 100000}"
        result_key = f"av-scan/results/{run_id}.json"
        try:
            _run_task(
                AV_SCAN_TASK_DEF,
                "av-scan",
                extra_env=[
                    {"name": "AV_KEYS", "value": json.dumps(keys)},
                    {"name": "AV_RESULT_KEY", "value": result_key},
                ],
            )
            verdicts = _poll_av_result(bucket, result_key)
        except Exception as e:  # noqa: BLE001 - fail closed
            logger.error("ClamAV scan launch/poll failed: %s", e)
            return [ScanResult(k, Verdict.ERROR, detail=str(e)[:120]) for k in keys]
        out = []
        for k in keys:
            v = verdicts.get(k, {})
            verdict = {
                "clean": Verdict.CLEAN,
                "infected": Verdict.INFECTED,
            }.get(str(v.get("verdict", "")), Verdict.ERROR)
            out.append(ScanResult(k, verdict, signature=str(v.get("signature", ""))))
        return out


def _poll_av_result(
    bucket: str, result_key: str, *, attempts: int = 40, delay: int = 15
):
    """Poll S3 for the ClamAV task's verdict JSON. Raises on timeout -> fail closed."""
    for _ in range(attempts):
        try:
            obj = s3.get_object(Bucket=bucket, Key=result_key)
            return json.loads(obj["Body"].read())
        except Exception:  # noqa: BLE001 - not ready yet
            time.sleep(delay)
    raise TimeoutError(f"AV result {result_key} not ready after polling")


def _submit_video(key: str) -> None:
    """Launch a demand-only Phase-0 VIDEO task for one video key (VIDEO_KEY
    override). Transcribes + speaker-ids -> chapter structure -> parse fires.
    Never a standing service — launched per upload, like _submit_ocr/_submit_convert."""
    book = key.rsplit("/", 1)[-1].rsplit(".", 1)[0].replace(" ", "_")
    _run_task(
        PHASE0_VIDEO_TASK_DEF,
        "content-uploaded-video",
        book_name=book,
        extra_env=[{"name": "VIDEO_KEY", "value": key}],
    )


def _submit_convert(key: str) -> None:
    """Launch a Phase-0 convert task for one epub/docx key (CONVERT_KEY override).
    The task writes the chapter structure -> parse fires on that upload."""
    book = key.rsplit("/", 1)[-1].rsplit(".", 1)[0].replace(" ", "_")
    _run_task(
        PHASE0_TASK_DEF,
        "content-uploaded-convert",
        book_name=book,
        extra_env=[{"name": "CONVERT_KEY", "value": key}],
    )


def _run_task(task_def, source, book_name="", extra_env=None):
    """Create networking, acquire lock, launch ECS task.

    extra_env: optional list of {"name","value"} container env overrides (e.g.
    CONVERT_KEY for the Phase-0 convert task)."""
    # Defense-in-depth: book_name becomes the BOOK_NAME env -> an S3 prefix, a
    # DynamoDB key, a --book arg, and a per-title cache rmtree target. The S3-event
    # path already validates via _title_from_key, but the manual-trigger path passes
    # a raw "book" field straight through. Reject anything outside the safe charset
    # (blocks "", "..", "/", traversal) BEFORE it reaches any of those sinks.
    if book_name and (
        book_name in (".", "..") or not _SAFE_TITLE_RE.fullmatch(book_name)
    ):
        logger.error(
            "Rejecting unsafe book_name %r for %s (not launching)", book_name, source
        )
        return
    # Cancel any pending delayed teardown
    _cancel_delayed_teardown()

    # Atomic lock FIRST — BEFORE waiting for networking. The old order (NAT wait, then
    # lock) opened a ~180s window where two concurrent invocations were both alive but
    # neither held the lock; the second's "stale lock" check saw no RUNNING task (the
    # first was still in NAT-wait, not yet launched) and cleared the lock -> DOUBLE LAUNCH.
    # Locking first makes the lock the single serialization point; NAT-wait happens inside it.
    # Per-book under multi-doc (G1); singleton in serial mode (unchanged).
    family = TASK_FAMILIES.get(task_def, "unknown")
    lock_key = _lock_key(family, book_name)
    now = int(time.time())
    # A lock younger than this is presumed held by an invocation still in NAT-wait, so it
    # must NOT be reclaimed as "stale" even if no task is RUNNING yet (that was the race).
    LAUNCH_GRACE_S = NAT_WAIT_SECONDS + 120
    try:
        dynamo.put_item(
            Item={
                "cache_key": lock_key,
                "book": book_name or "all",
                "response": str(now),
                "ttl": now + 7200,
            },
            ConditionExpression="attribute_not_exists(cache_key)",
        )
    except dynamo.meta.client.exceptions.ConditionalCheckFailedException:
        # Lock exists. Only reclaim it if BOTH (a) no live task AND (b) the lock is OLDER
        # than the launch grace window (so we never clear a lock whose holder is mid-NAT-wait
        # and hasn't launched its task yet — the G1/double-launch race).
        live = ecs.list_tasks(
            cluster=CLUSTER, family=family, desiredStatus="RUNNING"
        ).get("taskArns", [])
        lock_age = _lock_age_seconds(lock_key, now)
        if not live and lock_age is not None and lock_age > LAUNCH_GRACE_S:
            logger.info(
                "Stale lock for %s (no live task, age %ds > %ds), clearing",
                lock_key,
                lock_age,
                LAUNCH_GRACE_S,
            )
            dynamo.delete_item(Key={"cache_key": lock_key})
            try:
                dynamo.put_item(
                    Item={
                        "cache_key": lock_key,
                        "book": book_name or "all",
                        "response": str(now),
                        "ttl": now + 7200,
                    },
                    ConditionExpression="attribute_not_exists(cache_key)",
                )
            except dynamo.meta.client.exceptions.ConditionalCheckFailedException:
                logger.info(
                    "Another invocation claimed lock for %s, skipping", lock_key
                )
                return
        else:
            logger.info(
                "Task %s already locked (live=%s, age=%ss), skipping",
                lock_key,
                bool(live),
                lock_age,
            )
            # Queue per-book requests for later processing
            if book_name:
                if task_def == PHASE3_TASK_DEF:
                    _queue_pending_enrich(book_name)
                elif task_def == PHASE2_TASK_DEF:
                    _queue_pending_parsed_book(book_name)
            return

    # Lock is HELD. Kick off an ASYNC NAT warm-up (fire-and-forget) and launch
    # immediately — do NOT block the launch on NAT readiness. Phase 1 needs no
    # internet (reads/writes S3 via the gateway endpoint) and is fast; firing the
    # warm-up here means NAT provisions IN PARALLEL with the parse and is ready by
    # the time Phase 2 (which immediately follows) validates readiness. Phase 2/3
    # themselves INVOKE the nat-manager and WAIT for readiness before any egress
    # (ecs_entrypoint._ensure_nat_if_needed), so NAT is never on this launch's
    # critical path. This removes the old synchronous wait + fail-closed livelock
    # (NAT route activating ~1s after the 240s Lambda timeout stranded every run).
    # Task STARTUP (ECR/secrets/logs) rides the VPC interface endpoints, not NAT.
    try:
        boto3.client("lambda").invoke(
            FunctionName=NAT_MANAGER_FN,
            InvocationType="Event",  # async — do not wait
            Payload=json.dumps({"action": "create"}).encode(),
        )
        logger.info("Fired async NAT warm-up for %s (non-blocking)", family)
    except Exception as e:
        # A failed warm-up is non-fatal here: the phase will invoke nat-manager
        # itself before egress. Log and proceed (do NOT fail the launch).
        logger.warning("Async NAT warm-up invoke failed (phase will retry): %s", e)

    # Launch task
    logger.info(
        "Launching ECS task %s from %s (book=%s)", family, source, book_name or "all"
    )
    overrides = {}
    env_over = []
    if book_name:
        env_over.append({"name": "BOOK_NAME", "value": book_name})
    if extra_env:
        env_over.extend(extra_env)
    if env_over:
        overrides = {
            "containerOverrides": [
                {
                    "name": "pipeline",
                    "environment": env_over,
                }
            ]
        }
    try:
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
    except Exception as e:
        # Launch failed AFTER we took the lock — release it so the content isn't stuck
        # behind a held lock until the 2h TTL (that would need manual intervention).
        logger.error("run_task failed for %s (%s) — releasing lock", lock_key, e)
        try:
            dynamo.delete_item(Key={"cache_key": lock_key})
        except Exception:  # noqa: BLE001
            pass
        raise
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
        # ECS list_tasks desiredStatus accepts ONLY RUNNING/PENDING/STOPPED —
        # 'PROVISIONING' is a lastStatus, not a desiredStatus, and passing it raises
        # InvalidParameterException (which previously crashed the whole launch, leaving
        # content queued but Phase 1 never started). desiredStatus=RUNNING already
        # surfaces tasks whose lastStatus is PROVISIONING/PENDING/RUNNING; we add PENDING
        # for completeness. A starting task thus still counts as busy (G1 race guard).
        for status in ("RUNNING", "PENDING"):
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
        if keys:
            # Title-uniqueness gate over the FULL accumulated set (not per-
            # invocation): single-file -> checksum, multi-file -> LLM. Drops
            # repeats/collisions. Only runs when there ARE pending keys; an empty
            # pending set keeps the prior launch behavior (manifest-driven).
            proceed = _enforce_title_uniqueness_at_launch(keys)
            if set(proceed) != set(keys):
                # CONDITIONAL rewrite (optimistic lock on the exact list we read):
                # if a concurrent _queue_pending appended a key between our read and
                # here, the condition fails and we SKIP the rewrite rather than
                # clobber that append — the dropped keys will simply be re-gated on
                # the next launch cycle. Never a blind full-list overwrite.
                # (Relies on pending#content being APPEND-ONLY/ordered via
                # list_append, so "#k = :read" reliably detects a concurrent change.)
                try:
                    dynamo.put_item(
                        Item={"cache_key": "pending#content", "keys": proceed},
                        ConditionExpression="#k = :read",
                        ExpressionAttributeNames={"#k": "keys"},
                        ExpressionAttributeValues={":read": keys},
                    )
                    logger.info(
                        "Uniqueness gate: %d of %d content keys proceed",
                        len(proceed),
                        len(keys),
                    )
                except dynamo.meta.client.exceptions.ConditionalCheckFailedException:
                    logger.info(
                        "pending#content changed concurrently — skipping rewrite "
                        "(dropped keys will be re-gated next cycle)"
                    )
            if not proceed:
                logger.info(
                    "All pending content were duplicates/collisions — no launch"
                )
                return
            keys = proceed
        books = {_title_from_key(k) for k in keys if k}
        books.discard("")
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
    """Launch Phase 2 only if no pipeline tasks are running — and ONLY scoped to a
    specific book. An unscoped (book_name="") launch makes the ECS task run Phase 2
    over the WHOLE corpus (no BOOK_NAME), which trips the fail-loud scope guard and
    aborts; it also races _queue_parsed (the pending#parsed#<book> item may not be
    written yet when a chapter-parsed event fires). So if no book can be resolved we
    SKIP — the reconcile backstop / next phase-complete event re-drives Phase 2 once
    the pending queue is consistent."""
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
    if not book_name:
        # No resolvable book: do NOT launch unscoped (would scan the whole corpus and
        # trip the scope guard). Reconcile will drive Phase 2 when the queue is ready.
        logger.info(
            "No pending book resolved for Phase 2 — skipping launch "
            "(reconcile will re-drive once pending#parsed#<book> is written)"
        )
        return
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

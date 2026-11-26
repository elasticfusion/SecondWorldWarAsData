"""Human-review + promotion bridge for TEXTUAL fetch-provenance finds.

Principle (owner): a positive textual find (e.g. a Wayback snapshot) is enqueued for human
review; **on affirm** it flows through the SAME path as a deliberately-uploaded book: the affirmed
batch (``submit_affirmed_batch``) uploads the page kept when the find was judged to the
``contentrepository/`` front door (Phase 0), rather than being written directly as facts by an
enrichment side-channel.

This closes the gap identified in docs/archive/2026-11-16/FETCH_PROVENANCE_DESIGN.md: ``source_retrieval``
already quarantines internet content for review, but nothing promoted an AFFIRMED item into
ingestion. ``affirm_fetch_find`` records the decision and ``submit_affirmed_batch`` is the bridge.
Reuses: fetch_provenance (the review_status) and find_pages (the kept page).

All operations are fail-safe and never process content automatically — promotion only stages a
source for the normal pipeline; extraction remains the pipeline's job.
"""

from __future__ import annotations

import contextlib
import logging
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

logger = logging.getLogger(__name__)

from src.utils.observe import observe_warn  # noqa: E402


def _resolve_table(fp: Any) -> Any:
    """DynamoDB cache-table handle for the current runtime, or None (local/test).

    In the review Lambda there is NO config.aws.enabled flag (that is only flipped on inside ECS by
    ecs_entrypoint), so fp._table() would return None and the UI would see an empty list. When we
    are genuinely in Lambda (AWS reserved env AWS_LAMBDA_FUNCTION_NAME is set) resolve the table
    directly from CACHE_TABLE, mirroring dedup_ui_handler. Otherwise defer to the config-driven
    fp._table() (ECS/CLI/tests), which correctly returns None in local/test mode. Fail-safe -> None.
    """
    import os

    in_lambda = bool(os.environ.get("AWS_LAMBDA_FUNCTION_NAME"))
    table_name = os.environ.get("CACHE_TABLE", "").strip()
    if in_lambda and table_name:
        try:
            import boto3

            region = os.environ.get("AWS_REGION", "us-east-1")
            return boto3.resource("dynamodb", region_name=region).Table(table_name)
        except Exception as e:  # noqa: BLE001
            logger.debug("fetch_review CACHE_TABLE resolve failed: %s", e)
            return None
    try:
        return fp._table()  # noqa: SLF001 - config-driven (ECS/CLI/test) resolution
    except Exception:  # noqa: BLE001
        return None


def _load_events() -> List[Dict[str, Any]]:
    """Load TEXTUAL fetch-provenance events. In AWS mode (a DynamoDB cache table is configured —
    e.g. inside the review Lambda) SCAN the store for ``fetchevent#`` items; otherwise read the
    local JSONL reference surface. Returns [] on any error (fail-safe)."""
    events, _errors = _load_events_counted()
    return events


def load_events() -> tuple[List[Dict[str, Any]], int]:
    """Every stored find event (any review status) and the number of load failures. A caller that
    needs the whole picture must check the failure count: a scan that failed looks like no events.
    """
    return _load_events_counted()


def _load_events_counted() -> tuple[List[Dict[str, Any]], int]:
    """Load events AND the count of load/scan failures (corrupt records/files). Fail-safe.

    The error count lets callers distinguish a GENUINELY empty queue from one that is empty only
    because loads failed — a silent empty queue reads as 'nothing to review' and hides data loss.
    """
    from src.utils import fetch_provenance as fp

    table = _resolve_table(fp)
    if table is not None:
        return _load_events_dynamo(table, fp)
    return _load_events_local(fp)


def _load_events_dynamo(table: Any, fp: Any) -> tuple[List[Dict[str, Any]], int]:
    """Scan the DynamoDB cache table for fetch-provenance TEXTUAL events (paginated). Fail-safe.
    Returns (events, error_count) — error_count tracks per-item parse failures + a failed scan.
    """
    import json

    from boto3.dynamodb.conditions import Attr

    out: List[Dict[str, Any]] = []
    errors = 0
    try:
        kwargs: Dict[str, Any] = {
            "FilterExpression": Attr("cache_key").begins_with(
                fp._KEY_PREFIX
            ),  # noqa: SLF001
        }
        while True:
            resp = table.scan(**kwargs)
            for item in resp.get("Items", []):
                try:
                    rec = json.loads(item.get("response", "{}"))
                except Exception:  # noqa: BLE001
                    errors += 1
                    continue
                if isinstance(rec, dict) and rec.get("kind") == fp.TEXTUAL:
                    out.append(rec)
            lek = resp.get("LastEvaluatedKey")
            if not lek:
                break
            kwargs["ExclusiveStartKey"] = lek
    except Exception as e:  # noqa: BLE001
        errors += 1
        logger.debug("fetch_review dynamo scan skipped: %s", e)
    return out, errors


def _load_events_local(fp: Any) -> tuple[List[Dict[str, Any]], int]:
    """Load local textual fetch-provenance events (JSONL reference surface). Fail-safe.
    Returns (events, error_count) — error_count tracks per-line parse failures + file read errors.
    """
    import json

    out: List[Dict[str, Any]] = []
    errors = 0
    try:
        d: Path = fp._LOCAL_DIR  # noqa: SLF001 - reuse the configured local dir
        if not d.exists():
            return out, errors
        for jf in d.glob("*.jsonl"):
            try:
                lines = jf.read_text(encoding="utf-8").splitlines()
            except OSError as e:
                errors += 1
                logger.debug("fetch_review read skipped %s: %s", jf.name, e)
                continue
            for line in lines:
                if not line.strip():
                    continue
                try:
                    rec = json.loads(line)
                except Exception:  # noqa: BLE001
                    errors += 1
                    continue
                if rec.get("kind") == fp.TEXTUAL:
                    out.append(rec)
    except Exception as e:  # noqa: BLE001
        errors += 1
        logger.debug("fetch_review load skipped: %s", e)
    return out, errors


def list_pending() -> List[Dict[str, Any]]:
    """Textual finds awaiting human review (review_status == pending).

    Fail-closed is preserved (returns [] on total failure), but an EMPTY queue caused by load/scan
    errors no longer masquerades as 'nothing to review': when the result is empty AND errors>0 we
    emit a LOUD, greppable OBSERV fetch_review.load_errors so the data-loss is visible. Fail-safe.
    """
    from src.utils import fetch_provenance as fp

    events, errors = _load_events_counted()
    pending = [e for e in events if e.get("review_status") == fp.PENDING]
    if not pending and errors > 0:
        observe_warn("fetch_review", "load_errors", errors=errors)
    return pending


def list_affirmed() -> List[Dict[str, Any]]:
    """Affirmed finds NOT yet submitted to Phase 1 (review_status == affirmed). These accumulate as
    the reviewer approves individual URLs; submit_affirmed_batch() hands them to Phase 1 together.
    """
    from src.utils import fetch_provenance as fp

    return [e for e in _load_events() if e.get("review_status") == fp.AFFIRMED]


def set_review_status(fetch_event_id: str, status: str) -> bool:
    """Persist a reviewer's decision (affirmed/rejected) onto a stored fetch-provenance event.
    DynamoDB in AWS mode (overwrite the item), else rewrite the local JSONL line. Fail-safe.
    """
    import json

    from src.utils import fetch_provenance as fp

    try:
        table = _resolve_table(fp)  # env-aware (Lambda CACHE_TABLE) or config-driven
        if table is not None:
            key = f"{fp._KEY_PREFIX}{fetch_event_id}"  # noqa: SLF001
            item = table.get_item(Key={"cache_key": key}).get("Item")
            if not item:
                return False
            rec = json.loads(item.get("response", "{}"))
            rec["review_status"] = status
            table.put_item(Item={"cache_key": key, "response": json.dumps(rec)})
            return True
        # Local JSONL: rewrite the matching line in place.
        d: Path = fp._LOCAL_DIR  # noqa: SLF001
        if not d.exists():
            return False
        updated = False
        for jf in d.glob("*.jsonl"):
            lines = jf.read_text(encoding="utf-8").splitlines()
            out = []
            changed = False
            for line in lines:
                if not line.strip():
                    continue
                rec = json.loads(line)
                if rec.get("fetch_event_id") == fetch_event_id:
                    rec["review_status"] = status
                    changed = True
                out.append(json.dumps(rec))
            if changed:
                jf.write_text("\n".join(out) + "\n", encoding="utf-8")
                updated = True
        return updated
    except Exception as e:  # noqa: BLE001 - a review action must never crash
        logger.debug("set_review_status skipped for %s: %s", fetch_event_id, e)
        return False


def reject_fetch_find(fetch_event: Dict[str, Any]) -> bool:
    """Mark a textual find REJECTED (reviewer declined it as a source). Returns True if persisted."""
    from src.utils import fetch_provenance as fp

    ok = set_review_status(fetch_event.get("fetch_event_id", ""), fp.REJECTED)
    if ok:
        logger.info(
            "OBSERV fetch_find.rejected provider=%s fetch_event_id=%s",
            fetch_event.get("provider"),
            fetch_event.get("fetch_event_id"),
        )
    return ok


# Exclusion scopes returned by decide_exclude_scope / exclude_fetch_find.
SCOPE_SITE = (
    "site"  # exclude the ENTIRE domain from search + re-search (registry denylist)
)
SCOPE_URL = (
    "url"  # exclude only THIS url (won't-do / REJECTED), the conservative default
)


def decide_exclude_scope(fetch_event: Dict[str, Any], grok_client: Any = None) -> str:
    """Ask Grok whether the ENTIRE site should be excluded, or only this URL.

    Returns SCOPE_SITE or SCOPE_URL. FAIL-CLOSED to SCOPE_URL (the narrow, reversible choice) when
    there is no grok_client or on any Grok error — we never widen to a whole-site ban without a
    positive, confident signal."""
    if grok_client is None:
        return SCOPE_URL
    url = fetch_event.get("endpoint", "")
    summary = fetch_event.get("result_summary", "")
    try:
        from src.utils.grok_choice import ask_choice, can_reask, evictor

        prompt = (
            "A human reviewer is excluding a search result as not useful for a WWII history "
            "corpus. Should the ENTIRE website (domain) be excluded from all future searches, "
            "or ONLY this single URL?\n"
            "Answer SITE only if the whole domain is categorically low-value or off-topic for "
            "WWII primary/secondary sources (e.g. spam, shopping, unrelated wiki mirrors). "
            "Answer URL if only this particular page is unsuitable but the domain may hold "
            "other useful material.\n"
            f"URL: {url[:300]}\n"
            f"Result summary: {summary[:300]}\n"
            'Return ONLY "SITE" or "URL".'
        )
        system = "You decide the scope of a search-source exclusion."
        choice = ask_choice(
            lambda: grok_client.chat_completion(
                prompt=prompt,
                system_prompt=system,
                temperature=0.0,
                use_cache=True,
                cache_type="exclude_scope",
            ),
            ("SITE", "URL"),
            evictor(grok_client, prompt, system, "exclude_scope", 0.0),
            what="exclude_scope",
            reask=can_reask(grok_client),
        )
        # unusable twice -> the narrow, reversible URL scope (fail closed)
        scope = SCOPE_SITE if choice == "SITE" else SCOPE_URL
        logger.info(
            "OBSERV fetch_find.exclude_scope decided=%s source=grok url=%s",
            scope,
            url[:120],
        )
        return scope
    except Exception as e:  # noqa: BLE001 - fail closed to the narrow URL-only scope
        logger.warning(
            "OBSERV fetch_find.exclude_scope decided=url source=failclosed reason=%s",
            str(e)[:80],
        )
        return SCOPE_URL


def exclude_fetch_find(
    fetch_event: Dict[str, Any], grok_client: Any = None
) -> Dict[str, Any]:
    """Reviewer EXCLUDE action. Ask Grok the scope, then persist at that scope:
      * SCOPE_SITE -> url_registry.exclude_domain (the whole domain is skipped on search/re-search,
        cross-run) AND mark this find REJECTED (won't-do).
      * SCOPE_URL  -> reject_fetch_find only (this URL is won't-do; the domain stays searchable).
    Returns {scope, ok, host}. Fail-safe: never raises."""
    scope = decide_exclude_scope(fetch_event, grok_client)
    host = ""
    ok = True
    if scope == SCOPE_SITE:
        try:
            from src.utils.url_registry import _host_of, exclude_domain

            url = fetch_event.get("endpoint", "")
            host = _host_of(url)
            ok = exclude_domain(
                url,
                reason="reviewer excluded whole site (Grok scope=SITE)",
                source=f"urlreview:{fetch_event.get('provider', '')}",
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("exclude_fetch_find site persist failed: %s", e)
            ok = False
    # In BOTH scopes, this specific find is done (won't resurface for review).
    rejected = reject_fetch_find(fetch_event)
    logger.info(
        "OBSERV fetch_find.excluded scope=%s host=%s provider=%s fetch_event_id=%s ok=%s",
        scope,
        host,
        fetch_event.get("provider"),
        fetch_event.get("fetch_event_id"),
        ok and rejected,
    )
    return {"scope": scope, "ok": bool(ok and rejected), "host": host}


def find_source_id(fetch_event_id: str) -> str:
    """The Phase 0 source id (and slug) an affirmed find is submitted under: ``fetchfind-<id>``."""
    return f"fetchfind-{fetch_event_id}"


def affirm_fetch_find(fetch_event: Dict[str, Any]) -> Optional[str]:
    """Record a reviewer's AFFIRM on a textual find. Returns the source id it will be submitted
    under (``find_source_id``), or None when the decision could not be stored (the find stays
    pending, so the reviewer is told and may retry).

    No content is read here: ``submit_affirmed_batch`` later submits the page kept when the find
    was judged (``find_pages``, checked against its checksum), so nothing on the queue item -
    no path, no URL - chooses what is ingested. Fail-safe.
    """
    from src.utils import fetch_provenance as _fp
    from src.utils.find_queue import clean_for_log

    fid = fetch_event.get("fetch_event_id") or ""
    if fid and set_review_status(fid, _fp.AFFIRMED):
        logger.info(
            "OBSERV fetch_find.affirmed provider=%s fetch_event_id=%s -> source_id=%s "
            "(submitted to Phase 0 by the next affirmed batch)",
            clean_for_log(fetch_event.get("provider"), 40),
            clean_for_log(fid, 40),
            find_source_id(fid),
        )
        return find_source_id(fid)
    observe_warn(
        "fetch_find",
        "affirm_not_stored",
        fetch_event_id=clean_for_log(fid, 40),
        note="the reviewer's affirm could not be stored, so the find is still pending and will "
        "not be submitted. Retry it in the URL-review UI; if it fails again check the cache "
        "table (DynamoDB) access of the URL-review Lambda and its logs",
    )
    return None


def _safe_token(value: str, fallback: str) -> str:
    """Reduce a string to a safe path/key token ([A-Za-z0-9._-]+); fallback if nothing survives."""
    import re

    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", (value or "").strip()).strip("-.")
    return cleaned[:80] or fallback


def _find_title(fetch_event: Dict[str, Any]) -> str:
    """A safe TITLE segment for the content key, derived from the find's URL host + id."""
    from urllib.parse import urlparse

    host = (urlparse(fetch_event.get("endpoint", "")).hostname or "source").lower()
    fid = fetch_event.get("fetch_event_id", "") or "find"
    return _safe_token(f"{host}-{fid}", "fetchfind")


def _find_content_bytes(fetch_event: Dict[str, Any]) -> bytes:
    """The bytes to submit for a find: the page it was judged on (kept by ``find_pages``, checked
    against its checksum), else a provenance HTML stub (the source URL + metadata) so Phase 1 can
    ingest it like any uploaded source. Raises
    ``find_pages.PageUnusable`` when the find names a kept page that is missing or altered: it is
    not submitted (the stub would stand in for a page a person approved)."""
    import html as _html

    from src.utils import find_pages
    from src.utils.url_hygiene import scrub

    page = find_pages.load(fetch_event.get("request_params") or {})
    if page is not None:
        return page

    # No local file is read here: no writer puts a quarantine path on a fetch event, and a path
    # taken from a queue item would let a tampered item publish any file the Lambda can read.
    params = fetch_event.get("request_params") or {}
    log = logger.warning if params.get("page_kept") is False else logger.info
    log(
        "OBSERV fetch_review.submitted_stub fetch_event_id=%s provider=%s - the find has no kept "
        "page, so Phase 0 gets only its URL and summary. To ingest the page itself, upload it to "
        "contentrepository/ by hand",
        scrub(str(fetch_event.get("fetch_event_id", "")))[:40],
        scrub(str(fetch_event.get("provider", "")))[:40],
    )
    url = _html.escape(fetch_event.get("endpoint", ""))
    provider = _html.escape(str(fetch_event.get("provider", "")))
    summary = _html.escape(str(fetch_event.get("result_summary", "")))
    stub = (
        "<!doctype html><html><head><meta charset='utf-8'>"
        f"<title>Affirmed source: {url}</title></head><body>"
        f"<h1>Affirmed primary-source find</h1>"
        f"<p>Source URL: <a href='{url}'>{url}</a></p>"
        f"<p>Provider: {provider}</p><p>Provenance: {summary}</p>"
        "</body></html>"
    )
    return stub.encode("utf-8")


def _s3_client_and_bucket():
    """(boto3 s3 client, bucket) from S3_BUCKET env, or (None, '') if unavailable. Fail-safe."""
    import os

    bucket = os.environ.get("S3_BUCKET", "").strip()
    if not bucket:
        return None, ""
    try:
        import boto3

        region = os.environ.get("AWS_REGION", "us-east-1")
        return boto3.client("s3", region_name=region), bucket
    except Exception as e:  # noqa: BLE001
        logger.debug("submit_affirmed_batch s3 client unavailable: %s", e)
        return None, ""


_CONTENT_HASH_PREFIX = "contenthash#"


def _content_sha256(data: bytes) -> str:
    """sha256 of the content bytes (matches trigger_handler._title_checksum's digest format)."""
    from src.utils.find_pages import content_sha256

    return content_sha256(data)


CLAIM_WON, CLAIM_DUPLICATE, CLAIM_ERROR = "won", "duplicate", "error"


def _register_content_hash_if_new(digest: str, source_id: str) -> bool:
    """True unless the content is a known duplicate (see ``_claim_content_hash``)."""
    return _claim_content_hash(digest, source_id) != CLAIM_DUPLICATE


def _claim_content_hash(digest: str, source_id: str) -> str:
    """GLOBAL exact-content dedup: atomically claim ``contenthash#<sha>`` across ALL sources,
    regardless of domain/title. Returns:

    * CLAIM_WON - new content, this attempt holds the claim (it releases it if the content is
      not stored: ``claimed_content`` / ``release_content_hash``);
    * CLAIM_DUPLICATE - the identical bytes were already claimed: skip the ingestion;
    * CLAIM_ERROR - no table (local/test) or a DynamoDB error: fail-OPEN, proceed, but nothing
      was claimed, so nothing may be released.

    A conditional put, so two concurrent batches cannot both win the same hash.
    """
    from src.utils import fetch_provenance as fp

    table = _resolve_table(fp)
    if table is None:
        return CLAIM_ERROR  # no registry: proceed, nothing claimed (nothing to release)
    try:
        import json
        import time

        table.put_item(
            Item={
                "cache_key": f"{_CONTENT_HASH_PREFIX}{digest}",
                "response": json.dumps(
                    {"source_id": source_id, "registered_at": int(time.time())}
                ),
                # who holds the claim: only that writer may release it (release_content_hash)
                "source_id": source_id,
            },
            ConditionExpression="attribute_not_exists(cache_key)",
        )
        return CLAIM_WON
    except Exception as e:  # noqa: BLE001
        # Duplicate iff the conditional put failed because the item already exists. Prefer the
        # precise botocore Error.Code; fall back to a class-name/message substring so the helper
        # stays import-light and still classifies correctly if botocore isn't the raiser.
        code = ""
        resp = getattr(e, "response", None)
        if isinstance(resp, dict):
            code = (resp.get("Error", {}) or {}).get("Code", "")
        haystack = f"{code} {type(e).__name__} {e}"
        if "ConditionalCheckFailed" in haystack:
            return CLAIM_DUPLICATE  # identical content already registered
        # Transient/other error: fail-open (proceed) so dedup never blocks a real submit - but
        # nothing was claimed, so a failed write must not release (it could be a valid claim
        # an earlier run holds)
        logger.debug("content-hash claim errored (fail-open): %s", e)
        return CLAIM_ERROR


def release_content_hash(digest: str, source_id: str) -> None:
    """Give a content-hash claim back because the content was NOT stored (task 1.6): otherwise
    every later run skips the content as a duplicate of something that never landed. Only the
    claim's own writer releases it (conditional on ``source_id``). Never raises; a failure is
    alerted, since the content would be skipped for good."""
    from src.utils import fetch_provenance as fp

    table = _resolve_table(fp)
    if table is None:
        return
    try:
        table.delete_item(
            Key={"cache_key": f"{_CONTENT_HASH_PREFIX}{digest}"},
            ConditionExpression="source_id = :s",
            ExpressionAttributeValues={":s": source_id},
        )
        logger.info(
            "OBSERV content_hash.released source=%s %s status=ok - not stored, so the next "
            "run may submit it again",
            source_id,
            digest[:20],
        )
    except Exception as e:  # noqa: BLE001
        if "ConditionalCheckFailed" in f"{type(e).__name__} {e}":
            return  # someone else's claim (or already gone): nothing of ours to release
        from src.utils.observe import observe_error

        observe_error(
            "content_hash",
            "release_failed",
            note=(
                "Content that was NOT stored still holds its dedup claim, so later runs skip it "
                "as a duplicate. Delete the api-cache item named here, then re-run the step"
            ),
            cache_key=f"{_CONTENT_HASH_PREFIX}{digest}"[:120],
            source=source_id,
            error=type(e).__name__,
        )


@contextlib.contextmanager
def claimed_content(digest: str, source_id: str) -> Iterator[bool]:
    """Claim ``digest`` for the block (True = new content, go ahead and store it; False = a
    duplicate). If the block raises, the claim is released so the content is not lost to a
    failed write (task 1.6); a block that fails without raising calls release_content_hash.
    """
    state = _claim_content_hash(digest, source_id)
    try:
        yield state != CLAIM_DUPLICATE
    except BaseException:
        if state == CLAIM_WON:  # only a claim this attempt took is given back
            release_content_hash(digest, source_id)
        raise


def _submit_one_find(
    find: Dict[str, Any], client: Any, bucket: str, result: Dict[str, Any]
) -> None:
    """Submit one affirmed find (mutates ``result``). GLOBAL content-hash dedup first: identical
    bytes already submitted under ANY domain/title are skipped (counted ``deduped``) and the find is
    marked SUBMITTED (terminal). New content is uploaded to the contentrepository/ front door and
    marked SUBMITTED. Fail-safe: one bad find never sinks the batch."""
    from src.utils import fetch_provenance as fp
    from src.utils import find_pages
    from src.utils.url_hygiene import scrub

    fid = find.get("fetch_event_id", "")
    source_id = find_source_id(fid)
    try:
        body = _find_content_bytes(find)
        digest = _content_sha256(body)
        # (a) GLOBAL exact-content dedup across all sources/domains/titles; a failed upload
        # gives the claim back (task 1.6), so the find is not skipped as a duplicate later
        with claimed_content(digest, source_id) as new:
            if not new:
                logger.info(
                    "OBSERV fetch_find.batch_submit.deduped fetch_event_id=%s %s "
                    "— identical content already submitted; skipping re-ingestion",
                    fid,
                    digest[:20],
                )
                set_review_status(
                    fid, fp.SUBMITTED
                )  # terminal: a known duplicate, done
                result["deduped"] += 1
                return
            key = f"contentrepository/_system/{_find_title(find)}/{_safe_token(source_id, 'fetchfind')}.html"
            client.put_object(
                Bucket=bucket,
                Key=key,
                Body=body,
                ContentType="text/html; charset=utf-8",
            )
        # Only count a submission once the SUBMITTED transition is CONFIRMED; otherwise the content
        # is uploaded but the find stays AFFIRMED (deterministic key -> safe re-click overwrite;
        # launch is idle-gated + title-deduped downstream, so a redundant attempt is harmless).
        if set_review_status(fid, fp.SUBMITTED):
            result["submitted"] += 1
            result["source_ids"].append(source_id)
        else:
            logger.warning(
                "submit_affirmed_batch: %s uploaded but SUBMITTED status unconfirmed "
                "(stays AFFIRMED for the next batch)",
                fid,
            )
            result["skipped"] += 1
    except find_pages.PageUnusable as e:
        logger.error(
            "OBSERV fetch_review.page_unusable fetch_event_id=%s reason=%s status=error - the page "
            "this find was approved on is missing or altered in find-pages/, so it is not submitted "
            "(it stays approved and is tried again next batch). Check the data bucket's find-pages/ "
            "objects; if the page is lost, reject the find",
            scrub(str(fid))[:40],
            e,
        )
        result["skipped"] += 1
    except Exception as e:  # noqa: BLE001 - one bad find must not sink the batch
        logger.warning("submit_affirmed_batch: failed to submit %s: %s", fid, e)
        result["skipped"] += 1


def submit_affirmed_batch() -> Dict[str, Any]:
    """BATCH-submit all AFFIRMED-not-yet-submitted finds to Phase 1 in ONE shot.

    Uploads each find's content to ``contentrepository/_system/<title>/fetchfind-<id>.html`` in the
    data bucket. The S3 uploads fan out to the ONE ``content-uploaded`` front door
    (_handle_content_topic), which queues them together and launches Phase 1 ONCE — so the pipeline
    infrastructure spins up a single time for the whole approved batch, not per URL. Each submitted
    find is marked SUBMITTED so it is never re-submitted. Fail-safe: returns a summary, never raises.

    Returns ``{"submitted": N, "source_ids": [...], "skipped": M}``.
    """

    affirmed = list_affirmed()
    result: Dict[str, Any] = {
        "submitted": 0,
        "source_ids": [],
        "skipped": 0,
        "deduped": 0,
    }
    if not affirmed:
        logger.info("OBSERV fetch_find.batch_submit affirmed=0 — nothing to submit")
        return result

    client, bucket = _s3_client_and_bucket()
    if client is None:
        logger.warning("submit_affirmed_batch: no S3 bucket configured — cannot submit")
        result["skipped"] = len(affirmed)
        return result

    for find in affirmed:
        _submit_one_find(find, client, bucket, result)

    logger.info(
        "OBSERV fetch_find.batch_submit.summary affirmed=%d submitted=%d deduped=%d skipped=%d "
        "bucket=%s front_door=content-uploaded launches=1",
        len(affirmed),
        result["submitted"],
        result["deduped"],
        result["skipped"],
        bucket,
    )
    if result["skipped"]:
        logger.warning(
            "OBSERV fetch_find.batch_submit.anomaly %d-of-%d affirmed find(s) failed to submit "
            "(see prior warnings) — they remain AFFIRMED for the next batch",
            result["skipped"],
            len(affirmed),
        )
    return result

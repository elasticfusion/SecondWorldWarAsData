"""Human-review + promotion bridge for TEXTUAL fetch-provenance finds.

Principle (owner): a positive textual find (e.g. a Wayback snapshot) is enqueued for human
review; **on affirm** it flows through the SAME path as a deliberately-uploaded book — it becomes
a ``SourceMetadata`` record handed to the ingestion front-end (Phase 0/1), rather than being
written directly as facts by an enrichment side-channel.

This closes the gap identified in docs/current/FETCH_PROVENANCE_DESIGN.md: ``source_retrieval``
already quarantines internet content for review, but nothing promoted an AFFIRMED item into
ingestion. ``affirm_fetch_find`` is that bridge (minimal function/CLI first; a UI can call it
later). Reuses: fetch_provenance (the review_status), source_retrieval quarantine, SourceMetadata.

All operations are fail-safe and never process content automatically — promotion only stages a
source for the normal pipeline; extraction remains the pipeline's job.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


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
    from src.utils import fetch_provenance as fp

    table = _resolve_table(fp)
    if table is not None:
        return _load_events_dynamo(table, fp)
    return _load_events_local(fp)


def _load_events_dynamo(table: Any, fp: Any) -> List[Dict[str, Any]]:
    """Scan the DynamoDB cache table for fetch-provenance TEXTUAL events (paginated). Fail-safe."""
    import json

    from boto3.dynamodb.conditions import Attr

    out: List[Dict[str, Any]] = []
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
                    continue
                if isinstance(rec, dict) and rec.get("kind") == fp.TEXTUAL:
                    out.append(rec)
            lek = resp.get("LastEvaluatedKey")
            if not lek:
                break
            kwargs["ExclusiveStartKey"] = lek
    except Exception as e:  # noqa: BLE001
        logger.debug("fetch_review dynamo scan skipped: %s", e)
    return out


def _load_events_local(fp: Any) -> List[Dict[str, Any]]:
    """Load local textual fetch-provenance events (JSONL reference surface). Fail-safe."""
    import json

    out: List[Dict[str, Any]] = []
    try:
        d: Path = fp._LOCAL_DIR  # noqa: SLF001 - reuse the configured local dir
        if not d.exists():
            return out
        for jf in d.glob("*.jsonl"):
            for line in jf.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                try:
                    rec = json.loads(line)
                except Exception:  # noqa: BLE001
                    continue
                if rec.get("kind") == fp.TEXTUAL:
                    out.append(rec)
    except Exception as e:  # noqa: BLE001
        logger.debug("fetch_review load skipped: %s", e)
    return out


def list_pending() -> List[Dict[str, Any]]:
    """Textual finds awaiting human review (review_status == pending)."""
    from src.utils import fetch_provenance as fp

    return [e for e in _load_events() if e.get("review_status") == fp.PENDING]


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
        response = grok_client.chat_completion(
            prompt=(
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
            ),
            system_prompt="You decide the scope of a search-source exclusion.",
            temperature=0.0,
            use_cache=True,
            cache_type="exclude_scope",
        )
        scope = (
            SCOPE_SITE
            if str(response).strip().upper().startswith("SITE")
            else SCOPE_URL
        )
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


def affirm_fetch_find(
    fetch_event: Dict[str, Any], quarantine_path: Path, *, media_type: str = "html"
) -> Optional["SourceMetadata"]:  # type: ignore[name-defined] # noqa: F821
    """Promote an AFFIRMED textual find into a SourceMetadata for ingestion — the SAME front door
    as an uploaded book. Returns the SourceMetadata (staged, not yet extracted) or None on failure.

    ``fetch_event`` is the stored provenance record; ``quarantine_path`` is the reviewed, affirmed
    content already downloaded by source_retrieval's quarantine. Fail-safe.
    """
    try:
        from src.ingestion.source_metadata import SourceMetadata

        endpoint = fetch_event.get("endpoint", "")
        summary = fetch_event.get("result_summary") or ""
        # Capture the archive snapshot URL as the acquisition provenance when present.
        acq_url = endpoint
        if "->" in summary:
            acq_url = summary.split("->", 1)[1].strip() or endpoint

        src = SourceMetadata(
            source_id=f"fetchfind-{fetch_event.get('fetch_event_id', '')}",
            original_path=Path(quarantine_path),
            media_type=media_type,  # type: ignore[arg-type]
            acquisition_method="web_archive_affirmed",
            acquisition_url=acq_url,
            capture_date=fetch_event.get("queried_at"),
            notes=(
                "Promoted from an affirmed textual fetch-provenance find "
                f"(provider={fetch_event.get('provider')}, "
                f"fetch_event_id={fetch_event.get('fetch_event_id')}). "
                "Enters ingestion like an uploaded book."
            ),
        )
        logger.info(
            "OBSERV fetch_find.affirmed provider=%s fetch_event_id=%s -> source_id=%s staged for ingestion",
            fetch_event.get("provider"),
            fetch_event.get("fetch_event_id"),
            src.source_id,
        )
        from src.utils import fetch_provenance as _fp

        set_review_status(fetch_event.get("fetch_event_id", ""), _fp.AFFIRMED)
        return src
    except Exception as e:  # noqa: BLE001 - promotion must never crash a review action
        logger.warning(
            "fetch_find affirm failed for %s: %s",
            fetch_event.get("fetch_event_id"),
            e,
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
    """The bytes to submit for a find: the quarantined file if present locally, else a provenance
    HTML stub (the source URL + metadata) so Phase 1 can ingest it like any uploaded source.
    """
    import html as _html
    from pathlib import Path as _Path

    qp = fetch_event.get("quarantine_path") or fetch_event.get("retrieved_path") or ""
    try:
        if qp and _Path(qp).is_file():
            return _Path(qp).read_bytes()
    except Exception:  # noqa: BLE001
        pass
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
    import hashlib

    return "sha256:" + hashlib.sha256(data).hexdigest()


def _register_content_hash_if_new(digest: str, source_id: str) -> bool:
    """GLOBAL exact-content dedup: atomically claim ``contenthash#<sha>`` across ALL sources,
    regardless of domain/title. Returns True if this is NEW content (claim won -> proceed), False
    if the identical bytes were already submitted (skip the duplicate ingestion).

    Fail-SAFE but fail-OPEN: if there is no table (local/test) or any error, return True (proceed) —
    dedup must never block a legitimate submission. Uses a conditional put so two concurrent batches
    cannot both win the same hash.
    """
    from src.utils import fetch_provenance as fp

    table = _resolve_table(fp)
    if table is None:
        return True
    try:
        import json
        import time

        table.put_item(
            Item={
                "cache_key": f"{_CONTENT_HASH_PREFIX}{digest}",
                "response": json.dumps(
                    {"source_id": source_id, "registered_at": int(time.time())}
                ),
            },
            ConditionExpression="attribute_not_exists(cache_key)",
        )
        return True  # claim won -> new content
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
            return False  # identical content already registered -> duplicate
        # Transient/other error: fail-open (proceed) so dedup never blocks a real submit.
        logger.debug("content-hash claim errored (fail-open): %s", e)
        return True


def _submit_one_find(
    find: Dict[str, Any], client: Any, bucket: str, result: Dict[str, Any]
) -> None:
    """Submit one affirmed find (mutates ``result``). GLOBAL content-hash dedup first: identical
    bytes already submitted under ANY domain/title are skipped (counted ``deduped``) and the find is
    marked SUBMITTED (terminal). New content is uploaded to the contentrepository/ front door and
    marked SUBMITTED. Fail-safe: one bad find never sinks the batch."""
    from src.utils import fetch_provenance as fp

    fid = find.get("fetch_event_id", "")
    source_id = f"fetchfind-{fid}"
    try:
        body = _find_content_bytes(find)
        digest = _content_sha256(body)
        # (a) GLOBAL exact-content dedup across all sources/domains/titles.
        if not _register_content_hash_if_new(digest, source_id):
            logger.info(
                "OBSERV fetch_find.batch_submit.deduped fetch_event_id=%s %s "
                "— identical content already submitted; skipping re-ingestion",
                fid,
                digest[:20],
            )
            set_review_status(fid, fp.SUBMITTED)  # terminal: a known duplicate, done
            result["deduped"] += 1
            return
        key = f"contentrepository/_system/{_find_title(find)}/{_safe_token(source_id, 'fetchfind')}.html"
        client.put_object(
            Bucket=bucket, Key=key, Body=body, ContentType="text/html; charset=utf-8"
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
    from src.utils import fetch_provenance as fp

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

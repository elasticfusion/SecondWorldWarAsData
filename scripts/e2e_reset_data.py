#!/usr/bin/env python3
"""Data half of scripts/e2e_reset.sh: sweep run-state, derived data and queues, then VERIFY.

The shell script keeps the orchestration (pause backstops, stop ECS tasks/services, delete the
NAT); everything that touches DynamoDB / S3 / SQS lives here so it is paginated, batched, tested
(tests/test_e2e_reset_data.py, moto) and — unlike the old shell loops — FAILS CLOSED: any AWS error
is reported and exits non-zero, and verification treats an unreadable resource as "not clean".

Modes
  sweep   default : run-state keys (title#/pending#/manifest#/book_manifest#) + the manifests/ S3
                    prefix + contentrepository/ and output/content/ for --wipe-title titles.
          --full  : additionally the per-entity tables, the derived S3 prefixes (output/
                    needs-review/ dedup/ person_photos/ — every object VERSION, so the versioned
                    bucket is truly emptied), the SQS work queues + DLQs, and the lock#/batch_job#/
                    dedup_run#/reaper#/nat# run-state keys.
  verify  re-reads everything the sweep touched; exit 1 if anything is left or unreadable.

NEVER touched (cost / human data): the api-cache grok/fetch/search/url/place_coords/metrics rows,
the human dedup exclusion#/name_exclusion# rows, and contentrepository/ (re-uploaded sources keep
their source-url metadata) except for --wipe-title titles. NOTE --full DOES empty the dedup/ S3
prefix and the reviewed#/dedup_run# rows: they reference entity IDs that the wipe regenerates, so
they would be stale (the exclusion# rows are keyed by entity filename and are harmless).
"""

from __future__ import annotations

import argparse
import re
import sys
import time
from typing import Callable, Iterable, List, Sequence, Tuple

import boto3
from botocore.exceptions import BotoCoreError, ClientError

# Always swept. lock#/batch_job#/nat# are what nat-manager's demand check reads: left behind they
# keep the NAT up (the reset would then report NOT clean for no visible reason).
RUNSTATE_PREFIXES = (
    "title#",
    "pending#",
    "manifest#",
    "book_manifest#",
    "lock#",
    "batch_job#",
    "nat#",
)
# --full adds the remaining per-run state: doc lifecycle rows the dispatcher re-scans, parked
# task-token gates, OCR/retrieve claims, dedup review marks and the idle reaper's strike counter.
FULL_RUNSTATE_PREFIXES = (
    "dedup_run#",
    "reaper#",
    "doc#",
    "gate#",
    "ocr#",
    "ocrctl#",
    "oom#",
    "retrieve#",
    "reviewed#",
)
ENTITY_TABLES = (
    "people",
    "places",
    "maps",
    "bibliography",
    "casualties",
    "dates",
    "equipment",
    "groups",
    "logistics",
    "weather",
)
# Every SQS queue defined in cloudformation/ (compute.yaml, events.yaml). trigger-queue has a Lambda
# event-source mapping, so sweep() purges the queues FIRST.
SQS_QUEUES = (
    "trigger-queue",
    "openserp-work",
    "openserp-work-dlq",
    "s3-validation-backstop-dlq",
)
FULL_S3_PREFIXES = ("output", "needs-review", "dedup", "person_photos")
SAFE_ENVS = ("dev", "test")
_ENV_RE = re.compile(r"[a-z0-9-]{1,20}")
_TITLE_RE = re.compile(r"[A-Za-z0-9._-]{1,120}")
_BATCH = 25  # DynamoDB BatchWriteItem limit
_S3_BATCH = 1000  # S3 DeleteObjects limit


class ResetError(RuntimeError):
    """A reset step failed; the script must exit non-zero (never report 'clean')."""


# --- DynamoDB ---------------------------------------------------------------------------------


def _key_schema(ddb, table: str) -> Tuple[str, str | None]:
    desc = ddb.describe_table(TableName=table)["Table"]["KeySchema"]
    hk = next(k["AttributeName"] for k in desc if k["KeyType"] == "HASH")
    rk = next((k["AttributeName"] for k in desc if k["KeyType"] == "RANGE"), None)
    return hk, rk


def _scan_keys(ddb, table: str, hk: str, rk: str | None, prefixes: Sequence[str] = ()):
    """Yield the key dicts of matching items. Reserved-word-safe (#names), consistent, paginated."""
    names = {"#h": hk}
    proj = "#h"
    if rk:
        names["#r"] = rk
        proj += ",#r"
    kwargs = {
        "TableName": table,
        "ProjectionExpression": proj,
        "ExpressionAttributeNames": names,
        "ConsistentRead": True,
    }
    if prefixes:
        kwargs["FilterExpression"] = " OR ".join(
            f"begins_with(#h, :p{i})" for i in range(len(prefixes))
        )
        kwargs["ExpressionAttributeValues"] = {
            f":p{i}": {"S": p} for i, p in enumerate(prefixes)
        }
    for page in ddb.get_paginator("scan").paginate(**kwargs):
        for item in page.get("Items", []):
            yield {k: v for k, v in item.items() if k in (hk, rk)}


def _batch_delete(ddb, table: str, keys: Iterable[dict]) -> int:
    """Delete keys 25 at a time, retrying UnprocessedItems with backoff. Raises on persistent
    failure (a throttled delete must not be reported as cleared)."""
    deleted = 0
    batch: List[dict] = []

    def flush(items: List[dict]) -> None:
        pending = [{"DeleteRequest": {"Key": k}} for k in items]
        for attempt in range(8):
            resp = ddb.batch_write_item(RequestItems={table: pending})
            pending = resp.get("UnprocessedItems", {}).get(table, [])
            if not pending:
                return
            time.sleep(min(2**attempt * 0.1, 5))
        raise ResetError(
            f"{table}: {len(pending)} delete(s) still unprocessed (throttled)"
        )

    for key in keys:
        batch.append(key)
        if len(batch) == _BATCH:
            flush(batch)
            deleted += len(batch)
            batch = []
    if batch:
        flush(batch)
        deleted += len(batch)
    return deleted


def clear_table(ddb, table: str, prefixes: Sequence[str] = ()) -> int:
    """Delete all items (or only items whose hash key starts with a prefix). Missing table -> 0
    is the caller's decision; any other error propagates."""
    hk, rk = _key_schema(ddb, table)
    # Materialise first: deleting while scanning is allowed but makes page boundaries ambiguous.
    return _batch_delete(ddb, table, list(_scan_keys(ddb, table, hk, rk, prefixes)))


def count_left(ddb, table: str, prefixes: Sequence[str] = ()) -> int:
    hk, rk = _key_schema(ddb, table)
    return sum(1 for _ in _scan_keys(ddb, table, hk, rk, prefixes))


# --- S3 ---------------------------------------------------------------------------------------


def empty_prefix(s3, bucket: str, prefix: str) -> int:
    """Delete EVERY version and delete marker under prefix/ (a plain delete on a versioned bucket
    only adds markers). Returns the number of versions/markers removed."""
    removed = 0
    batch: List[dict] = []
    paginator = s3.get_paginator("list_object_versions")
    for page in paginator.paginate(Bucket=bucket, Prefix=f"{prefix}/"):
        for v in [*page.get("Versions", []), *page.get("DeleteMarkers", [])]:
            batch.append({"Key": v["Key"], "VersionId": v["VersionId"]})
            if len(batch) == _S3_BATCH:
                removed += _delete_objects(s3, bucket, batch)
                batch = []
    if batch:
        removed += _delete_objects(s3, bucket, batch)
    return removed


def _delete_objects(s3, bucket: str, batch: List[dict]) -> int:
    resp = s3.delete_objects(Bucket=bucket, Delete={"Objects": batch, "Quiet": True})
    if resp.get("Errors"):
        e = resp["Errors"][0]
        raise ResetError(f"s3 delete failed for {e.get('Key')}: {e.get('Code')}")
    return len(batch)


def s3_left(s3, bucket: str, prefix: str) -> int:
    n = 0
    for page in s3.get_paginator("list_object_versions").paginate(
        Bucket=bucket, Prefix=f"{prefix}/"
    ):
        n += len(page.get("Versions", [])) + len(page.get("DeleteMarkers", []))
    return n


# --- SQS --------------------------------------------------------------------------------------


def purge_queue(sqs, name: str) -> str:
    """Purge one queue. Returns 'purged' | 'missing'. SQS allows one purge per 60s per queue:
    PurgeQueueInProgress waits and retries once; any other error raises."""
    try:
        url = sqs.get_queue_url(QueueName=name)["QueueUrl"]
    except ClientError as e:
        if e.response["Error"]["Code"] in (
            "AWS.SimpleQueueService.NonExistentQueue",
            "QueueDoesNotExist",
        ):
            return "missing"
        raise
    for attempt in (0, 1):
        try:
            sqs.purge_queue(QueueUrl=url)
            return "purged"
        except ClientError as e:
            if e.response["Error"]["Code"] == "PurgeQueueInProgress" and attempt == 0:
                time.sleep(61)
                continue
            raise
    return "purged"  # pragma: no cover


def queue_depth(sqs, name: str) -> int | None:
    """Messages visible + in flight + delayed, or None if the queue does not exist."""
    try:
        url = sqs.get_queue_url(QueueName=name)["QueueUrl"]
    except ClientError as e:
        if e.response["Error"]["Code"] in (
            "AWS.SimpleQueueService.NonExistentQueue",
            "QueueDoesNotExist",
        ):
            return None
        raise
    attrs = sqs.get_queue_attributes(QueueUrl=url, AttributeNames=["All"])["Attributes"]
    return sum(
        int(attrs.get(k, 0))
        for k in (
            "ApproximateNumberOfMessages",
            "ApproximateNumberOfMessagesNotVisible",
            "ApproximateNumberOfMessagesDelayed",
        )
    )


# --- orchestration ----------------------------------------------------------------------------


def _is_missing(e: ClientError) -> bool:
    return e.response["Error"]["Code"] == "ResourceNotFoundException"


def plan(full: bool, titles: Sequence[str], queues: bool = True) -> dict:
    """The exact set of things sweep removes / verify checks (shared by both: DRY)."""
    return {
        "runstate": RUNSTATE_PREFIXES + (FULL_RUNSTATE_PREFIXES if full else ()),
        "tables": ENTITY_TABLES if full else (),
        "s3_prefixes": ("manifests",)
        + (FULL_S3_PREFIXES if full else ())
        + tuple(
            p for t in titles for p in (f"contentrepository/{t}", f"output/content/{t}")
        ),
        "queues": SQS_QUEUES if (full and queues) else (),
    }


def sweep(
    env: str,
    region: str,
    full: bool,
    titles: Sequence[str],
    log=print,
    queues: bool = True,
) -> int:
    """Run the sweep; returns the number of problems (0 = all steps succeeded). ``queues=False``
    skips the SQS purge (the second race-proofing pass; SQS allows one purge per 60s).
    """
    p = plan(full, titles, queues)
    ddb = boto3.client("dynamodb", region_name=region)
    s3 = boto3.client("s3", region_name=region)
    sqs = boto3.client("sqs", region_name=region)
    cache, bucket = f"{env}-wwii-api-cache", f"{env}-wwii-data-pipeline"
    problems = 0

    def step(label: str, fn: Callable[[], str]) -> None:
        nonlocal problems
        try:
            log(f"  {label}: {fn()}")
        except (ClientError, BotoCoreError, ResetError) as e:
            problems += 1
            log(
                f"  {label}: FAILED — {e}",
            )

    # Queues FIRST: trigger-queue feeds a Lambda that launches tasks and writes pending#/lock# rows;
    # purging it before the tables are cleared stops it re-creating state mid-sweep.
    for q in p["queues"]:
        step(f"queue {env}-wwii-{q}", lambda qn=q: purge_queue(sqs, f"{env}-wwii-{qn}"))
    step(
        f"{cache} run-state {list(p['runstate'])}",
        lambda: f"deleted {clear_table(ddb, cache, p['runstate'])}",
    )
    for prefix in p["s3_prefixes"]:
        step(
            f"s3://{bucket}/{prefix}/",
            lambda pr=prefix: f"removed {empty_prefix(s3, bucket, pr)} version(s)",
        )
    for t in p["tables"]:
        table = f"{env}-wwii-{t}"

        def _do(tb=table) -> str:
            try:
                return f"deleted {clear_table(ddb, tb)}"
            except ClientError as e:
                if _is_missing(e):
                    return "table not found (skipped)"
                raise

        step(table, _do)
    return problems


def verify(
    env: str,
    region: str,
    full: bool,
    titles: Sequence[str],
    log=print,
    wait_s: int = 90,
) -> int:
    """Re-read everything; returns the leftover/unreadable count (0 = clean). Unreadable counts
    as NOT clean. Queue depth is eventually consistent after a purge, so poll up to wait_s.
    """
    p = plan(full, titles)
    ddb = boto3.client("dynamodb", region_name=region)
    s3 = boto3.client("s3", region_name=region)
    sqs = boto3.client("sqs", region_name=region)
    cache, bucket = f"{env}-wwii-api-cache", f"{env}-wwii-data-pipeline"
    left = 0

    def check(label: str, fn: Callable[[], int | None]) -> None:
        nonlocal left
        try:
            n = fn()
        except (ClientError, BotoCoreError, ResetError) as e:
            left += 1
            log(f"  VERIFY-ERROR {label}: {e}")
            return
        if n:
            left += n
            log(f"  LEFT {label}: {n}")

    check(cache, lambda: count_left(ddb, cache, p["runstate"]))
    for prefix in p["s3_prefixes"]:
        check(f"s3://{bucket}/{prefix}/", lambda pr=prefix: s3_left(s3, bucket, pr))
    for t in p["tables"]:

        def _cnt(tb=f"{env}-wwii-{t}") -> int:
            try:
                return count_left(ddb, tb)
            except ClientError as e:
                if _is_missing(e):
                    return 0
                raise

        check(f"{env}-wwii-{t}", _cnt)
    for q in p["queues"]:
        deadline = time.time() + wait_s
        name = f"{env}-wwii-{q}"
        while True:
            try:
                depth = queue_depth(sqs, name)
            except (ClientError, BotoCoreError) as e:
                left += 1
                log(f"  VERIFY-ERROR queue {name}: {e}")
                break
            if not depth or time.time() >= deadline:
                if depth:
                    left += depth
                    log(f"  LEFT queue {name}: ~{depth} message(s)")
                break
            time.sleep(5)
    log(f"  leftovers: {left}")
    return left


def _validate(args: argparse.Namespace) -> None:
    if not _ENV_RE.fullmatch(args.env):
        raise SystemExit(f"invalid --env {args.env!r}")
    for t in args.wipe_title:
        if not _TITLE_RE.fullmatch(t):
            raise SystemExit(f"invalid --wipe-title {t!r}")
    if args.env not in SAFE_ENVS and not args.allow_nondev_full:
        # Even the default mode deletes lock#/batch_job#/nat# rows and the shell stops tasks and
        # deletes the NAT; --full also empties entity tables, queues and output/.
        raise SystemExit(
            f"e2e reset refused for env {args.env!r} (it stops tasks, deletes the NAT and "
            f"clears run-state{' + all derived data' if args.full else ''}); "
            f"pass --allow-nondev-full only if you are certain"
        )


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("mode", choices=("sweep", "verify", "validate"))
    ap.add_argument("--env", default="dev")
    ap.add_argument("--region", default="us-east-1")
    ap.add_argument("--full", action="store_true")
    ap.add_argument("--wipe-title", action="append", default=[])
    ap.add_argument("--allow-nondev-full", action="store_true")
    ap.add_argument(
        "--skip-queues", action="store_true", help="sweep: do not purge SQS"
    )
    args = ap.parse_args(argv)
    _validate(args)
    if args.mode == "validate":
        return (
            0  # the shell calls this FIRST so a refused invocation stops before step 1
        )
    if args.mode == "sweep":
        problems = sweep(
            args.env,
            args.region,
            args.full,
            args.wipe_title,
            queues=not args.skip_queues,
        )
    else:
        problems = verify(args.env, args.region, args.full, args.wipe_title)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())

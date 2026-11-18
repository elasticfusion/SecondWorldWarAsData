#!/usr/bin/env python3
"""Snapshot the pipeline data set and restore it (owner, 2026-11-17: back up the Phase 2 output so
a Phase 3 investigation can reset to it). Runbook: docs/current/RUNBOOK.md "Snapshot and restore".

Modes
  backup  --name N   copy the S3 prefixes output/ dedup/ manifests/ to backups/N/, take a DynamoDB
                     on-demand backup named N, and save every DynamoDB key plus a manifest.json
                     under backups/N/. Refuses a name already used.
  restore --name N   make the live data match backup N. A DRY RUN (prints the plan) unless --apply.
  verify  --name N   exit 1 unless the live data matches backup N (same rules as restore).

Rules (owner decisions, 2026-11-17)
  - S3: each prefix becomes an exact copy of the backup: changed or missing objects are copied back,
    objects added since are deleted. The bucket is versioned, so a deleted or overwritten object
    stays recoverable as an older version.
  - DynamoDB, cached Grok answers (``<cache_type>#<64-hex>``, optionally under ``books/<book>/``)
    are KEPT as they are: not deleted, not overwritten, so a re-run does not pay for them again.
  - DynamoDB, every other known prefix (RESET_PREFIXES: run state, locks, leases, verdict and
    registry caches, fetch events and claims, dedup rows) returns to the snapshot: rows added since are deleted;
    rows changed or deleted since are put back from the table backup, which is restored to a
    temporary table for the comparison and then dropped. --no-table-restore skips that (faster and
    free) and only deletes the added rows.
  - IGNORED_KEYS are operational heartbeats (the idle reaper's strike counter, rewritten hourly):
    never compared or restored, so verify does not fail an hour after every restore.
  - A key that is none of these is UNCLASSIFIED: left alone and listed; verify fails until the prefix is
    added to RESET_PREFIXES or the Grok key rule.
  - Copying a ``output/content/*-parsed.json`` back fires the bucket's Phase 2 trigger (S3 ->
    SNS -> trigger Lambda). restore --apply refuses while the plan copies any, unless
    --allow-phase2-trigger is given.

Every step refuses to run while a pipeline task is running, and every AWS error exits non-zero.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess  # nosec B404 - fixed argv (git rev-parse), no shell, no user input
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

import boto3

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.e2e_reset_data import (  # noqa: E402
    FULL_RUNSTATE_PREFIXES,
    RUNSTATE_PREFIXES,
    SAFE_ENVS,
    ResetError,
    _batch_delete,
    _delete_objects,
)

PREFIXES = ("output", "dedup", "manifests")
BACKUP_ROOT = "backups"
KEY = "cache_key"
# Returned to the snapshot. Run state and locks (shared with the E2E reset) plus what Phase 3 and
# the dedup step write: OpenSERP/search verdicts, the URL registry and fetch gates, fetch events,
# dedup exclusions and place coordinates, batch statistics, spend. Checked BEFORE the Grok rule:
# a url# key has the same <word>#<64-hex> shape as a Grok answer.
RESET_PREFIXES = tuple(
    dict.fromkeys(
        RUNSTATE_PREFIXES
        + FULL_RUNSTATE_PREFIXES
        + (
            "search#",
            "url#",
            "fetchgate#",
            "excludedomain#",
            "fetchevent#",
            "exclusion#",
            "name_exclusion#",
            "place_coords#",
            "batch_stats#",
            "metrics#",
            "credit#",
            "contenthash#",  # Phase 3 fetch claims (src/enrichment/fetch_review.py)
            "spot#",  # Spot price ladder per book (src/utils/spot_ladder.py)
            "taskstop#",  # a task's own stop report (src/utils/task_marker.py)
            "stopevt#",  # stop events already reported (trigger_handler._first_report)
        )
    )
)
IGNORED_KEYS = frozenset(
    {"reaper#state"}
)  # lambda_handlers/idle_backstop_reaper._save_state
# Operational caches, not run state: the daily Fargate rates (lambda_handlers/price_refresh.py)
IGNORED_PREFIXES = ("price#",)
GROK_KEY = re.compile(r"(books/[A-Za-z0-9._-]+/)?[a-z0-9_]+#[0-9a-f]{64}")
_NAME_RE = re.compile(r"[A-Za-z0-9._-]{3,64}")  # DynamoDB BackupName rules
_ENV_RE = re.compile(r"[a-z0-9-]{1,20}")
_BATCH = 25  # DynamoDB BatchWriteItem limit
_S3_BATCH = 1000  # S3 DeleteObjects limit


def classify(key: str) -> str:
    """'ignore' | 'reset' | 'grok' | 'unclassified' for a DynamoDB cache_key (module rules)."""
    if key in IGNORED_KEYS or key.startswith(IGNORED_PREFIXES):
        return "ignore"
    if key.startswith(RESET_PREFIXES):
        return "reset"
    if GROK_KEY.fullmatch(key):
        return "grok"
    return "unclassified"


# --- guards -------------------------------------------------------------------------------------


def pipeline_tasks(
    ecs, cluster: str, exempt_groups: frozenset = frozenset()
) -> List[str]:
    """ARNs of running or starting tasks that can write pipeline data: every task except those in
    ``exempt_groups``. Only the OpenSERP search server is exempt (it answers searches and writes
    nothing); the OpenSERP WORKER service writes output/ and DynamoDB, so it counts."""
    arns: List[str] = []
    for status in ("RUNNING", "PENDING"):
        for page in ecs.get_paginator("list_tasks").paginate(
            cluster=cluster, desiredStatus=status
        ):
            arns += page.get("taskArns", [])
    out = []
    for i in range(0, len(arns), 100):
        for t in ecs.describe_tasks(cluster=cluster, tasks=arns[i : i + 100])["tasks"]:
            if str(t.get("group", "")) not in exempt_groups:
                out.append(t["taskArn"])
    return out


def _refuse_if_busy(ecs, env: str) -> None:
    cluster = _names(env)[2]
    busy = pipeline_tasks(ecs, cluster, frozenset({f"service:{env}-wwii-openserp"}))
    if busy:
        raise ResetError(
            f"{len(busy)} pipeline task(s) running in {cluster} (e.g. {busy[0]}): a snapshot "
            "taken or restored now would be inconsistent; wait for them or stop them"
        )


# --- S3 -----------------------------------------------------------------------------------------


def list_objects(s3, bucket: str, prefix: str) -> Dict[str, Tuple[str, int]]:
    """{key relative to prefix/: (ETag, size)} of the current objects under prefix/."""
    out: Dict[str, Tuple[str, int]] = {}
    for page in s3.get_paginator("list_objects_v2").paginate(
        Bucket=bucket, Prefix=f"{prefix}/"
    ):
        for o in page.get("Contents", []):
            out[o["Key"][len(prefix) + 1 :]] = (o["ETag"], o["Size"])
    return out


def _same(s3, bucket: str, a: str, b: str, ma, mb) -> bool:
    """Same content? ETags of single-part objects are their MD5; a multipart ETag ('-N') is not,
    so those are compared byte for byte (rare: objects here are small)."""
    if ma[1] != mb[1]:
        return False
    if ma[0] == mb[0]:
        return True
    if "-" not in ma[0] + mb[0]:
        return False
    return _read(s3, bucket, a) == _read(s3, bucket, b)


def _exists(s3, bucket: str, key: str) -> bool:
    return bool(
        s3.list_objects_v2(Bucket=bucket, Prefix=key, MaxKeys=1).get("KeyCount")
    )


def _read(s3, bucket: str, key: str) -> bytes:
    return s3.get_object(Bucket=bucket, Key=key)["Body"].read()


def triggering_copies(plan: dict) -> List[str]:
    """The planned copy-backs that start Phase 2: parsed chapter files under output/content/."""
    return [
        f"output/{k}"
        for k in plan["s3"].get("output", {}).get("copy", [])
        if k.startswith("content/") and k.endswith("-parsed.json")
    ]


def s3_plan(s3, bucket: str, name: str) -> Dict[str, Dict[str, List[str]]]:
    """Per prefix: the relative keys to copy back from the backup and to delete from live."""
    plan = {}
    for p in PREFIXES:
        src = f"{BACKUP_ROOT}/{name}/{p}"
        snap, live = list_objects(s3, bucket, src), list_objects(s3, bucket, p)
        copy = [
            k
            for k, m in snap.items()
            if k not in live
            or not _same(s3, bucket, f"{src}/{k}", f"{p}/{k}", m, live[k])
        ]
        plan[p] = {"copy": sorted(copy), "delete": sorted(set(live) - set(snap))}
    return plan


def copy_keys(s3, bucket: str, src: str, dst: str, keys: Sequence[str]) -> int:
    for k in keys:
        s3.copy_object(
            Bucket=bucket,
            Key=f"{dst}/{k}",
            CopySource={"Bucket": bucket, "Key": f"{src}/{k}"},
        )
    return len(keys)


def delete_keys(s3, bucket: str, prefix: str, keys: Sequence[str]) -> int:
    """Delete current objects (versioned bucket: a delete marker, so the object is recoverable)."""
    n = 0
    for i in range(0, len(keys), _S3_BATCH):
        n += _delete_objects(
            s3, bucket, [{"Key": f"{prefix}/{k}"} for k in keys[i : i + _S3_BATCH]]
        )
    return n


# --- DynamoDB -----------------------------------------------------------------------------------


def scan_items(ddb, table: str) -> Dict[str, dict]:
    """{cache_key: full item (typed)} of every item; consistent, paginated."""
    out = {}
    for page in ddb.get_paginator("scan").paginate(
        TableName=table, ConsistentRead=True
    ):
        for item in page.get("Items", []):
            out[item[KEY]["S"]] = item
    return out


def scan_keys(ddb, table: str) -> List[str]:
    keys: List[str] = []
    for page in ddb.get_paginator("scan").paginate(
        TableName=table,
        ProjectionExpression="#k",
        ExpressionAttributeNames={"#k": KEY},
        ConsistentRead=True,
    ):
        keys += [i[KEY]["S"] for i in page.get("Items", [])]
    return keys


def backup_arn(ddb, table: str, name: str) -> Optional[str]:
    """The ARN of the AVAILABLE on-demand backup ``name`` of ``table``, else None."""
    kwargs = {"TableName": table}
    while True:
        resp = ddb.list_backups(**kwargs)
        for b in resp.get("BackupSummaries", []):
            if b["BackupName"] == name and b["BackupStatus"] == "AVAILABLE":
                return b["BackupArn"]
        if "LastEvaluatedBackupArn" not in resp:
            return None
        kwargs["ExclusiveStartBackupArn"] = resp["LastEvaluatedBackupArn"]


def snapshot_items(ddb, table: str, arn: str, name: str, log=print) -> Dict[str, dict]:
    """Restore backup ``arn`` to a temporary table, read every item, drop the table (always)."""
    temp = f"{table}-snap-{name}"[:255]
    if temp in _tables(ddb):
        raise ResetError(
            f"temporary table {temp} already exists (left by an interrupted run): delete it "
            f"(aws dynamodb delete-table --table-name {temp}) and re-run"
        )
    ddb.restore_table_from_backup(
        TargetTableName=temp, BackupArn=arn, BillingModeOverride="PAY_PER_REQUEST"
    )
    log(f"restoring backup to temporary table {temp} (takes minutes)")
    try:
        ddb.get_waiter("table_exists").wait(
            TableName=temp, WaiterConfig={"Delay": 15, "MaxAttempts": 120}
        )
        return _unexpired(scan_items(ddb, temp))
    finally:
        _drop(ddb, temp, log)


def _tables(ddb) -> List[str]:
    names: List[str] = []
    for page in ddb.get_paginator("list_tables").paginate():
        names += page.get("TableNames", [])
    return names


def _drop(ddb, temp: str, log) -> None:
    """Drop the temporary table; never mask the original error, but say what to clean up."""
    try:
        ddb.delete_table(TableName=temp)
        log(f"dropped temporary table {temp}")
    except Exception as e:  # noqa: BLE001 - reported with the manual fix
        log(
            f"ERROR: temporary table {temp} NOT dropped ({type(e).__name__}); delete it when it "
            f"is ACTIVE: aws dynamodb delete-table --table-name {temp}"
        )


def _unexpired(items: Dict[str, dict]) -> Dict[str, dict]:
    """Snapshot rows whose DynamoDB TTL (``ttl``, epoch seconds) has passed count as absent: put
    back, TTL would delete them again and verify would flap."""
    now = time.time()
    return {
        k: v for k, v in items.items() if float(v.get("ttl", {}).get("N", "inf")) > now
    }


def ddb_plan(
    live: Dict[str, dict], snap: Dict[str, Optional[dict]]
) -> Dict[str, List[str]]:
    """What returns DynamoDB to the snapshot. ``snap`` maps each snapshot key to its item, or to
    None when only the key list is known (--no-table-restore: changed rows are not checked).
    """
    delete, unc_live = _added_side(live, snap)
    put, unc_snap = _snapshot_side(live, snap)
    return {
        "delete": sorted(delete),
        "put": sorted(put),
        "unclassified": sorted(set(unc_live) | set(unc_snap)),
    }


def _added_side(live, snap) -> Tuple[List[str], List[str]]:
    """Live rows not as in the snapshot: reset rows added since (delete), unclassified ones."""
    delete, unclassified = [], []
    for k, item in live.items():
        kind = classify(k)
        if kind == "reset" and k not in snap:
            delete.append(k)
        elif kind == "unclassified" and (k not in snap or _changed(item, snap[k])):
            unclassified.append(k)
    return delete, unclassified


def _snapshot_side(live, snap) -> Tuple[List[str], List[str]]:
    """Snapshot rows missing or changed live: reset rows to put back, unclassified ones."""
    put, unclassified = [], []
    for k, item in snap.items():
        kind = classify(k)
        if item is None or kind in ("grok", "ignore") or live.get(k) == item:
            continue
        (put if kind == "reset" else unclassified).append(k)
    return put, unclassified


def _changed(live_item: dict, snap_item: Optional[dict]) -> bool:
    return snap_item is not None and live_item != snap_item


def batch_put(ddb, table: str, items: Sequence[dict]) -> int:
    """Put items 25 at a time, retrying UnprocessedItems; raises on persistent failure."""
    for i in range(0, len(items), _BATCH):
        pending = [{"PutRequest": {"Item": it}} for it in items[i : i + _BATCH]]
        for attempt in range(8):
            resp = ddb.batch_write_item(RequestItems={table: pending})
            pending = resp.get("UnprocessedItems", {}).get(table, [])
            if not pending:
                break
            time.sleep(min(2**attempt * 0.1, 5))
        else:
            raise ResetError(
                f"{table}: {len(pending)} put(s) still unprocessed (throttled)"
            )
    return len(items)


# --- modes --------------------------------------------------------------------------------------


def _names(env: str) -> Tuple[str, str, str]:
    return f"{env}-wwii-data-pipeline", f"{env}-wwii-api-cache", f"{env}-wwii-pipeline"


def _git_sha() -> str:
    try:
        return subprocess.run(  # nosec B603 B607 - fixed argv
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def _wait_backup(ddb, arn: str) -> None:
    for _ in range(120):
        status = ddb.describe_backup(BackupArn=arn)["BackupDescription"][
            "BackupDetails"
        ]["BackupStatus"]
        if status == "AVAILABLE":
            return
        time.sleep(5)
    raise ResetError(f"DynamoDB backup {arn} not AVAILABLE after 10 minutes")


def backup(s3, ddb, ecs, env: str, name: str, log=print) -> dict:
    bucket, table, _ = _names(env)
    _refuse_if_busy(ecs, env)
    root = f"{BACKUP_ROOT}/{name}"
    if list_objects(s3, bucket, root) or backup_arn(ddb, table, name):
        raise ResetError(f"backup {name!r} already exists; pick another --name")
    counts = {}
    for p in PREFIXES:
        keys = sorted(list_objects(s3, bucket, p))
        counts[p] = copy_keys(s3, bucket, p, f"{root}/{p}", keys)
        if len(list_objects(s3, bucket, f"{root}/{p}")) != counts[p]:
            raise ResetError(f"{p}: copied object count does not match the source")
        log(f"copied {counts[p]} object(s) {p}/ -> {root}/{p}/")
    arn = ddb.create_backup(TableName=table, BackupName=name)["BackupDetails"][
        "BackupArn"
    ]
    _wait_backup(ddb, arn)
    keys = scan_keys(ddb, table)
    s3.put_object(
        Bucket=bucket, Key=f"{root}/dynamodb_keys.json", Body=json.dumps(keys).encode()
    )
    manifest = {
        "name": name,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "git_sha": _git_sha(),
        "s3_objects": counts,
        "dynamodb_backup_arn": arn,
        "dynamodb_keys": len(keys),
    }
    s3.put_object(
        Bucket=bucket,
        Key=f"{root}/manifest.json",
        Body=json.dumps(manifest, indent=2).encode(),
    )
    log(
        f"DynamoDB backup {arn} AVAILABLE; {len(keys)} key(s) saved; manifest {root}/manifest.json"
    )
    return manifest


def _snapshot(
    s3, ddb, env: str, name: str, table_restore: bool, log
) -> Dict[str, Optional[dict]]:
    bucket, table, _ = _names(env)
    arn = backup_arn(ddb, table, name)
    if table_restore:
        if not arn:
            raise ResetError(f"no AVAILABLE DynamoDB backup named {name!r} for {table}")
        return snapshot_items(ddb, table, arn, name, log)
    body = s3.get_object(Bucket=bucket, Key=f"{BACKUP_ROOT}/{name}/dynamodb_keys.json")[
        "Body"
    ]
    return {k: None for k in json.loads(body.read())}


def make_plan(
    s3, ddb, env: str, name: str, table_restore: bool = True, log=print, snap=None
) -> dict:
    """The restore plan. ``snap`` (the snapshot items of an earlier plan) avoids restoring the
    table backup again for the check after an apply."""
    bucket, table, _ = _names(env)
    if not _exists(s3, bucket, f"{BACKUP_ROOT}/{name}/manifest.json"):
        raise ResetError(
            f"no complete backup at s3://{bucket}/{BACKUP_ROOT}/{name}/ (manifest.json is written "
            "last; a backup without it did not finish - take a new one under another name)"
        )
    if snap is None:
        snap = _snapshot(s3, ddb, env, name, table_restore, log)
    return {
        "s3": s3_plan(s3, bucket, name),
        "ddb": ddb_plan(scan_items(ddb, table), snap),
        "snap": snap,
        "table_restore": table_restore,
    }


def _examples(log, copy_or_put: List[str], delete: List[str]) -> None:
    """The first few keys of each operation, so a difference can be inspected."""
    for label, keys in (("restore", copy_or_put), ("delete", delete)):
        if keys:
            log(f"  {label} e.g. {keys[:5]}")


def report(plan: dict, log=print) -> Tuple[int, int]:
    """Print the plan; return (differences restore fixes, unclassified keys it leaves alone)."""
    n = 0
    for p, ops in plan["s3"].items():
        log(f"s3 {p}/: copy back {len(ops['copy'])}, delete {len(ops['delete'])}")
        _examples(log, ops["copy"], ops["delete"])
        n += len(ops["copy"]) + len(ops["delete"])
    d = plan["ddb"]
    checked = (
        ""
        if plan["table_restore"]
        else " (changed rows NOT checked: --no-table-restore)"
    )
    log(
        f"dynamodb: delete {len(d['delete'])} added row(s), put back {len(d['put'])}{checked}"
    )
    _examples(log, d["put"], d["delete"])
    if d["unclassified"]:
        log(
            f"dynamodb: {len(d['unclassified'])} UNCLASSIFIED changed key(s) left alone, e.g. "
            f"{d['unclassified'][0]!r} - add the prefix to RESET_PREFIXES (or the Grok key rule)"
        )
    trig = triggering_copies(plan)
    if trig:
        log(
            f"WARNING: {len(trig)} parsed file(s) would be copied back, which starts Phase 2: {trig[:3]}"
        )
    return n + len(d["delete"]) + len(d["put"]), len(d["unclassified"])


def apply(s3, ddb, env: str, name: str, plan: dict, log=print) -> None:
    bucket, table, _ = _names(env)
    for p, ops in plan["s3"].items():
        copy_keys(s3, bucket, f"{BACKUP_ROOT}/{name}/{p}", p, ops["copy"])
        delete_keys(s3, bucket, p, ops["delete"])
    d = plan["ddb"]
    _batch_delete(ddb, table, [{KEY: {"S": k}} for k in d["delete"]])
    batch_put(ddb, table, [plan["snap"][k] for k in d["put"]])
    log("applied: S3 copied back + deleted, DynamoDB rows deleted + put back")


def run(mode: str, env: str, name: str, region: str, opts: dict) -> int:
    """``opts``: apply, table_restore, allow_phase2_trigger."""
    s3, ddb, ecs = (
        boto3.client(s, region_name=region) for s in ("s3", "dynamodb", "ecs")
    )
    if mode == "backup":
        backup(s3, ddb, ecs, env, name)
        return 0
    _refuse_if_busy(ecs, env)
    plan = make_plan(s3, ddb, env, name, opts["table_restore"])
    diffs, unclassified = report(plan)
    if mode == "verify":
        ok = not diffs and not unclassified
        print(
            "MATCHES the backup"
            if ok
            else f"DIFFERS from the backup ({diffs + unclassified})"
        )
        return 0 if ok else 1
    if not opts["apply"]:
        print("dry run: nothing changed (add --apply to restore)")
        return 0
    return _restore(s3, ddb, env, name, plan, opts)


def _restore(s3, ddb, env: str, name: str, plan: dict, opts: dict) -> int:
    if triggering_copies(plan) and not opts["allow_phase2_trigger"]:
        raise ResetError(
            "the restore would copy back parsed chapter files, which starts Phase 2; re-run with "
            "--allow-phase2-trigger if that is wanted"
        )
    apply(s3, ddb, env, name, plan)
    left, unclassified = report(
        make_plan(s3, ddb, env, name, opts["table_restore"], snap=plan["snap"])
    )
    if left or unclassified:
        print(
            f"NOT clean: {left} difference(s) left, {unclassified} unclassified key(s)"
        )
        return 1
    print("restored: live data matches the backup")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("mode", choices=("backup", "restore", "verify"))
    ap.add_argument("--name", required=True)
    ap.add_argument("--env", default="dev")
    ap.add_argument("--region", default="us-east-1")
    ap.add_argument("--apply", action="store_true", help="restore: really change data")
    ap.add_argument(
        "--no-table-restore",
        action="store_true",
        help="do not restore the table backup to compare changed rows (only delete added rows)",
    )
    ap.add_argument(
        "--allow-phase2-trigger",
        action="store_true",
        help="restore: allow copying back parsed chapter files (starts Phase 2)",
    )
    ap.add_argument("--allow-nondev", action="store_true")
    args = ap.parse_args(argv)
    if not _NAME_RE.fullmatch(args.name) or not _ENV_RE.fullmatch(args.env):
        raise SystemExit("invalid --name or --env")
    if args.env not in SAFE_ENVS and not args.allow_nondev:
        raise SystemExit(
            f"refused for env {args.env!r}; pass --allow-nondev if certain"
        )
    try:
        opts = {
            "apply": args.apply,
            "table_restore": not args.no_table_restore,
            "allow_phase2_trigger": args.allow_phase2_trigger,
        }
        return run(args.mode, args.env, args.name, args.region, opts)
    except ResetError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())

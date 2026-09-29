"""OCR SPOT controller (backlog #2, Option B — hourly spot-retry / 48h on-demand cap).

Operator design: prefer SPOT for GPU OCR (max savings); if a job can't get spot
capacity for a while, the CUSTOM controller routes it to an on-demand fallback
queue, capped at 48h cumulative on-demand time — then it's abandoned/alerted, not
looped forever.

Runs on an hourly EventBridge schedule. Each run:
  1. WARN if running spot vCPU >= 80% of the live G/VT spot quota (L-3819A6DF),
     with an actionable quota-increase link (the quota is soft/raisable).
  2. For each job stuck RUNNABLE on the SPOT queue longer than SPOT_WAIT_SECS:
     resubmit an equivalent job to the ON-DEMAND queue, terminate the spot job,
     and record the move in ocrctl#{ident} (so it's not re-moved / double-OCR'd).
  3. For each controller-tracked on-demand job past 48h cumulative: terminate +
     alert (the 48h cap) rather than pay indefinitely.

Idempotent: a job identity (jobName) is moved at most once; the ocrctl# record is
the source of truth. Self-contained; no heavy imports.
"""

import json
import logging
import os
import time

import boto3

logger = logging.getLogger()
logger.setLevel(os.getenv("LOG_LEVEL", "INFO"))

ENV_NAME = os.getenv("ENV_NAME", "dev")
REGION = os.getenv("AWS_REGION", "us-east-1")
CACHE_TABLE = os.getenv("CACHE_TABLE", f"{ENV_NAME}-wwii-api-cache")
SPOT_QUEUE = os.getenv("OCR_SPOT_QUEUE", f"{ENV_NAME}-wwii-chandra-gpu")
ONDEMAND_QUEUE = os.getenv(
    "OCR_ONDEMAND_QUEUE", f"{ENV_NAME}-wwii-chandra-gpu-ondemand"
)
JOB_DEF = os.getenv("OCR_JOB_DEF", f"{ENV_NAME}-wwii-chandra")
NOTIFICATION_TOPIC_ARN = os.getenv("NOTIFICATION_TOPIC_ARN", "")

# A spot job RUNNABLE longer than this (no capacity) is routed to on-demand.
SPOT_WAIT_SECS = int(os.getenv("OCR_SPOT_WAIT_SECS", "3600"))  # 1h
# Cumulative on-demand time cap per job.
ONDEMAND_CAP_SECS = int(os.getenv("OCR_ONDEMAND_CAP_SECS", str(48 * 3600)))  # 48h
# G/VT Spot Instance Requests quota (soft, raisable).
_SPOT_QUOTA_CODE = "L-3819A6DF"
_PER_JOB_VCPU = 4


def _batch():
    return boto3.client("batch", region_name=REGION)


def _table():
    return boto3.resource("dynamodb", region_name=REGION).Table(CACHE_TABLE)


def _notify(subject: str, message: str) -> None:
    if not NOTIFICATION_TOPIC_ARN:
        return
    try:
        boto3.client("sns", region_name=REGION).publish(
            TopicArn=NOTIFICATION_TOPIC_ARN, Subject=subject, Message=message
        )
    except Exception as e:  # pragma: no cover - defensive
        logger.warning("notify failed: %s", e)


def _spot_quota_vcpus() -> int:
    try:
        sq = boto3.client("service-quotas", region_name=REGION)
        return int(
            sq.get_service_quota(ServiceCode="ec2", QuotaCode=_SPOT_QUOTA_CODE)[
                "Quota"
            ]["Value"]
        )
    except Exception as e:
        logger.warning("spot quota lookup failed: %s", e)
        return 0


def _list_jobs(queue: str, status: str) -> list:
    jobs = []
    paginator = _batch().get_paginator("list_jobs")
    for page in paginator.paginate(jobQueue=queue, jobStatus=status):
        jobs.extend(page.get("jobSummaryList", []))
    return jobs


def _warn_if_near_quota() -> None:
    """§5.0: warn when running spot vCPU >= 80% of the live spot quota."""
    quota = _spot_quota_vcpus()
    if quota <= 0:
        return
    running = _list_jobs(SPOT_QUEUE, "RUNNING") + _list_jobs(SPOT_QUEUE, "STARTING")
    used_vcpu = len(running) * _PER_JOB_VCPU
    if used_vcpu >= 0.8 * quota:
        _notify(
            "WWII Pipeline: OCR spot near quota ceiling",
            (
                f"OCR spot usage {used_vcpu} vCPU is >=80% of the G/VT spot quota "
                f"({quota} vCPU). Request an increase to raise OCR throughput: "
                f"https://console.aws.amazon.com/servicequotas/home/services/ec2/quotas/"
                f"{_SPOT_QUOTA_CODE} (quota {_SPOT_QUOTA_CODE})."
            ),
        )


def _resubmit_to_ondemand(job: dict) -> None:
    """Resubmit a spot-starved job to the on-demand queue (once), terminate the
    spot job, and record the move. Reuses the job's container command so the same
    input/output/page-range is OCR'd."""
    name = job["jobName"]
    ident = f"ocrctl#{name}"
    table = _table()
    # Claim the move atomically — a job is moved at most once.
    try:
        table.put_item(
            Item={
                "cache_key": ident,
                "ondemand_started_at": int(time.time()),
                "spot_job_id": job["jobId"],
            },
            ConditionExpression="attribute_not_exists(cache_key)",
        )
    except table.meta.client.exceptions.ConditionalCheckFailedException:
        return  # already moved
    batch = _batch()
    # Recover the original container command to replicate the job.
    desc = batch.describe_jobs(jobs=[job["jobId"]])["jobs"]
    cmd = desc[0].get("container", {}).get("command", []) if desc else []
    try:
        resp = batch.submit_job(
            jobName=name[:128],
            jobQueue=ONDEMAND_QUEUE,
            jobDefinition=JOB_DEF,
            containerOverrides={"command": cmd} if cmd else {},
        )
        batch.terminate_job(jobId=job["jobId"], reason="spot-starved -> on-demand (§2)")
        table.update_item(
            Key={"cache_key": ident},
            UpdateExpression="SET ondemand_job_id = :j",
            ExpressionAttributeValues={":j": resp["jobId"]},
        )
        logger.info("Routed spot-starved %s -> on-demand %s", name, resp["jobId"])
    except Exception as e:
        table.delete_item(Key={"cache_key": ident})  # release for retry
        logger.error("Failed to route %s to on-demand: %s", name, e)


def _route_spot_starved() -> int:
    """Route jobs stuck RUNNABLE on the spot queue > SPOT_WAIT_SECS to on-demand."""
    now = int(time.time())
    routed = 0
    for job in _list_jobs(SPOT_QUEUE, "RUNNABLE"):
        created = int(job.get("createdAt", now * 1000)) / 1000  # ms -> s
        if now - created >= SPOT_WAIT_SECS:
            _resubmit_to_ondemand(job)
            routed += 1
    return routed


def _enforce_ondemand_cap() -> int:
    """Terminate + alert on-demand jobs past the 48h cumulative cap."""
    now = int(time.time())
    capped = 0
    table = _table()
    resp = table.scan(
        FilterExpression="begins_with(cache_key, :p) AND attribute_exists(ondemand_job_id)",
        ExpressionAttributeValues={":p": "ocrctl#"},
    )
    for item in resp.get("Items", []):
        started = int(item.get("ondemand_started_at", now))
        if now - started >= ONDEMAND_CAP_SECS:
            jid = item.get("ondemand_job_id")
            try:
                _batch().terminate_job(jobId=jid, reason="48h on-demand cap (§2)")
                _notify(
                    "WWII Pipeline: OCR job hit 48h on-demand cap",
                    f"OCR job {item['cache_key']} exceeded the 48h on-demand cap and "
                    f"was terminated. It needs investigation (persistent spot "
                    f"unavailability or a stuck doc).",
                )
                capped += 1
            except Exception as e:
                logger.warning("cap-terminate failed for %s: %s", jid, e)
    return capped


def handler(_event, _context):
    """Hourly: warn near quota, route spot-starved -> on-demand, enforce 48h cap."""
    _warn_if_near_quota()
    routed = _route_spot_starved()
    capped = _enforce_ondemand_cap()
    logger.info("OCR controller: routed=%d capped=%d", routed, capped)
    return {"routed": routed, "capped": capped}

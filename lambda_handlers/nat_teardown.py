"""NAT/endpoint TEARDOWN decider — the SOLE owner of networking teardown.

Lifecycle model (owner directive 2026-10-15):
  * Phases 1/2/3 NEVER tear down NAT/endpoints — they may only REQUEST start via the
    nat_manager Lambda (action=create).
  * nat_manager owns bring-up (action=create) + the actual delete primitive
    (action=delete), but DELETE is invoked ONLY from here.
  * THIS Lambda is the only teardown decider. It fires on a 4-HOUR cron. On each run:
      - If the DynamoDB queue has work being processed OR waiting to be processed, OR any
        container is running an ongoing process  ->  do NOTHING.
      - If a container is on with ongoing work, re-check every 10 MINUTES (a one-shot
        self-rescheduled EventBridge Scheduler invoke) until idle — don't wait for the
        next 4h tick.
      - Only when the queue is empty AND no container is processing  ->  tear down NAT +
        endpoints (nat_manager action=delete).

Demand = `nat_manager._nat_demand_present()` (single source of truth): live nat#lease#
entries, running non-openserp ECS pipeline tasks, in-flight OCR Batch jobs, and
pending/ready Grok batch_job# rows. Fail-safe: on ANY uncertainty, keep NAT (never tear
down on error).
"""

import logging
import os

logger = logging.getLogger(__name__)
logger.setLevel(os.getenv("LOG_LEVEL", "INFO"))

ENV_NAME = os.getenv("ENV_NAME", "dev")
REGION = os.getenv("AWS_REGION", "us-east-1")
RECHECK_MINUTES = int(os.getenv("NAT_TEARDOWN_RECHECK_MINUTES", "10"))
_RECHECK_SCHEDULE = f"{ENV_NAME}-wwii-nat-teardown-recheck"


def _nat_present(ec2) -> bool:
    """True if a NAT gateway exists (available/pending) — nothing to tear down if not."""
    resp = ec2.describe_nat_gateways(
        Filters=[
            {"Name": "tag:Name", "Values": [f"{ENV_NAME}-nat"]},
            {"Name": "state", "Values": ["available", "pending"]},
        ]
    )
    return bool(resp.get("NatGateways", []))


def _schedule_recheck() -> None:
    """Self-schedule a ONE-SHOT recheck in RECHECK_MINUTES via EventBridge Scheduler, so
    while a container is still processing we poll at 10-min cadence (not the 4h cron) and
    tear down promptly once idle. ActionAfterCompletion=DELETE cleans the one-shot up.
    """
    import datetime

    import boto3

    account = boto3.client("sts", region_name=REGION).get_caller_identity()["Account"]
    fn_arn = f"arn:aws:lambda:{REGION}:{account}:function:{ENV_NAME}-wwii-nat-teardown"
    role_arn = os.environ.get(
        "SCHEDULER_ROLE_ARN",
        f"arn:aws:iam::{account}:role/{ENV_NAME}-wwii-scheduler-role",
    )
    run_at = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(
        minutes=RECHECK_MINUTES
    )
    scheduler = boto3.client("scheduler", region_name=REGION)
    kwargs = dict(
        Name=_RECHECK_SCHEDULE,
        ScheduleExpression=f"at({run_at.strftime('%Y-%m-%dT%H:%M:%S')})",
        ScheduleExpressionTimezone="UTC",
        FlexibleTimeWindow={"Mode": "OFF"},
        Target={
            "Arn": fn_arn,
            "RoleArn": role_arn,
            "Input": '{"source": "recheck"}',
        },
        ActionAfterCompletion="DELETE",
    )
    try:
        scheduler.create_schedule(**kwargs)
        logger.info("Scheduled NAT-teardown recheck in %d min", RECHECK_MINUTES)
    except scheduler.exceptions.ConflictException:
        # A one-shot with this name already exists — most often the just-FIRED recheck
        # still mid-cleanup (ActionAfterCompletion=DELETE ordering vs this re-invocation
        # is not guaranteed). UPDATE it to the new run time so the 10-min chain re-arms
        # DETERMINISTICALLY (leaving it would break the chain → next eval only at the 4h
        # cron). If it vanished between create and update, create once more.
        try:
            scheduler.update_schedule(**kwargs)
            logger.info("Re-armed NAT-teardown recheck (+%d min)", RECHECK_MINUTES)
        except scheduler.exceptions.ResourceNotFoundException:
            try:
                scheduler.create_schedule(**kwargs)
            except Exception as e:  # noqa: BLE001
                logger.warning("Failed to re-create NAT-teardown recheck: %s", e)
    except (
        Exception
    ) as e:  # noqa: BLE001 - scheduling failure must not crash the decider
        logger.warning("Failed to schedule NAT-teardown recheck: %s", e)


def _teardown() -> dict:
    """Invoke nat_manager action=delete (the only place delete is invoked)."""
    import boto3

    lam = boto3.client("lambda", region_name=REGION)
    resp = lam.invoke(
        FunctionName=f"{ENV_NAME}-wwii-nat-manager",
        InvocationType="RequestResponse",
        Payload=b'{"action": "delete"}',
    )
    logger.info("NAT-teardown: queue empty + no container — invoked nat_manager delete")
    return {"action": "delete", "nat_manager_status": resp.get("StatusCode")}


def handler(event, _context=None):
    """4h cron (and 10-min self-recheck) teardown decider. Fail-safe: never tears down on
    uncertainty/error."""
    try:
        import boto3

        from lambda_handlers.nat_manager import _nat_demand_present

        ec2 = boto3.client("ec2", region_name=REGION)
        if not _nat_present(ec2):
            logger.info("NAT-teardown: no NAT present — nothing to do")
            return {"action": "none", "reason": "nat already down"}

        if _nat_demand_present():
            # Queue has work (processing/waiting) OR a container is running an ongoing
            # process -> do NOT tear down; recheck in 10 min rather than wait 4h.
            logger.info(
                "NAT-teardown: demand present (queue work / running container) — "
                "rechecking in %d min",
                RECHECK_MINUTES,
            )
            _schedule_recheck()
            return {"action": "recheck", "reason": "nat demand present"}

        # Queue empty AND no container processing -> tear down.
        return _teardown()
    except (
        Exception
    ) as e:  # noqa: BLE001 - a decider bug must never crash or wrongly reap
        logger.error("NAT-teardown: skipped on error (no teardown): %s", e)
        return {"action": "none", "reason": f"error: {e}"}

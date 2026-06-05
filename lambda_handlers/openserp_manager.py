"""Lambda handler for idle monitoring and cost-saving teardown.

Triggered by EventBridge rule every 10 minutes.
Checks for running ECS tasks — if none for 30 minutes, tears down:
  - ECS OpenSERP service (scale to 0)
  - NAT Gateway + release Elastic IP
  - Internal ALB
Resources are discovered dynamically by tag, not from config.
"""

import logging
import os
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)
logger.setLevel(os.getenv("LOG_LEVEL", "INFO"))

ENV_NAME = os.getenv("ENV_NAME", "dev")
IDLE_MINUTES = int(os.getenv("IDLE_MINUTES", "30"))
SSM_PREFIX = f"/{ENV_NAME}-wwii-pipeline"


def handler(event, _context):
    """Check cluster activity and tear down if idle."""
    import boto3

    region = os.getenv("AWS_REGION", "us-east-1")
    cluster = f"{ENV_NAME}-wwii-pipeline"
    service = f"{ENV_NAME}-wwii-openserp"

    ecs = boto3.client("ecs", region_name=region)

    # Guardrail: force teardown if NAT has been up >2h with no pipeline tasks
    if _nat_too_old(max_hours=2):
        running = ecs.list_tasks(cluster=cluster, desiredStatus="RUNNING")
        task_arns = running.get("taskArns", [])
        # Only openserp or nothing — no pipeline work happening
        pipeline_tasks = [t for t in task_arns if "openserp" not in t]
        if not pipeline_tasks:
            logger.warning(
                "NAT has been up >2h with no pipeline tasks — force teardown"
            )
            _teardown(ecs, cluster, service, region)
            return {"action": "force_teardown", "reason": "NAT age exceeded 2h"}

    # Check for any running tasks in the cluster
    running = ecs.list_tasks(cluster=cluster, desiredStatus="RUNNING")
    task_count = len(running.get("taskArns", []))

    if task_count > 0:
        logger.info("Active: %d running tasks — skipping teardown", task_count)
        return {"action": "none", "tasks": task_count}

    # Check if OpenSERP service is already at 0
    resp = ecs.describe_services(cluster=cluster, services=[service])
    services = resp.get("services", [])
    if not services or services[0].get("desiredCount", 0) == 0:
        # Check if NAT was recently created (task may be starting)
        if _nat_recently_created():
            logger.info(
                "NAT created recently — task may be starting, skipping teardown"
            )
            return {"action": "none", "reason": "nat recently created"}
        if _networking_already_down():
            return {"action": "none", "reason": "already down"}
        _teardown(ecs, cluster, service, region)
        logger.info("Idle: no tasks, service at 0 — cleaned up NAT/ALB")
        return {"action": "cleanup", "reason": "already idle, cleaned resources"}

    # Service is up but no tasks — check if it's been idle long enough
    # Use the last task stop time as the idle marker
    stopped = ecs.list_tasks(cluster=cluster, desiredStatus="STOPPED")
    last_active = _get_last_task_time(ecs, cluster, stopped.get("taskArns", []))

    now = datetime.now(timezone.utc)
    idle_since = now - timedelta(minutes=IDLE_MINUTES)

    if last_active and last_active > idle_since:
        minutes_ago = int((now - last_active).total_seconds() / 60)
        logger.info(
            "Last task stopped %d min ago — waiting for %d min threshold",
            minutes_ago,
            IDLE_MINUTES,
        )
        return {"action": "none", "last_active_min_ago": minutes_ago}

    # Idle long enough — tear down
    logger.info("Idle for %d+ minutes — tearing down", IDLE_MINUTES)
    _teardown(ecs, cluster, service, region)
    return {"action": "teardown", "reason": f"idle {IDLE_MINUTES}+ min"}


def _get_last_task_time(ecs, cluster, task_arns):
    """Get the most recent task stop time."""
    if not task_arns:
        return None
    # Check last 5 stopped tasks
    resp = ecs.describe_tasks(cluster=cluster, tasks=task_arns[:5])
    latest = None
    for task in resp.get("tasks", []):
        stopped = task.get("stoppedAt")
        if stopped and (latest is None or stopped > latest):
            latest = stopped
    return latest


def _nat_recently_created(minutes=5):
    """Check if NAT was created within the last N minutes."""
    import boto3

    ec2 = boto3.client("ec2", region_name=os.getenv("AWS_REGION", "us-east-1"))
    try:
        resp = ec2.describe_nat_gateways(
            Filters=[
                {"Name": "tag:Name", "Values": [f"{ENV_NAME}-nat"]},
                {"Name": "state", "Values": ["available", "pending"]},
            ]
        )
        for gw in resp.get("NatGateways", []):
            create_time = gw.get("CreateTime")
            if create_time:
                age = (datetime.now(timezone.utc) - create_time).total_seconds()
                if age < minutes * 60:
                    return True
    except Exception:
        pass
    return False


def _nat_too_old(max_hours=2):
    """Return True if NAT has been available for longer than max_hours."""
    import boto3

    ec2 = boto3.client("ec2", region_name=os.getenv("AWS_REGION", "us-east-1"))
    try:
        resp = ec2.describe_nat_gateways(
            Filters=[
                {"Name": "tag:Name", "Values": [f"{ENV_NAME}-nat"]},
                {"Name": "state", "Values": ["available"]},
            ]
        )
        for gw in resp.get("NatGateways", []):
            create_time = gw.get("CreateTime")
            if create_time:
                age = (datetime.now(timezone.utc) - create_time).total_seconds()
                if age > max_hours * 3600:
                    return True
    except Exception:
        pass
    return False


def _networking_already_down():
    """Return True if no NAT gateway exists."""
    import boto3

    ec2 = boto3.client("ec2", region_name=os.getenv("AWS_REGION", "us-east-1"))
    try:
        resp = ec2.describe_nat_gateways(
            Filters=[
                {"Name": "tag:Name", "Values": [f"{ENV_NAME}-nat"]},
                {"Name": "state", "Values": ["available", "pending"]},
            ]
        )
        return len(resp.get("NatGateways", [])) == 0
    except Exception:
        return True


def _teardown(ecs, cluster, service, region):
    """Scale OpenSERP to 0 and delete dynamic networking stack."""
    import boto3

    # 1. Scale OpenSERP to 0
    try:
        ecs.update_service(cluster=cluster, service=service, desiredCount=0)
        logger.info("Scaled OpenSERP service to 0")
    except Exception as e:
        logger.warning("Failed to scale service: %s", e)

    # 2. Delete networking stack (NAT, ALB, VPC endpoints)
    try:
        boto3.client("lambda", region_name=region).invoke(
            FunctionName=f"{ENV_NAME}-wwii-nat-manager",
            InvocationType="RequestResponse",
            Payload=b'{"action": "delete"}',
        )
        logger.info("Networking stack delete invoked")
    except Exception as e:
        logger.warning("Failed to invoke networking delete: %s", e)

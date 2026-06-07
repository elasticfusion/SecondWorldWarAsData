"""AWS networking operations for ECS entrypoint (NAT, OpenSERP, locks)."""

import json
import logging
import os
import time
from pathlib import Path

import boto3

logger = logging.getLogger(__name__)

BUCKET = os.environ.get("S3_BUCKET", "")
REGION = os.environ.get("AWS_DEFAULT_REGION", os.environ.get("AWS_REGION", "us-east-1"))

PHASE_SUFFIXES = {
    "phase1_parse.py": "phase1-parse",
    "phase2_extract.py": "phase2-extract",
    "phase3_enrich_data.py": "phase3-enrich",
    "import_to_dynamodb.py": "import",
}


def _s3_client():
    return boto3.client("s3", region_name=REGION)


def _get_account_id() -> str:
    """Get AWS account ID from STS."""
    return boto3.client("sts", region_name=REGION).get_caller_identity()["Account"]


def _load_secrets():
    """Fetch GROK_API_KEY from Secrets Manager if not already set."""
    if os.environ.get("GROK_API_KEY"):
        return
    secret_id = os.environ.get("SECRETS_ID", "")
    if not secret_id:
        return
    try:
        sm = boto3.client("secretsmanager", region_name=REGION)
        resp = sm.get_secret_value(SecretId=secret_id)
        os.environ["GROK_API_KEY"] = resp["SecretString"]
        logger.info("Loaded API key from Secrets Manager")
    except Exception as e:
        logger.error("Failed to load secret %s: %s", secret_id, e)


def _cancel_stale_teardown() -> None:
    """Cancel any pending delayed networking teardown from a previous task."""
    try:
        env = os.environ.get("ENV_NAME", "dev")
        scheduler = boto3.client("scheduler", region_name=REGION)
        scheduler.delete_schedule(Name=f"{env}-wwii-delayed-teardown")
        logger.info("Cancelled stale delayed teardown")
    except Exception:
        pass  # No schedule exists — normal


def _get_openserp_alb_dns() -> str:
    """Find the OpenSERP task private IP. Returns empty string if not found."""
    try:
        env = os.environ.get("ENV_NAME", "dev")
        cluster = f"{env}-wwii-pipeline"
        ecs = boto3.client("ecs", region_name=REGION)
        tasks = ecs.list_tasks(
            cluster=cluster, serviceName=f"{env}-wwii-openserp", desiredStatus="RUNNING"
        )
        task_arns = tasks.get("taskArns", [])
        if not task_arns:
            logger.warning("OpenSERP IP discovery: no running tasks found")
            return ""
        resp = ecs.describe_tasks(cluster=cluster, tasks=task_arns[:1])
        for task in resp.get("tasks", []):
            for attachment in task.get("attachments", []):
                for detail in attachment.get("details", []):
                    if detail.get("name") == "privateIPv4Address":
                        return detail["value"]
        logger.debug(
            "OpenSERP IP discovery: task found but no privateIPv4Address in attachments"
        )
    except Exception as e:
        logger.warning("OpenSERP IP discovery failed: %s", e)
    return ""


def _start_openserp_if_needed(phase_script: str) -> None:
    """Scale OpenSERP service to 1 for Phase 2/3 and discover its IP."""
    if "phase1" in phase_script:
        return
    try:
        env = os.environ.get("ENV_NAME", "dev")
        cluster = f"{env}-wwii-pipeline"
        service = f"{env}-wwii-openserp"
        ecs = boto3.client("ecs", region_name=REGION)
        resp = ecs.describe_services(cluster=cluster, services=[service])
        svc = resp.get("services", [{}])[0]
        if svc.get("runningCount", 0) > 0:
            # Already running — just discover IP
            ip = _get_openserp_alb_dns()
            if ip:
                _patch_openserp_url(ip)
                logger.info("OpenSERP already running at %s:7001", ip)
            return

        if svc.get("desiredCount", 0) == 0:
            # Ensure VPC endpoints exist (needed for ECR image pull)
            try:
                boto3.client("lambda", region_name=REGION).invoke(
                    FunctionName=f"{env}-wwii-nat-manager",
                    InvocationType="RequestResponse",
                    Payload=b'{"action": "create"}',
                )
            except Exception as e:
                logger.warning("nat_manager invoke failed: %s", e)

            ecs.update_service(cluster=cluster, service=service, desiredCount=1)
            logger.info("Started OpenSERP service (scaling from 0 to 1)")
        else:
            # Service already desired=1 but not running — force new deployment
            ecs.update_service(
                cluster=cluster,
                service=service,
                desiredCount=1,
                forceNewDeployment=True,
            )
            logger.info("Forced new OpenSERP deployment (was stuck)")

        # Wait for task to be running and get its IP
        import time

        for _ in range(72):  # 12 min max
            ip = _get_openserp_alb_dns()
            if ip:
                _patch_openserp_url(ip)
                logger.info("OpenSERP running at %s:7001", ip)
                return
            time.sleep(10)
        logger.warning("OpenSERP service did not start in 12 min")
    except Exception as e:
        logger.warning("Failed to start OpenSERP: %s", e)


def _patch_openserp_url(ip: str) -> None:
    """Patch config.yaml with OpenSERP task IP."""
    import yaml

    config_path = Path("/app/config.yaml")
    with open(config_path, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)
    config.setdefault("external_maps", {})["openserp_url"] = f"http://{ip}:7001"
    config.setdefault("supplemental_material", {})["use_openserp"] = True
    with open(config_path, "w", encoding="utf-8") as f:
        yaml.dump(config, f, default_flow_style=False)


def _stop_openserp_if_running(phase_script: str) -> None:
    """Scale OpenSERP to 0 after Phase 2/3 completes."""
    if "phase1" in phase_script:
        return
    try:
        env = os.environ.get("ENV_NAME", "dev")
        cluster = f"{env}-wwii-pipeline"
        service = f"{env}-wwii-openserp"
        ecs = boto3.client("ecs", region_name=REGION)
        ecs.update_service(cluster=cluster, service=service, desiredCount=0)
        logger.info("Scaled OpenSERP to 0")
    except Exception as e:
        logger.warning("Failed to scale OpenSERP to 0: %s", e)


def _acquire_lock(phase_script: str) -> bool:
    """Acquire a DynamoDB lock for this phase. Returns True if acquired."""
    family_suffix = PHASE_SUFFIXES.get(phase_script)
    if not family_suffix:
        return True
    env_name = os.environ.get("ENV_NAME", "dev")
    lock_key = f"lock#{env_name}-wwii-{family_suffix}"
    logger.info("Acquiring DynamoDB lock: %s", lock_key)
    try:
        import time

        table_name = os.environ.get("CACHE_TABLE", "dev-wwii-api-cache")
        table = boto3.resource("dynamodb", region_name=REGION).Table(table_name)
        table.put_item(
            Item={
                "cache_key": lock_key,
                "response": str(int(time.time())),
                "ttl": int(time.time()) + 86400,
            },
            ConditionExpression="attribute_not_exists(cache_key)",
        )
        logger.info("Acquired DynamoDB lock: %s", lock_key)
        return True
    except table.meta.client.exceptions.ConditionalCheckFailedException:
        logger.warning("DynamoDB lock already held: %s", lock_key)
        return False
    except Exception as e:
        logger.warning("Lock check failed: %s, proceeding anyway", e)
        return True


def _remove_lock(phase_script: str) -> None:
    """Remove the DynamoDB lock for this phase."""
    family_suffix = PHASE_SUFFIXES.get(phase_script)
    if not family_suffix:
        return
    env_name = os.environ.get("ENV_NAME", "dev")
    lock_key = f"lock#{env_name}-wwii-{family_suffix}"
    try:
        table_name = os.environ.get("CACHE_TABLE", "dev-wwii-api-cache")
        table = boto3.resource("dynamodb", region_name=REGION).Table(table_name)
        table.delete_item(Key={"cache_key": lock_key})
        logger.info("Removed lock: %s", lock_key)
    except Exception as e:
        logger.warning("Failed to remove lock %s: %s", lock_key, e)


def _clear_all_locks() -> None:
    """Clear all pipeline locks at start of new run."""
    try:
        table_name = os.environ.get("CACHE_TABLE", "dev-wwii-api-cache")
        table = boto3.resource("dynamodb", region_name=REGION).Table(table_name)
        resp = table.scan(
            FilterExpression="begins_with(cache_key, :prefix)",
            ExpressionAttributeValues={":prefix": "lock#"},
            ProjectionExpression="cache_key",
        )
        for item in resp.get("Items", []):
            table.delete_item(Key={"cache_key": item["cache_key"]})
            logger.info("Cleared lock: %s", item["cache_key"])
    except Exception as e:
        logger.warning("Failed to clear locks: %s", e)

    # Reset dedup review status so Phase 3 requires approval
    try:
        s3 = _s3_client()
        s3.put_object(
            Bucket=BUCKET,
            Key="dedup/review_status.json",
            Body=json.dumps({"complete": False, "reviewed": {}}).encode(),
        )
        logger.info("Reset dedup review status")
    except Exception as e:
        logger.warning("Failed to reset dedup status: %s", e)

    # Clear stale manifest
    try:
        table.delete_item(Key={"cache_key": "manifest#phase2"})
        logger.info("Cleared manifest")
    except Exception:
        pass


def _schedule_delayed_teardown(delay_minutes: int = 30) -> None:
    """Schedule networking teardown after a delay via EventBridge Scheduler.

    If Phase 3 launches before the delay expires, the trigger Lambda
    cancels this schedule. Avoids churn when dedup review is fast.
    """
    import datetime

    env = os.environ.get("ENV_NAME", "dev")
    schedule_name = f"{env}-wwii-delayed-teardown"
    run_at = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(
        minutes=delay_minutes
    )
    nat_fn_arn = (
        f"arn:aws:lambda:{REGION}:{_get_account_id()}:function:{env}-wwii-nat-manager"
    )
    role_arn = os.environ.get(
        "SCHEDULER_ROLE_ARN",
        f"arn:aws:iam::{_get_account_id()}:role/{env}-wwii-scheduler-role",
    )
    try:
        scheduler = boto3.client("scheduler", region_name=REGION)
    except Exception as e:
        logger.warning("Failed to create scheduler client: %s", e)
        return
    try:
        scheduler.create_schedule(
            Name=schedule_name,
            ScheduleExpression=f"at({run_at.strftime('%Y-%m-%dT%H:%M:%S')})",
            ScheduleExpressionTimezone="UTC",
            FlexibleTimeWindow={"Mode": "OFF"},
            Target={
                "Arn": nat_fn_arn,
                "RoleArn": role_arn,
                "Input": '{"action": "delete"}',
            },
            ActionAfterCompletion="DELETE",
        )
        logger.info("Scheduled networking teardown in %d minutes", delay_minutes)
    except scheduler.exceptions.ConflictException:
        # Schedule already exists — update it
        try:
            scheduler.update_schedule(
                Name=schedule_name,
                ScheduleExpression=f"at({run_at.strftime('%Y-%m-%dT%H:%M:%S')})",
                ScheduleExpressionTimezone="UTC",
                FlexibleTimeWindow={"Mode": "OFF"},
                Target={
                    "Arn": nat_fn_arn,
                    "RoleArn": role_arn,
                    "Input": '{"action": "delete"}',
                },
                ActionAfterCompletion="DELETE",
            )
            logger.info(
                "Updated delayed teardown schedule to %d minutes", delay_minutes
            )
        except Exception as e:
            logger.warning("Failed to update delayed teardown: %s", e)
    except Exception as e:
        logger.warning("Failed to schedule delayed teardown: %s", e)


def _teardown_networking() -> None:
    """Scale down OpenSERP and invoke nat_manager to delete NAT + VPC endpoints."""
    try:
        env = os.environ.get("ENV_NAME", "dev")
        ecs = boto3.client("ecs", region_name=REGION)
        ecs.update_service(
            cluster=f"{env}-wwii-pipeline",
            service=f"{env}-wwii-openserp",
            desiredCount=0,
        )
        logger.info("Scaled OpenSERP to 0")
    except Exception as e:
        logger.warning("Failed to scale OpenSERP: %s", e)
    try:
        env = os.environ.get("ENV_NAME", "dev")
        lam = boto3.client("lambda", region_name=REGION)
        lam.invoke(
            FunctionName=f"{env}-wwii-nat-manager",
            InvocationType="RequestResponse",
            Payload=json.dumps({"action": "delete"}).encode(),
        )
        logger.info("Networking torn down (NAT + VPC endpoints)")
    except Exception as e:
        logger.warning("Failed to tear down networking: %s", e)

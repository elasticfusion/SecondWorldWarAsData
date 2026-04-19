"""OpenSERP ECS task management — start, stop, health check."""

import logging
import time

import requests

logger = logging.getLogger(__name__)


def _get_ecs_client(region: str):
    """Get boto3 ECS client."""
    import boto3

    return boto3.client("ecs", region_name=region)


def get_openserp_url(config: dict) -> str:
    """Get the OpenSERP URL — local or ECS-based.

    If aws.enabled is false, returns localhost URL.
    If aws.enabled is true, ensures ECS task is running and returns ALB URL.
    """
    aws = config.get("aws", {})
    if not aws.get("enabled"):
        return config.get("external_maps", {}).get(
            "openserp_url", "http://localhost:7001"
        )

    openserp_cfg = aws.get("openserp", {})
    cluster = openserp_cfg["cluster"]
    service = openserp_cfg["service"]
    region = aws.get("region", "us-east-1")
    timeout = openserp_cfg.get("startup_timeout", 120)

    ecs = _get_ecs_client(region)

    # Check if service has running tasks
    resp = ecs.describe_services(cluster=cluster, services=[service])
    svc = resp["services"][0]
    running = svc.get("runningCount", 0)

    if running == 0:
        logger.info("OpenSERP not running — starting ECS task")
        ecs.update_service(cluster=cluster, service=service, desiredCount=1)
        _wait_for_healthy(cluster, service, ecs, timeout)

    # Return the ALB URL from service's load balancer config
    lbs = svc.get("loadBalancers", [])
    if lbs:
        # ALB DNS is not in ECS response — read from SSM or config
        alb_dns = openserp_cfg.get("alb_dns", "")
        if alb_dns:
            return f"http://{alb_dns}:7001"

    # Fallback: get task IP directly
    return _get_task_url(cluster, service, ecs)


def _wait_for_healthy(cluster: str, service: str, ecs, timeout: int) -> None:
    """Wait for ECS service to have a running, healthy task."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        resp = ecs.describe_services(cluster=cluster, services=[service])
        svc = resp["services"][0]
        if svc.get("runningCount", 0) > 0:
            # Check health
            url = _get_task_url(cluster, service, ecs)
            if url and _health_check(url):
                logger.info("OpenSERP healthy at %s", url)
                return
        time.sleep(10)
    raise TimeoutError(f"OpenSERP did not become healthy within {timeout}s")


def _get_task_url(cluster: str, service: str, ecs) -> str:
    """Get the private IP of the running ECS task."""
    tasks = ecs.list_tasks(
        cluster=cluster, serviceName=service, desiredStatus="RUNNING"
    )
    task_arns = tasks.get("taskArns", [])
    if not task_arns:
        return ""
    details = ecs.describe_tasks(cluster=cluster, tasks=[task_arns[0]])
    for attachment in details["tasks"][0].get("attachments", []):
        for kv in attachment.get("details", []):
            if kv.get("name") == "privateIPv4Address":
                return f"http://{kv['value']}:7001"
    return ""


def _health_check(url: str) -> bool:
    """Check if OpenSERP is responding."""
    try:
        resp = requests.get(f"{url}/health", timeout=5)
        return resp.status_code == 200
    except requests.RequestException:
        return False


def stop_openserp(config: dict) -> None:
    """Scale OpenSERP ECS service to 0."""
    aws = config.get("aws", {})
    if not aws.get("enabled"):
        return
    openserp_cfg = aws.get("openserp", {})
    ecs = _get_ecs_client(aws.get("region", "us-east-1"))
    ecs.update_service(
        cluster=openserp_cfg["cluster"],
        service=openserp_cfg["service"],
        desiredCount=0,
    )
    logger.info("OpenSERP scaled to 0")

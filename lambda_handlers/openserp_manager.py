"""Lambda handler for OpenSERP idle monitoring and cost-saving teardown.

Triggered by EventBridge rule every 10 minutes.
Checks ALB RequestCount metric — if zero for 30 minutes, tears down:
  - ECS service (scale to 0)
  - NAT Gateway + Elastic IP
  - ALB
Stores teardown config in SSM for re-creation by openserp_client.py.
"""

import logging
import os
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)
logger.setLevel(os.getenv("LOG_LEVEL", "INFO"))

IDLE_THRESHOLD_MINUTES = 30


def handler(event, _context):
    """Check OpenSERP activity and tear down if idle."""
    import boto3

    from src.utils.config import load_config

    config = load_config()
    aws = config.get("aws", {})
    if not aws.get("enabled"):
        return {"action": "skipped", "reason": "aws not enabled"}

    region = aws.get("region", "us-east-1")
    openserp_cfg = aws.get("openserp", {})
    cluster = openserp_cfg["cluster"]
    service = openserp_cfg["service"]

    ecs = boto3.client("ecs", region_name=region)

    # Check if already scaled to 0
    resp = ecs.describe_services(cluster=cluster, services=[service])
    svc = resp["services"][0]
    if svc.get("desiredCount", 0) == 0:
        return {"action": "none", "reason": "already scaled to 0"}

    # Check ALB request count
    target_group_arn = openserp_cfg.get("target_group_arn", "")
    if not target_group_arn:
        return {"action": "skipped", "reason": "no target_group_arn configured"}

    if _has_recent_activity(target_group_arn, region):
        return {"action": "none", "reason": "active in last 30 minutes"}

    # Idle — tear down
    logger.info("OpenSERP idle for %d minutes — tearing down", IDLE_THRESHOLD_MINUTES)
    _teardown(cluster, service, openserp_cfg, region)
    return {
        "action": "teardown",
        "reason": f"idle for {IDLE_THRESHOLD_MINUTES}+ minutes",
    }


def _has_recent_activity(target_group_arn: str, region: str) -> bool:
    """Check ALB RequestCount metric for the target group."""
    import boto3

    cw = boto3.client("cloudwatch", region_name=region)
    # Extract TG dimension from ARN: arn:aws:...targetgroup/name/id → targetgroup/name/id
    tg_dim = "/".join(target_group_arn.split(":")[-1].split("/")[1:])

    now = datetime.now(timezone.utc)
    resp = cw.get_metric_statistics(
        Namespace="AWS/ApplicationELB",
        MetricName="RequestCount",
        Dimensions=[{"Name": "TargetGroup", "Value": f"targetgroup/{tg_dim}"}],
        StartTime=now - timedelta(minutes=IDLE_THRESHOLD_MINUTES),
        EndTime=now,
        Period=IDLE_THRESHOLD_MINUTES * 60,
        Statistics=["Sum"],
    )
    datapoints = resp.get("Datapoints", [])
    total = sum(dp.get("Sum", 0) for dp in datapoints)
    return total > 0


def _teardown(cluster: str, service: str, openserp_cfg: dict, region: str) -> None:
    """Scale ECS to 0 and delete NAT Gateway + ALB."""
    import boto3

    ecs = boto3.client("ecs", region_name=region)
    ec2 = boto3.client("ec2", region_name=region)
    elbv2 = boto3.client("elbv2", region_name=region)
    ssm = boto3.client("ssm", region_name=region)

    # 1. Scale ECS to 0
    ecs.update_service(cluster=cluster, service=service, desiredCount=0)
    logger.info("ECS service scaled to 0")

    # 2. Delete NAT Gateway
    nat_gw_id = openserp_cfg.get("nat_gateway_id", "")
    if nat_gw_id:
        try:
            ec2.delete_nat_gateway(NatGatewayId=nat_gw_id)
            logger.info("Deleted NAT Gateway: %s", nat_gw_id)
            # Store for re-creation
            ssm.put_parameter(
                Name="/wwii-pipeline/nat-gateway-config",
                Value=nat_gw_id,
                Type="String",
                Overwrite=True,
            )
        except Exception as e:
            logger.warning("Failed to delete NAT Gateway: %s", e)

    # 3. Delete ALB
    alb_arn = openserp_cfg.get("alb_arn", "")
    if alb_arn:
        try:
            elbv2.delete_load_balancer(LoadBalancerArn=alb_arn)
            logger.info("Deleted ALB: %s", alb_arn)
        except Exception as e:
            logger.warning("Failed to delete ALB: %s", e)

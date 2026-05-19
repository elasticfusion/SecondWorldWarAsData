"""Lambda handler for dynamic networking lifecycle management.

Creates/deletes NAT Gateway, ALB, and VPC endpoints individually.
Each component is validated before creation — safe to call repeatedly.
Invoked by trigger Lambda (action=create) and idle monitor (action=delete).
"""

import logging
import os
import time

logger = logging.getLogger(__name__)
logger.setLevel(os.getenv("LOG_LEVEL", "INFO"))

ENV_NAME = os.getenv("ENV_NAME", "dev")
PUBLIC_SUBNET = os.getenv("PUBLIC_SUBNET_ID", "")
PRIVATE_SUBNETS = [
    s.strip() for s in os.getenv("PRIVATE_SUBNET_IDS", "").split(",") if s.strip()
]
VPC_ID = os.getenv("VPC_ID", "")
SECURITY_GROUP = os.getenv("SECURITY_GROUP_ID", "")
OPENSERP_SG = os.getenv("OPENSERP_SG_ID", "")
NAT_TAG = f"{ENV_NAME}-nat"
MANAGED_TAG = f"{ENV_NAME}-wwii-pipeline"

INTERFACE_ENDPOINTS = ["ecr.api", "ecr.dkr", "logs"]


def handler(event, _context):
    """Manage dynamic networking lifecycle."""
    import boto3

    # Handle SNS trigger (pipeline completion → teardown)
    if "Records" in event:
        for record in event.get("Records", []):
            if record.get("EventSource") == "aws:sns":
                message = record.get("Sns", {}).get("Message", "")
                if "Phase 3" in message and "completed successfully" in message:
                    logger.info("Phase 3 completion — tearing down networking")
                    region = os.getenv("AWS_REGION", "us-east-1")
                    ec2 = boto3.client("ec2", region_name=region)
                    return _delete_all(ec2, region)
                logger.info("Ignoring SNS (not Phase 3 completion): %s", message[:80])
                return {"action": "none", "reason": "not Phase 3 completion"}

    action = event.get("action", "status")
    region = os.getenv("AWS_REGION", "us-east-1")
    ec2 = boto3.client("ec2", region_name=region)

    if action == "create":
        return _create_all(ec2, region)
    if action == "delete":
        return _delete_all(ec2, region)
    return _status(ec2)


def _status(ec2):
    """Return current state of all components."""
    return {
        "nat": _find_nat(ec2) or "none",
        "endpoints": [e["ServiceName"].split(".")[-1] for e in _find_endpoints(ec2)],
    }


# === CREATE ===


def _create_all(ec2, region):
    """Create all networking components. Each is validated before creation."""
    import boto3

    # Acquire lock
    table_name = os.getenv("CACHE_TABLE", "dev-wwii-api-cache")
    table = boto3.resource("dynamodb", region_name=region).Table(table_name)
    try:
        table.put_item(
            Item={
                "cache_key": "lock#nat-manager",
                "response": str(int(time.time())),
                "ttl": int(time.time()) + 600,
            },
            ConditionExpression="attribute_not_exists(cache_key)",
        )
    except table.meta.client.exceptions.ConditionalCheckFailedException:
        logger.info("Another instance running, waiting for completion...")
        _wait_for_nat(ec2)
        return {"status": "ready"}

    try:
        # 1. VPC Endpoints (fastest to create)
        _ensure_endpoints(ec2, region)

        # 2. NAT Gateway (slowest — 1-2 min)
        _ensure_nat(ec2)

        _notify("Networking UP — NAT, VPC endpoints ready")
        return {"status": "ready"}
    except Exception as e:
        _notify(f"Networking FAILED — {e}")
        logger.error("Create failed: %s", e)
        raise
    finally:
        try:
            table.delete_item(Key={"cache_key": "lock#nat-manager"})
        except Exception:
            pass


# === DELETE ===


def _delete_all(ec2, region):
    """Delete all dynamic networking components."""
    deleted = False

    # 1. NAT Gateway
    nat_id = _find_nat(ec2)
    if nat_id:
        ec2.delete_nat_gateway(NatGatewayId=nat_id)
        logger.info("Deleted NAT: %s", nat_id)
        deleted = True

    # 2. VPC Endpoints
    endpoints = _find_endpoints(ec2)
    if endpoints:
        ep_ids = [e["VpcEndpointId"] for e in endpoints]
        ec2.delete_vpc_endpoints(VpcEndpointIds=ep_ids)
        logger.info("Deleted %d endpoints", len(ep_ids))
        deleted = True

    if deleted:
        _notify("Networking DOWN — NAT, VPC endpoints deleted")
    else:
        logger.info("Nothing to delete — networking already down")
    return {"status": "deleted"}


# === NAT Gateway ===


def _find_nat(ec2):
    """Find existing NAT Gateway by tag."""
    resp = ec2.describe_nat_gateways(
        Filter=[
            {"Name": "tag:Name", "Values": [NAT_TAG]},
            {"Name": "state", "Values": ["available", "pending"]},
        ]
    )
    gws = resp.get("NatGateways", [])
    return gws[0]["NatGatewayId"] if gws else None


def _ensure_nat(ec2):
    """Create NAT if it doesn't exist, wait for available, update route."""
    nat_id = _find_nat(ec2)
    if nat_id:
        # Verify it's not transitioning to deleted
        resp = ec2.describe_nat_gateways(NatGatewayIds=[nat_id])
        state = (
            resp["NatGateways"][0]["State"] if resp.get("NatGateways") else "deleted"
        )
        if state in ("deleting", "deleted", "failed"):
            logger.info("NAT %s is %s, creating new one", nat_id, state)
            nat_id = None

    if not nat_id:
        nat_id = _create_nat(ec2)

    # Wait for available
    waiter = ec2.get_waiter("nat_gateway_available")
    waiter.wait(NatGatewayIds=[nat_id], WaiterConfig={"Delay": 10, "MaxAttempts": 18})
    logger.info("NAT available: %s", nat_id)

    _update_route(ec2, nat_id)


def _create_nat(ec2):
    """Create NAT Gateway with EIP."""
    # Reuse free EIP or allocate
    eips = ec2.describe_addresses(Filters=[{"Name": "domain", "Values": ["vpc"]}])
    free = [a for a in eips.get("Addresses", []) if not a.get("AssociationId")]
    eip_alloc = (
        free[0]["AllocationId"]
        if free
        else ec2.allocate_address(Domain="vpc")["AllocationId"]
    )

    resp = ec2.create_nat_gateway(
        SubnetId=PUBLIC_SUBNET,
        AllocationId=eip_alloc,
        TagSpecifications=[
            {
                "ResourceType": "natgateway",
                "Tags": [{"Key": "Name", "Value": NAT_TAG}],
            }
        ],
    )
    nat_id = resp["NatGateway"]["NatGatewayId"]
    logger.info("Created NAT: %s", nat_id)
    return nat_id


def _update_route(ec2, nat_id):
    """Ensure private route table points to this NAT."""
    rtbs = ec2.describe_route_tables(
        Filters=[{"Name": "tag:Name", "Values": [f"{ENV_NAME}-private-rt"]}]
    )
    for rtb in rtbs.get("RouteTables", []):
        try:
            ec2.create_route(
                RouteTableId=rtb["RouteTableId"],
                DestinationCidrBlock="0.0.0.0/0",
                NatGatewayId=nat_id,
            )
        except Exception:
            ec2.replace_route(
                RouteTableId=rtb["RouteTableId"],
                DestinationCidrBlock="0.0.0.0/0",
                NatGatewayId=nat_id,
            )
        logger.info("Route updated: %s", rtb["RouteTableId"])


def _wait_for_nat(ec2):
    """Wait for NAT to become available (called when another instance is creating)."""
    for _ in range(18):
        nat_id = _find_nat(ec2)
        if nat_id:
            try:
                waiter = ec2.get_waiter("nat_gateway_available")
                waiter.wait(
                    NatGatewayIds=[nat_id],
                    WaiterConfig={"Delay": 10, "MaxAttempts": 12},
                )
                return
            except Exception:
                pass
        time.sleep(10)


# === VPC Endpoints ===


def _find_endpoints(ec2):
    """Find managed VPC endpoints by tag."""
    resp = ec2.describe_vpc_endpoints(
        Filters=[
            {"Name": "tag:ManagedBy", "Values": [MANAGED_TAG]},
            {
                "Name": "vpc-endpoint-state",
                "Values": ["available", "pending", "deleting"],
            },
        ]
    )
    return resp.get("VpcEndpoints", [])


def _ensure_endpoints(ec2, region):
    """Create missing VPC endpoints. Checks each individually."""
    existing = {e["ServiceName"].split(".")[-1] for e in _find_endpoints(ec2)}

    for svc in INTERFACE_ENDPOINTS:
        if svc in existing:
            logger.info("Endpoint %s already exists", svc)
            continue
        # Also check for untagged endpoints
        if _endpoint_exists_untagged(ec2, region, svc):
            logger.info("Endpoint %s exists (untagged)", svc)
            continue
        _create_endpoint(ec2, region, svc)

    # Wait for all to be available
    for _ in range(30):
        pending = [e for e in _find_endpoints(ec2) if e["State"] == "pending"]
        if not pending:
            return
        time.sleep(10)


def _endpoint_exists_untagged(ec2, region, svc):
    """Check if an endpoint exists for this service (even without our tag)."""
    service_name = f"com.amazonaws.{region}.{svc}"
    resp = ec2.describe_vpc_endpoints(
        Filters=[
            {"Name": "service-name", "Values": [service_name]},
            {"Name": "vpc-id", "Values": [VPC_ID]},
            {
                "Name": "vpc-endpoint-state",
                "Values": ["available", "pending", "deleting"],
            },
        ]
    )
    return len(resp.get("VpcEndpoints", [])) > 0


def _create_endpoint(ec2, region, svc):
    """Create a single VPC endpoint."""
    service_name = f"com.amazonaws.{region}.{svc}"
    ec2.create_vpc_endpoint(
        VpcId=VPC_ID,
        ServiceName=service_name,
        VpcEndpointType="Interface",
        SubnetIds=PRIVATE_SUBNETS,
        SecurityGroupIds=[sg for sg in [SECURITY_GROUP, OPENSERP_SG] if sg],
        PrivateDnsEnabled=True,
        TagSpecifications=[
            {
                "ResourceType": "vpc-endpoint",
                "Tags": [
                    {"Key": "Name", "Value": f"{ENV_NAME}-{svc}"},
                    {"Key": "ManagedBy", "Value": MANAGED_TAG},
                ],
            }
        ],
    )
    logger.info("Created endpoint: %s", svc)


# === Notifications ===


def _notify(message):
    """Send SNS notification."""
    topic_arn = os.getenv("NOTIFICATION_TOPIC_ARN", "")
    if not topic_arn:
        return
    try:
        import boto3

        region = os.getenv("AWS_REGION", "us-east-1")
        boto3.client("sns", region_name=region).publish(
            TopicArn=topic_arn,
            Subject=f"WWII Pipeline: {message.split(' — ')[0]}",
            Message=message,
        )
    except Exception as e:
        logger.warning("Notification failed: %s", e)

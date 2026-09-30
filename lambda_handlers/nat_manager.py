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

# Interface endpoints so private-subnet compute reaches AWS services WITHOUT NAT.
# ecs/ecs-agent/ecs-telemetry are REQUIRED for GPU Batch instances to register with
# the ECS/Batch control plane without NAT (their absence stalled OCR jobs at
# RUNNABLE — instances booted but couldn't join the cluster). Placed on the same
# 2 GPU-capable subnets the Batch CE + Fargate use (single aligned subnet set).
INTERFACE_ENDPOINTS = [
    "ecr.api",
    "ecr.dkr",
    "logs",
    "secretsmanager",
    "ecs",
    "ecs-agent",
    "ecs-telemetry",
]


def _lease_table():
    """DynamoDB table for lease lookups (patchable in tests — no global boto3 patch)."""
    import boto3

    region = os.getenv("AWS_REGION", "us-east-1")
    return boto3.resource("dynamodb", region_name=region).Table(
        f"{ENV_NAME}-wwii-api-cache"
    )


def _ecs_client():
    """ECS client for task lookups (patchable in tests)."""
    import boto3

    region = os.getenv("AWS_REGION", "us-east-1")
    return boto3.client("ecs", region_name=region)


def _nat_demand_present() -> bool:
    """Cluster-wide NAT demand (M3, §4): live leases OR running pipeline tasks.

    A phase-completion SNS message must NOT tear down NAT while ANOTHER phase/job
    still needs egress (the Phase1->Phase2 churn: Phase 1 'complete' fired teardown
    under a starting Phase 2). Checks live nat#lease# entries (filtering expired
    TTLs) and running non-openserp ECS tasks. Returns True (keep NAT) on any error
    — never tear down on uncertainty.
    """
    now = int(time.time())
    try:
        # 1) live leases
        resp = _lease_table().scan(
            FilterExpression="begins_with(cache_key, :p)",
            ExpressionAttributeValues={":p": "nat#lease#"},
            ProjectionExpression="cache_key, #t",
            ExpressionAttributeNames={"#t": "ttl"},
        )
        for item in resp.get("Items", []):
            ttl = item.get("ttl")
            if ttl is None or int(ttl) > now:
                logger.info("NAT demand: live lease present — keeping NAT")
                return True
        # 2) running pipeline tasks (exclude openserp support service)
        running = (
            _ecs_client()
            .list_tasks(cluster=f"{ENV_NAME}-wwii-pipeline", desiredStatus="RUNNING")
            .get("taskArns", [])
        )
        pipeline = [t for t in running if "openserp" not in t]
        if pipeline:
            logger.info(
                "NAT demand: %d running pipeline task(s) — keeping NAT", len(pipeline)
            )
            return True
        # 3) OCR Batch jobs in flight — GPU instances need egress to register with
        # ECS + pull the image + S3. Batch jobs aren't ECS tasks and hold no nat
        # lease, so count them explicitly, else NAT is torn down mid-OCR (jobs then
        # stall RUNNABLE forever). Check both the spot and on-demand OCR queues.
        if _ocr_jobs_in_flight():
            logger.info("NAT demand: OCR Batch job(s) in flight — keeping NAT")
            return True
        return False
    except Exception as e:  # pragma: no cover - defensive
        logger.warning("NAT demand check failed (%s) — assuming demand, keeping NAT", e)
        return True


def _ocr_jobs_in_flight() -> bool:
    """True if any OCR Batch job is non-terminal on either OCR queue (needs egress).

    Fails SAFE: if a queue check raises anything other than a definitive
    'queue does not exist', we assume demand is present (return True) rather than
    silently reporting no-demand. A swallowed error (e.g. missing batch:ListJobs
    IAM permission) previously made this return False, which tore down NAT under
    a running OCR job — the guard was blind, not permissive."""
    import boto3
    from botocore.exceptions import ClientError

    batch = boto3.client("batch", region_name=os.getenv("AWS_REGION", "us-east-1"))
    for queue in (
        f"{ENV_NAME}-wwii-chandra-gpu",
        f"{ENV_NAME}-wwii-chandra-gpu-ondemand",
    ):
        for status in ("SUBMITTED", "PENDING", "RUNNABLE", "STARTING", "RUNNING"):
            try:
                if batch.list_jobs(jobQueue=queue, jobStatus=status).get(
                    "jobSummaryList"
                ):
                    return True
            except ClientError as e:
                code = e.response.get("Error", {}).get("Code", "")
                # A genuinely-absent queue is fine to skip; anything else
                # (AccessDenied, throttling, etc.) means we CANNOT confirm
                # no-demand, so fail safe and assume demand.
                if code in ("ClientException", "JobQueueNotFoundException"):
                    continue
                logger.warning(
                    "OCR demand check error on %s/%s (%s) — assuming demand",
                    queue,
                    status,
                    code,
                )
                return True
            except Exception as e:  # pragma: no cover - defensive, fail safe
                logger.warning("OCR demand check error (%s) — assuming demand", e)
                return True
    return False


def handler(event, _context):
    """Manage dynamic networking lifecycle."""
    import boto3

    # SNS trigger (pipeline completion → demand-aware teardown)
    if "Records" in event:
        return _handle_sns_records(event)

    action = event.get("action", "status")
    region = os.getenv("AWS_REGION", "us-east-1")
    ec2 = boto3.client("ec2", region_name=region)

    if action == "create":
        return _create_all(ec2, region)
    if action == "delete":
        return _delete_all(ec2, region, force=bool(event.get("force")))
    if action == "verify":
        ready, missing = _verify_ready(ec2, region)
        return {"ready": ready, "missing": missing}
    return _status(ec2)


def _verify_ready(ec2, region):
    """Readiness contract: egress is READY iff the NAT gateway is 'available' AND
    every required interface endpoint is 'available'. Returns (ready, missing[]).
    Compute (GPU OCR) must not be requested until this is True — otherwise an
    instance boots with no path to ECS/ECR and can't register (the OCR stall)."""
    missing = []
    # NAT available?
    nat_id = _find_nat(ec2)
    nat_ok = False
    if nat_id:
        resp = ec2.describe_nat_gateways(NatGatewayIds=[nat_id])
        nat_ok = (
            bool(resp.get("NatGateways"))
            and resp["NatGateways"][0]["State"] == "available"
        )
    if not nat_ok:
        missing.append("nat")
    # All required interface endpoints available?
    available = {
        e["ServiceName"].split(".")[-1]
        for e in _find_endpoints(ec2)
        if e["State"] == "available"
    }
    for svc in INTERFACE_ENDPOINTS:
        if svc not in available and not _endpoint_available_untagged(ec2, region, svc):
            missing.append(svc)
    return (len(missing) == 0, missing)


def _endpoint_available_untagged(ec2, region, svc):
    """True if an AVAILABLE endpoint exists for this service (untagged included)."""
    service_name = f"com.amazonaws.{region}.{svc}"
    resp = ec2.describe_vpc_endpoints(
        Filters=[
            {"Name": "service-name", "Values": [service_name]},
            {"Name": "vpc-id", "Values": [VPC_ID]},
            {"Name": "vpc-endpoint-state", "Values": ["available"]},
        ]
    )
    return len(resp.get("VpcEndpoints", [])) > 0


def _handle_sns_records(event) -> dict:
    """Handle SNS records: on a pipeline-completion message, tear down NAT — but
    only if cluster NAT demand is zero (M3 §4 — fixes the Phase1->Phase2 churn)."""
    import boto3

    for record in event.get("Records", []):
        if record.get("EventSource") != "aws:sns":
            continue
        message = record.get("Sns", {}).get("Message", "")
        if "completed successfully" not in message:
            logger.info("Ignoring SNS (not completion): %s", message[:80])
            return {"action": "none", "reason": "not pipeline completion"}
        if _nat_demand_present():
            logger.info("Completion message, but NAT demand remains — NOT tearing down")
            return {"action": "none", "reason": "nat demand present"}
        logger.info("Pipeline completion — tearing down networking")
        region = os.getenv("AWS_REGION", "us-east-1")
        return _delete_all(boto3.client("ec2", region_name=region), region)
    return {"action": "none", "reason": "no sns record"}


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
        already_existed = bool(_find_nat(ec2))
        _ensure_nat(ec2)

        if not already_existed:
            _notify("Networking UP — NAT, VPC endpoints ready")
        # Return ACCURATE readiness — never blind 'ready'. Compute must not be
        # requested until egress is verified (NAT available + all required
        # endpoints available), else GPU instances boot but can't register.
        ready, missing = _verify_ready(ec2, region)
        return {"status": "ready" if ready else "not_ready", "missing": missing}
    except Exception as e:
        _notify(f"Networking FAILED — {e}")
        logger.error("Create failed: %s", e)
        raise
    finally:
        try:
            table.delete_item(Key={"cache_key": "lock#nat-manager"})
        except Exception as e:
            logger.warning("Failed to release lock: %s", e)


# === DELETE ===


def _delete_all(ec2, region, force=False):
    """Delete all dynamic networking components.

    Refuses teardown while there is live NAT demand (in-flight OCR Batch jobs /
    running pipeline tasks) unless force=True — a direct action=delete previously
    bypassed the demand check and tore down NAT+endpoints under a RUNNING OCR job,
    blackholing its egress so the container could not pull from ECR (CannotPull
    ECRContainerError). The guard now lives here so EVERY delete path honors it,
    not just the SNS-completion path."""
    if not force and _nat_demand_present():
        logger.info("Delete requested but NAT demand present — refusing teardown")
        return {"action": "none", "reason": "nat demand present"}

    deleted = False

    # 1. NAT Gateway
    nat_id = _find_nat(ec2)
    if nat_id:
        # Get EIP allocation before deleting NAT
        nat_info = ec2.describe_nat_gateways(NatGatewayIds=[nat_id])
        eip_alloc_ids = [
            addr["AllocationId"]
            for gw in nat_info.get("NatGateways", [])
            for addr in gw.get("NatGatewayAddresses", [])
            if addr.get("AllocationId")
        ]
        ec2.delete_nat_gateway(NatGatewayId=nat_id)
        logger.info("Deleted NAT: %s", nat_id)
        # Release EIPs after NAT is deleted (wait for disassociation)
        if eip_alloc_ids:
            time.sleep(5)  # Brief wait for NAT to release EIP
            for alloc_id in eip_alloc_ids:
                try:
                    ec2.release_address(AllocationId=alloc_id)
                    logger.info("Released EIP: %s", alloc_id)
                except Exception as e:
                    logger.warning("Failed to release EIP %s: %s", alloc_id, e)
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
    """Create NAT Gateway with fresh EIP (new IP each time for reputation isolation)."""
    # Release any orphaned EIPs first (prevents AddressLimitExceeded)
    try:
        eips = ec2.describe_addresses(Filters=[{"Name": "domain", "Values": ["vpc"]}])
        for addr in eips.get("Addresses", []):
            if not addr.get("AssociationId"):
                ec2.release_address(AllocationId=addr["AllocationId"])
                logger.info("Released orphaned EIP: %s", addr["AllocationId"])
    except Exception as e:
        logger.warning("EIP cleanup failed: %s", e)

    eip_alloc = ec2.allocate_address(Domain="vpc")["AllocationId"]

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
                continue
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
    """Create missing VPC endpoints. Each checked individually.

    Only 'available'/'pending' endpoints count as PRESENT — a 'deleting' endpoint
    is NOT present (the create-after-delete race: treating 'deleting' as present
    skipped recreation, leaving instances with no endpoints -> can't register).
    We wait out any 'deleting' endpoint for a service, then (re)create it."""
    present = {
        e["ServiceName"].split(".")[-1]
        for e in _find_endpoints(ec2)
        if e["State"] in ("available", "pending")
    }
    for svc in INTERFACE_ENDPOINTS:
        if svc in present:
            logger.info("Endpoint %s already present", svc)
            continue
        _wait_out_deleting(ec2, region, svc)
        if _endpoint_present_untagged(ec2, region, svc):
            logger.info("Endpoint %s present (untagged)", svc)
            continue
        _create_endpoint(ec2, region, svc)

    # Wait for all to be available
    for _ in range(30):
        pending = [e for e in _find_endpoints(ec2) if e["State"] == "pending"]
        if not pending:
            return
        time.sleep(10)


def _wait_out_deleting(ec2, region, svc):
    """Block until any 'deleting' endpoint for this service is gone (so a fresh one
    can be created without the racy exists-check false-matching it)."""
    service_name = f"com.amazonaws.{region}.{svc}"
    for _ in range(30):
        resp = ec2.describe_vpc_endpoints(
            Filters=[
                {"Name": "service-name", "Values": [service_name]},
                {"Name": "vpc-id", "Values": [VPC_ID]},
                {"Name": "vpc-endpoint-state", "Values": ["deleting"]},
            ]
        )
        if not resp.get("VpcEndpoints"):
            return
        logger.info("Waiting for %s endpoint to finish deleting...", svc)
        time.sleep(10)


def _endpoint_present_untagged(ec2, region, svc):
    """True if a usable (available/pending) endpoint exists for this service —
    'deleting' does NOT count as present."""
    service_name = f"com.amazonaws.{region}.{svc}"
    resp = ec2.describe_vpc_endpoints(
        Filters=[
            {"Name": "service-name", "Values": [service_name]},
            {"Name": "vpc-id", "Values": [VPC_ID]},
            {"Name": "vpc-endpoint-state", "Values": ["available", "pending"]},
        ]
    )
    return len(resp.get("VpcEndpoints", [])) > 0


def _create_endpoint(ec2, region, svc):
    """Create a single VPC interface endpoint with private DNS.

    Tolerates the AWS 'private-dns-enabled cannot be set because there is already
    a conflicting DNS domain' error: a just-deleted endpoint's private-DNS
    association lingers briefly after the endpoint leaves 'deleting', so a fresh
    create can be rejected. We wait the deleting endpoint out and retry; if a
    usable endpoint appears meanwhile, that's success. (This is distinct from the
    exists-check race — here AWS rejects the create outright on the DNS domain.)"""
    from botocore.exceptions import ClientError

    service_name = f"com.amazonaws.{region}.{svc}"
    tag_spec = [
        {
            "ResourceType": "vpc-endpoint",
            "Tags": [
                {"Key": "Name", "Value": f"{ENV_NAME}-{svc}"},
                {"Key": "ManagedBy", "Value": MANAGED_TAG},
            ],
        }
    ]
    for attempt in range(6):
        try:
            ec2.create_vpc_endpoint(
                VpcId=VPC_ID,
                ServiceName=service_name,
                VpcEndpointType="Interface",
                SubnetIds=PRIVATE_SUBNETS,
                SecurityGroupIds=[sg for sg in [SECURITY_GROUP, OPENSERP_SG] if sg],
                PrivateDnsEnabled=True,
                TagSpecifications=tag_spec,
            )
            logger.info("Created endpoint: %s", svc)
            return
        except ClientError as e:
            msg = str(e)
            if "conflicting DNS domain" not in msg and "private-dns-enabled" not in msg:
                raise
            # A prior endpoint's private-DNS still lingers. If a usable one now
            # exists, we're done; otherwise wait out the deleting one and retry.
            if _endpoint_present_untagged(ec2, region, svc):
                logger.info("Endpoint %s now present (DNS conflict resolved)", svc)
                return
            logger.info(
                "DNS-domain conflict creating %s (attempt %d) — waiting out deleting",
                svc,
                attempt + 1,
            )
            _wait_out_deleting(ec2, region, svc)
            time.sleep(10)
    # Last resort: if it's present after all retries, success; else raise.
    if _endpoint_present_untagged(ec2, region, svc):
        return
    raise RuntimeError(f"Could not create endpoint {svc} — persistent DNS conflict")


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

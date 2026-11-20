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
# NAT placement in AZ order (task 1.2c, owner 2026-11-18: one zonal NAT, failing over to the
# other AZ when its AZ fails). PUBLIC_SUBNET_IDS lists them; PUBLIC_SUBNET_ID alone still works.
PUBLIC_SUBNETS = [
    s.strip()
    for s in (os.getenv("PUBLIC_SUBNET_IDS", "") or PUBLIC_SUBNET).split(",")
    if s.strip()
]
AZ_STATE_KEY = "nat#azstate"  # NOT under nat#lease#: it must not count as NAT demand
BAD_SUBNET_MINUTES = int(os.getenv("NAT_BAD_SUBNET_MINUTES", "60"))
PENDING_STUCK_SECONDS = 300  # a NAT is normally available in 1-2 min
NAT_WAIT_ATTEMPTS = 18  # x 10 s = 3 min, capped by what is left of the Lambda's 300 s
FAILOVER_MIN_INTERVAL_S = 600  # tasks reporting no egress cannot ping-pong the NAT
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
    # Control-plane endpoints for the IN-VPC nat_teardown decider. Created with NAT and
    # deleted with NAT (owner directive: endpoints are part of the NAT spin-up/tear-down
    # cycle, NOT always-on). The decider only needs these WHILE NAT is up (to decide
    # teardown / poll containers); when NAT is down it detects that from the free
    # DynamoDB-gateway-backed queue/lease state and exits before any endpoint call.
    "ec2",
    "lambda",
    "scheduler",
]


def _lease_table():
    """DynamoDB table for lease lookups (patchable in tests — no global boto3 patch)."""
    import boto3

    region = os.getenv("AWS_REGION", "us-east-1")
    return boto3.resource("dynamodb", region_name=region).Table(
        os.getenv("CACHE_TABLE", f"{ENV_NAME}-wwii-api-cache")
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
        if _nat_live_lease_present(now):
            return True
        # 2) running pipeline tasks (exclude the openserp support SERVICE).
        if _nat_pipeline_task_present():
            return True
        # 3) OCR Batch jobs in flight — GPU instances need egress to register with
        # ECS + pull the image + S3. Batch jobs aren't ECS tasks and hold no nat
        # lease, so count them explicitly, else NAT is torn down mid-OCR (jobs then
        # stall RUNNABLE forever). Check both the spot and on-demand OCR queues.
        if _ocr_jobs_in_flight():
            logger.info("NAT demand: OCR Batch job(s) in flight — keeping NAT")
            return True
        # 4) PENDING Grok batch jobs — a submitted batch awaiting the poller keeps
        # NAT UP (operator directive). This avoids tearing NAT down and immediately
        # recreating it for the --retrieve-only task, and guarantees the retrieve
        # has egress the moment the batch completes (no cold-start race).
        if _grok_batch_pending():
            logger.info("NAT demand: pending Grok batch job(s) — keeping NAT")
            return True
        return False
    except Exception as e:  # pragma: no cover - defensive
        logger.warning("NAT demand check failed (%s) — assuming demand, keeping NAT", e)
        return True


def _nat_live_lease_present(now: int) -> bool:
    """Demand check 1: any live nat#lease# entry (filtering expired TTLs). Raises on error so
    the caller's fail-safe (keep NAT on uncertainty) applies."""
    from src.utils.ddb_scan import scan_prefix

    for (
        item
    ) in scan_prefix(  # all pages: a lease beyond the first 1 MB page counts (task 1.2)
        _lease_table(),
        "nat#lease#",
        projection="cache_key, #t",
        names={"#t": "ttl"},
    ):
        ttl = item.get("ttl")
        if ttl is None or int(ttl) > now:
            logger.info("NAT demand: live lease present — keeping NAT")
            return True
    return False


def _nat_pipeline_task_present() -> bool:
    """Demand check 2: any running non-openserp pipeline ECS task. Raises on error so the
    caller's fail-safe (keep NAT on uncertainty) applies."""
    # NOTE: the openserp exclusion must inspect each task's GROUP
    # (service:{env}-wwii-openserp), NOT the task ARN — the ARN is
    # .../task/{cluster}/{taskId} and never contains 'openserp', so an
    # ARN-substring check silently counted openserp as a pipeline task and
    # pinned NAT up forever. describe_tasks to read the group.
    #
    # Exclude ONLY the browser support service group EXACTLY
    # (service:{env}-wwii-openserp). A substring 'openserp' match is too broad:
    # it also excluded the SQS worker (service:{env}-wwii-openserp-worker), which
    # DOES need NAT (ECR/S3/Grok/search egress) — so a running worker was invisible
    # to the demand check and NAT got torn down under it (ECR pull i/o timeout).
    ecs = _ecs_client()
    browser_group = f"service:{ENV_NAME}-wwii-openserp"
    running = ecs.list_tasks(
        cluster=f"{ENV_NAME}-wwii-pipeline", desiredStatus="RUNNING"
    ).get("taskArns", [])
    pipeline = []
    if running:
        described = ecs.describe_tasks(
            cluster=f"{ENV_NAME}-wwii-pipeline", tasks=running
        ).get("tasks", [])
        pipeline = [t for t in described if (t.get("group", "") or "") != browser_group]
    if pipeline:
        logger.info(
            "NAT demand: %d running pipeline task(s) — keeping NAT", len(pipeline)
        )
        return True
    return False


def _grok_batch_pending() -> bool:
    """True if any Grok batch job still needs NAT: status 'pending' (awaiting the
    poller) OR 'ready' (completed at Grok but the --retrieve-only task has not run
    yet — the completion->retrieve window). Counting 'ready' prevents tearing NAT
    down exactly as the retrieve task is launched. Age-capped: a zombie job older
    than NAT_BATCH_DEMAND_MAX_HOURS no longer pins NAT (cost tail guard). Fails SAFE
    (assume demand) on error."""
    try:
        from src.utils.job_queue import _get_table

        now = int(time.time())
        max_age = int(os.getenv("NAT_BATCH_DEMAND_MAX_HOURS", "8")) * 3600
        from src.utils.ddb_scan import scan_prefix

        for item in scan_prefix(  # all pages (task 1.2)
            _get_table(),
            "batch_job#",
            names={"#s": "status"},
            extra_filter="#s IN (:pending, :ready)",
            values={":pending": "pending", ":ready": "ready"},
        ):
            submitted = int(item.get("submitted_at", now))
            if now - submitted <= max_age:
                return True  # a live (non-zombie) batch needs NAT
        return False
    except Exception as e:  # pragma: no cover - defensive, fail safe
        logger.warning("Grok batch demand check failed (%s) — assuming demand", e)
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
    from src.utils import gpu_ladder

    for queue in gpu_ladder.all_queues(
        ENV_NAME
    ):  # every Spot rung + the on-demand queue
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
    remaining = getattr(_context, "get_remaining_time_in_millis", None)
    _DEADLINE[0] = time.time() + remaining() / 1000.0 if remaining else 0.0
    ec2 = boto3.client("ec2", region_name=region)

    if action == "create":
        return _create_all(ec2, region)
    if action == "failover":
        # a task found no egress through a NAT reported ready (its AZ may be down)
        return _create_all(ec2, region, failover_from=str(event.get("nat_id", "")))
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
    # NAT 'available' != routable — the default route must also be ACTIVE.
    elif not _route_active(ec2, nat_id):
        missing.append("route")
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
    """SNS records (e.g. pipeline-completion) NO LONGER drive teardown.

    Per the NAT lifecycle model: phases/phase-completion events NEVER tear down NAT or
    endpoints — the dedicated `nat_teardown` cron Lambda is the SOLE teardown decider
    (fires every 4h; tears down only when the DynamoDB queue is empty AND no container is
    processing, rechecking every 10 min while work is ongoing). This handler is retained
    only so a stray SNS subscription cannot error; it is now a no-op.
    """
    logger.info(
        "SNS record received — teardown is owned by the nat_teardown cron; no-op"
    )
    return {"action": "none", "reason": "teardown owned by nat_teardown cron Lambda"}


def _status(ec2):
    """Return current state of all components."""
    return {
        "nat": _find_nat(ec2) or "none",
        "endpoints": [e["ServiceName"].split(".")[-1] for e in _find_endpoints(ec2)],
    }


# === CREATE ===


def _await_other_instance(ec2, region) -> dict:
    """Another instance holds the create lock: wait for its NAT, then make sure the default route
    points at it (idempotent) and return VERIFIED readiness - never a blind 'ready'.

    The lock holder can die between creating the NAT and updating the route (a Lambda timeout:
    endpoints took 3 min, then the NAT wait ran past 300s, 2026-11-14). Its NAT is then
    'available' while 0.0.0.0/0 still points at the deleted one (a blackhole), and every caller
    used to be told 'ready' - tasks ran for an hour with no egress."""
    _wait_for_nat(ec2)
    nat_id = _find_nat(ec2)
    if nat_id and not _route_active(ec2, nat_id):
        logger.warning(
            "Default route not active for %s after the other instance: repointing",
            nat_id,
        )
        try:
            _update_route(ec2, nat_id)
        except Exception as e:  # noqa: BLE001 - reported as not ready below
            logger.error("Route repair failed for %s: %s", nat_id, e)
    ready, missing = _verify_ready(ec2, region)
    # no "failover" key: a failover request that waited here was not acted on, and is resent
    return {
        "status": "ready" if ready else "not_ready",
        "missing": missing,
        "nat": nat_id or "",
    }


def _create_all(ec2, region, failover_from: str = ""):
    """Create all networking components. Each is validated before creation. ``failover_from``: a
    NAT that tasks cannot egress through - replaced in another AZ (task 1.2c)."""
    # Acquire lock
    table = _az_table()
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
        return _await_other_instance(ec2, region)

    try:
        return _create_locked(ec2, region, table, failover_from)
    except Exception as e:
        _notify(f"Networking FAILED — {e}")
        logger.error("Create failed: %s", e)
        raise
    finally:
        try:
            table.delete_item(Key={"cache_key": "lock#nat-manager"})
        except Exception as e:
            logger.warning("Failed to release lock: %s", e)


def _create_locked(ec2, region, table, failover_from: str) -> dict:
    """_create_all's work, under the nat-manager lock."""
    outcome = ""
    try:
        # 1. VPC Endpoints (fastest to create)
        _ensure_endpoints(ec2, region)

        # 2. NAT Gateway (slowest — 1-2 min)
        outcome = _failover_nat(ec2, table, failover_from) if failover_from else ""
        already_existed = bool(_find_nat(ec2))
        _ensure_nat(ec2, table)

        if not already_existed:
            _notify("Networking UP — NAT, VPC endpoints ready")
        _stamp_lease_floor(table)
        # Return ACCURATE readiness — never blind 'ready'. Compute must not be
        # requested until egress is verified (NAT available + all required
        # endpoints available), else GPU instances boot but can't register.
        ready, missing = _verify_ready(ec2, region)
        result = {
            "status": "ready" if ready else "not_ready",
            "missing": missing,
            "nat": _find_nat(ec2) or "",
        }
    except NatNotReady as e:
        result = _not_ready(e)
    if outcome:
        # also when not ready: the old NAT is gone even if the new one is not up yet
        result["failover"] = outcome
    return result


def _not_ready(e: "NatNotReady") -> dict:
    """The NAT's AZ looks unhealthy (it was replaced or its subnet marked bad): the caller (the
    task's readiness loop) retries, and the next attempt uses the other AZ. No NAT in any AZ is
    an operator alert."""
    logger.warning("OBSERV nat.not_ready status=warn reason=%s", e)
    if e.alert:
        _notify(f"Networking FAILED — {e}")
    return {"status": "not_ready", "missing": ["nat"], "reason": str(e)}


def _stamp_lease_floor(table) -> None:
    """A NAT lease FLOOR: a gateway-level lease with a 4h TTL so the scheduled teardown won't
    reclaim NAT for at least NAT_LEASE_HOURS even across the brief Phase1->Phase2 gap or the human
    dedup wait before Phase 3 re-requests. Per-task leases still add demand on top; this is only a
    minimum floor. TTL self-expires so a crashed run can't pin NAT forever."""
    try:
        lease_hours = int(os.getenv("NAT_LEASE_HOURS", "4"))
        table.put_item(
            Item={
                "cache_key": "nat#lease#gateway",
                "response": str(int(time.time())),
                "ttl": int(time.time()) + lease_hours * 3600,
            }
        )
        logger.info("Stamped %dh NAT gateway lease floor", lease_hours)
    except Exception as e:  # pragma: no cover - defensive
        logger.warning("Failed to stamp NAT gateway lease: %s", e)


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
    if _delete_nat_gateway(ec2):
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


def _delete_nat_gateway(ec2, nat_id: str = "") -> bool:
    """Delete the NAT gateway (``nat_id``, else the tagged one) and release its EIPs. Returns
    True if a NAT was deleted. Preserves the exact order: delete NAT -> wait -> release EIPs.
    """
    nat_id = nat_id or (_find_nat(ec2) or "")
    if not nat_id:
        return False
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
    return True


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


class NatNotReady(RuntimeError):
    """The NAT is not usable yet: its AZ was marked bad and the next attempt places it in another,
    or (``alert``) it could not be created anywhere - an operator alert."""

    def __init__(self, message: str, alert: bool = False):
        super().__init__(message)
        self.alert = alert


def _nat_info(ec2) -> dict:
    """The tagged NAT that is pending/available ({} if none). A FAILED one (its AZ may be down)
    marks its subnet bad first; AWS keeps failed NATs visible for about an hour."""
    resp = ec2.describe_nat_gateways(
        Filter=[
            {"Name": "tag:Name", "Values": [NAT_TAG]},
            {"Name": "state", "Values": ["available", "pending", "failed"]},
        ]
    )
    live: dict = {}
    for gw in resp.get("NatGateways", []):
        if gw.get("State") == "failed":
            _note_failed_nat(gw)
        elif not live:
            live = {
                "id": gw["NatGatewayId"],
                "state": gw.get("State", ""),
                "subnet": gw.get("SubnetId", ""),
                "created": gw.get("CreateTime"),
            }
    return live


_failed_seen: set = set()


def _note_failed_nat(gw: dict) -> None:
    """Mark a failed NAT's subnet bad ONCE per NAT: AWS shows a failed NAT for about an hour, and
    re-marking on every call (or cold start) would stretch the bad window."""
    if gw["NatGatewayId"] in _failed_seen:
        return
    _failed_seen.add(gw["NatGatewayId"])
    table = _az_table()
    seen = list(_az_state(table).get("failed_nats") or [])
    if gw["NatGatewayId"] in seen:
        return
    _set_az_state(table, failed_nats=(seen + [gw["NatGatewayId"]])[-20:])
    _mark_bad(
        table,
        gw.get("SubnetId", ""),
        f"NAT {gw['NatGatewayId']} failed: "
        f"{gw.get('FailureCode', '')} {gw.get('FailureMessage', '')}".strip(),
    )


def _age_seconds(created) -> float:
    try:
        return time.time() - created.timestamp()
    except AttributeError:
        return 0.0


def _ensure_nat(ec2, table=None):
    """Create NAT if it doesn't exist, wait for available, update route. A NAT stuck pending, or
    one that does not become available in time, is deleted and its subnet marked bad; the next
    attempt places it in another AZ (task 1.2c). Raises NatNotReady then."""
    table = table if table is not None else _az_table()
    nat = _nat_info(ec2)
    if (
        nat
        and nat["state"] == "pending"
        and _age_seconds(nat["created"]) > PENDING_STUCK_SECONDS
    ):
        if not _fail_subnet(ec2, table, nat, "NAT stuck pending"):
            # never create a second NAT while the stuck one may still exist (two would bill)
            raise NatNotReady(f"could not delete the stuck NAT {nat['id']}")
        nat = {}
    if not nat:
        nat = _create_nat_with_failover(ec2, table)

    # Wait for available, within what is left of this Lambda (endpoints may have taken minutes)
    from botocore.exceptions import WaiterError

    attempts = _wait_attempts_left()
    waiter = ec2.get_waiter("nat_gateway_available")
    try:
        waiter.wait(
            NatGatewayIds=[nat["id"]],
            WaiterConfig={"Delay": 10, "MaxAttempts": attempts},
        )
    except WaiterError as e:
        _after_wait_timeout(ec2, table, nat, attempts, e)
    logger.info("NAT available: %s (%s)", nat["id"], nat["subnet"])

    _update_route(ec2, nat["id"])
    _note_subnet_in_use(table, nat["subnet"])
    _release_orphan_eips(ec2)  # a replaced NAT's EIP is free once its deletion finished


def _after_wait_timeout(ec2, table, nat: dict, attempts: int, error: Exception) -> None:
    """The waiter gave up. Available after all: return (use it). Failed, or still pending after
    the full 3 minutes: delete it, mark its subnet bad, raise. Otherwise (the Lambda ran out of
    time, or the state is unknown) keep it - the next call keeps waiting, and replaces it only
    once it has been pending > PENDING_STUCK_SECONDS - and raise."""
    state = _nat_state(ec2, nat["id"])
    if state == "available":
        return
    if state == "failed" or (state == "pending" and attempts >= NAT_WAIT_ATTEMPTS):
        _fail_subnet(ec2, table, nat, "NAT failed or not available in 3 minutes")
        raise NatNotReady(
            f"NAT {nat['id']} not available in {nat['subnet']}"
        ) from error
    raise NatNotReady(
        f"NAT {nat['id']} still pending ({state or 'unknown'})"
    ) from error


_DEADLINE = [0.0]  # this invocation's end (handler), so waits fit in the Lambda's time


def _wait_attempts_left() -> int:
    """NAT waiter attempts (10 s each) that fit before this Lambda's deadline, keeping 40 s for
    the route and readiness checks. Full NAT_WAIT_ATTEMPTS when the deadline is unknown.
    """
    if not _DEADLINE[0]:
        return NAT_WAIT_ATTEMPTS
    return max(1, min(NAT_WAIT_ATTEMPTS, int((_DEADLINE[0] - time.time() - 40) // 10)))


def _nat_state(ec2, nat_id: str) -> str:
    try:
        gws = ec2.describe_nat_gateways(NatGatewayIds=[nat_id]).get("NatGateways", [])
        return gws[0].get("State", "") if gws else "deleted"
    except Exception:  # noqa: BLE001 - unknown: treat as not failed
        return ""


# create_nat_gateway errors that point at the subnet or its AZ (anything else - limits,
# throttling, permissions - is not the AZ's fault and goes to the "Networking FAILED" alert)
AZ_ERROR_CODES = (
    "InsufficientFreeAddressesInSubnet",
    "InvalidSubnetID.NotFound",
    "InvalidSubnet",
    "Unsupported",
    "ServerInternal",
    "InternalError",
    "ServiceUnavailable",
    "Unavailable",
)


def _az_error(e: Exception) -> str:
    """The error code if ``e`` points at the subnet or its AZ, else ''."""
    code = (getattr(e, "response", None) or {}).get("Error", {}).get("Code", "")
    return code if code in AZ_ERROR_CODES else ""


def _create_nat_with_failover(ec2, table) -> dict:
    """Create the NAT in the first subnet not marked bad; a create error marks that subnet bad
    and tries the next. The first subnet is the primary AZ; any other is a failover, alerted.
    """
    state = _az_state(table)
    errors = []
    for subnet in _subnet_order(state):
        try:
            nat_id = _create_nat(ec2, subnet)
        except (
            Exception
        ) as e:  # noqa: BLE001 - an AZ error tries the next AZ; others raise
            code = _az_error(e)
            if not code:
                raise
            errors.append(f"{subnet}: {code}")
            _mark_bad(table, subnet, f"create_nat_gateway failed: {code}")
            continue
        if subnet != PUBLIC_SUBNETS[0]:
            _alert_failover(table, subnet, state)
        return {"id": nat_id, "state": "pending", "subnet": subnet, "created": None}
    raise NatNotReady(
        f"NAT could not be created in any subnet ({'; '.join(errors)}); the task retries, "
        "check AWS Health for the region",
        alert=True,
    )


def _failover_nat(ec2, table, nat_id: str) -> str:
    """Tasks have no egress through ``nat_id``: delete it and mark its subnet bad, so _ensure_nat
    creates the replacement in another AZ. Only if it is still the current NAT, and at most once
    per FAILOVER_MIN_INTERVAL_S (several tasks may report the same outage). Returns the outcome:
    "done", or "skipped" (already replaced / recent failover). Raises NatNotReady if the delete
    failed (the claim is released, so the next request can retry)."""
    nat = _nat_info(ec2)
    if not nat or nat["id"] != nat_id:
        logger.info("OBSERV nat.failover_skipped status=ok reason=already_replaced")
        return "skipped"
    if not _claim_failover(table):
        logger.info("OBSERV nat.failover_skipped status=ok reason=recent_failover")
        return "skipped"
    if not _fail_subnet(ec2, table, nat, "tasks have no internet through it"):
        _set_az_state(table, last_failover=0)
        raise NatNotReady(f"could not delete NAT {nat['id']} for the failover")
    return "done"


def _claim_failover(table) -> bool:
    """Atomically take the failover slot (one per FAILOVER_MIN_INTERVAL_S across instances)."""
    now = int(time.time())
    try:
        table.update_item(
            Key={"cache_key": AZ_STATE_KEY},
            UpdateExpression="SET last_failover = :now",
            ConditionExpression="attribute_not_exists(last_failover) OR last_failover < :cut",
            ExpressionAttributeValues={
                ":now": now,
                ":cut": now - FAILOVER_MIN_INTERVAL_S,
            },
        )
        return True
    except Exception as e:  # noqa: BLE001 - the guard, or DynamoDB down
        code = (getattr(e, "response", None) or {}).get("Error", {}).get("Code", "")
        if code != "ConditionalCheckFailedException":
            logger.warning(
                "OBSERV nat.failover_claim_failed status=warn err=%s", code or e
            )
        return False


def _fail_subnet(ec2, table, nat: dict, reason: str) -> bool:
    """Mark the NAT's subnet bad and delete the NAT. False if the delete failed."""
    _mark_bad(table, nat["subnet"], f"{reason} ({nat['id']})")
    # counted: if AWS later shows this NAT as failed, it does not mark the subnet bad again
    seen = list(_az_state(table).get("failed_nats") or [])
    _set_az_state(table, failed_nats=(seen + [nat["id"]])[-20:])
    try:
        _delete_nat_gateway(ec2, nat["id"])
        return True
    except Exception as e:  # noqa: BLE001 - reported to the caller
        logger.warning(
            "OBSERV nat.delete_failed status=warn nat=%s err=%s", nat["id"], e
        )
        return False


# --- AZ state (one item, nat#azstate) ---------------------------------------------------------


def _az_table():
    """The api-cache table (the same one the lease lookups use)."""
    return _lease_table()


def _az_state(table) -> dict:
    try:
        return table.get_item(Key={"cache_key": AZ_STATE_KEY}).get("Item") or {}
    except Exception as e:  # noqa: BLE001 - no state: primary first
        logger.warning(
            "OBSERV nat.azstate_unreadable status=warn err=%s", type(e).__name__
        )
        return {}


def _set_az_state(table, **fields) -> None:
    try:
        # read strictly: an unreadable item must not be overwritten with only ``fields``
        current = table.get_item(Key={"cache_key": AZ_STATE_KEY}).get("Item") or {}
        item = {
            **current,
            **fields,
            "cache_key": AZ_STATE_KEY,
            "ttl": int(time.time()) + 7 * 86400,
        }
        table.put_item(Item=item)
    except Exception as e:  # noqa: BLE001 - placement still works, without memory
        logger.warning(
            "OBSERV nat.azstate_write_failed status=warn err=%s", type(e).__name__
        )


def _mark_bad(table, subnet: str, reason: str) -> None:
    if not subnet:
        return
    found = _az_state(table).get("bad")
    bad = dict(found) if isinstance(found, dict) else {}
    bad[subnet] = {
        "until": int(time.time()) + BAD_SUBNET_MINUTES * 60,
        "reason": reason[:200],
    }
    _set_az_state(table, bad=bad)
    logger.warning(
        "OBSERV nat.subnet_marked_bad status=warn subnet=%s minutes=%d reason=%s",
        subnet,
        BAD_SUBNET_MINUTES,
        reason,
    )


def _bad_until(state: dict, subnet: str) -> int:
    """When ``subnet``'s bad mark ends (0 if none or unreadable)."""
    try:
        return int(((state.get("bad") or {}).get(subnet) or {}).get("until", 0))
    except (AttributeError, TypeError, ValueError):
        return 0


def _subnet_order(state: dict) -> list:
    """Subnets not marked bad, in AZ order; then the bad ones, the one marked bad longest ago
    first (if every AZ looks bad, still try)."""
    now = time.time()
    good = [s for s in PUBLIC_SUBNETS if _bad_until(state, s) <= now]
    rest = sorted(
        (s for s in PUBLIC_SUBNETS if s not in good), key=lambda s: _bad_until(state, s)
    )
    return good + rest


def _alert_failover(table, subnet: str, state: dict) -> None:
    primary = PUBLIC_SUBNETS[0]
    reason = ((state.get("bad") or {}).get(primary) or {}).get("reason", "unknown")
    _set_az_state(table, failed_over=True)
    _notify(
        f"NAT failover — the NAT was created in {subnet} because the primary subnet {primary} "
        f"is marked unavailable ({reason}). Egress resumes when it is ready (a few minutes); "
        f"{primary} is tried again after {BAD_SUBNET_MINUTES} minutes. Check AWS Health for "
        "an Availability Zone event; nothing to do if it is an AWS outage"
    )


def _note_subnet_in_use(table, subnet: str) -> None:
    if subnet == (PUBLIC_SUBNETS[0] if PUBLIC_SUBNETS else subnet) and _az_state(
        table
    ).get("failed_over"):
        _set_az_state(table, failed_over=False)
        logger.info(
            "OBSERV nat.failback status=ok subnet=%s - the NAT is back in the primary subnet",
            subnet,
        )


def _release_orphan_eips(ec2) -> None:
    """Release every unassociated VPC EIP (each costs money): a replaced NAT's, once its
    deletion has finished, or one left by a failed create."""
    try:
        eips = ec2.describe_addresses(Filters=[{"Name": "domain", "Values": ["vpc"]}])
        for addr in eips.get("Addresses", []):
            if not addr.get("AssociationId"):
                ec2.release_address(AllocationId=addr["AllocationId"])
                logger.info("Released orphaned EIP: %s", addr["AllocationId"])
    except Exception as e:
        logger.warning("EIP cleanup failed: %s", e)


def _create_nat(ec2, subnet: str = ""):
    """Create NAT Gateway with fresh EIP (new IP each time for reputation isolation)."""
    _release_orphan_eips(ec2)  # first (prevents AddressLimitExceeded)

    eip_alloc = ec2.allocate_address(Domain="vpc")["AllocationId"]

    try:
        resp = ec2.create_nat_gateway(
            SubnetId=subnet or PUBLIC_SUBNETS[0],
            AllocationId=eip_alloc,
            TagSpecifications=[
                {
                    "ResourceType": "natgateway",
                    "Tags": [{"Key": "Name", "Value": NAT_TAG}],
                }
            ],
        )
    except Exception:
        try:  # an unused EIP costs money: release it before the next AZ is tried
            ec2.release_address(AllocationId=eip_alloc)
        except (
            Exception
        ) as e:  # noqa: BLE001 - the next create's orphan cleanup retries it
            logger.warning("Failed to release EIP %s: %s", eip_alloc, e)
        raise
    nat_id = resp["NatGateway"]["NatGatewayId"]
    logger.info("Created NAT: %s in %s", nat_id, subnet or PUBLIC_SUBNETS[0])
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
    # NAT 'available' != routable: the 0.0.0.0/0 route must also become ACTIVE
    # (there is a short propagation lag). Block here, inside the Lambda, until the
    # route is active so callers that get status=ready can actually egress. This
    # is where the readiness wait belongs — a long-lived Lambda/task, NOT the
    # trigger's 240s launch path (the old fail-closed livelock).
    _wait_route_active(ec2, nat_id)


def _route_active(ec2, nat_id) -> bool:
    """True if the env's private route table has an ACTIVE 0.0.0.0/0 route to nat_id."""
    try:
        rtbs = ec2.describe_route_tables(
            Filters=[{"Name": "tag:Name", "Values": [f"{ENV_NAME}-private-rt"]}]
        ).get("RouteTables", [])
        for rtb in rtbs:
            for r in rtb.get("Routes", []):
                if (
                    r.get("DestinationCidrBlock") == "0.0.0.0/0"
                    and r.get("NatGatewayId") == nat_id
                    and r.get("State") == "active"
                ):
                    return True
    except Exception as e:  # pragma: no cover - defensive
        logger.warning("Route-active check error: %s", e)
    return False


def _wait_route_active(ec2, nat_id, max_seconds: int = 120) -> bool:
    """Poll until the default route to nat_id is ACTIVE (propagation lag after NAT
    becomes available). Returns True when active, False on timeout (caller logs)."""
    for _ in range(max(1, max_seconds // 5)):
        if _route_active(ec2, nat_id):
            logger.info("Default route active -> %s", nat_id)
            return True
        time.sleep(5)
    logger.warning("Default route to %s not active after %ds", nat_id, max_seconds)
    return False


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

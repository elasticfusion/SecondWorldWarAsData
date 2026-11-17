# Networking Lifecycle

Dynamic NAT Gateway and VPC endpoint management for cost optimization.

**Last Updated:** 2026-11-16

---

## Overview

NAT Gateway ($0.045/hr + $0.045/GB) and VPC endpoints are created and destroyed on demand. Infrastructure only exists during active pipeline execution.

---

## Components

| Resource | Cost When Active | Managed By |
|----------|-----------------|------------|
| NAT Gateway | $0.045/hr | nat_manager Lambda |
| Elastic IP | $0 (associated) / $0.005/hr (idle) | nat_manager Lambda (reuses existing) |
| VPC Endpoints (Interface type) | $0.01/hr each | nat_manager Lambda |
| VPC Endpoints (Gateway type: S3, DynamoDB) | $0 | Always present (CloudFormation) |

**Interface VPC endpoints created on demand** (`INTERFACE_ENDPOINTS` in `lambda_handlers/nat_manager.py`): ECR API, ECR DKR, CloudWatch Logs, Secrets Manager, ECS, ECS agent, ECS telemetry (needed for GPU Batch instances to join the cluster), and EC2 and Lambda (for the in-VPC nat_teardown decider); see that list for any later additions.

---

## Lifecycle States

```
IDLE (no NAT, no interface endpoints, OpenSERP desired=0)
  │
  ├── trigger Lambda invokes nat_manager(create) ──→ ACTIVE
  │   (before launching any ECS task)
  │
  └── batch_poller invokes nat_manager(create) ──→ ACTIVE
      (before launching retrieve task)

ACTIVE (NAT up, endpoints up, tasks running)
  │
  ├── (1) nat_teardown Lambda (every 4 h; re-checks every 10 min while work runs)
  │       tears down only when NO demand remains ──→ IDLE
  │
  ├── (2) openserp_manager idle monitor (every 10 min) ──→ IDLE
  │       (no pipeline tasks for 30 min, OR NAT age > 2 h with no pipeline tasks)
  │
  └── (3) idle_backstop_reaper (hourly) ──→ IDLE
          (confirmed no-progress stall: object count under output/ unchanged, two strikes)
```

Phases 1/2/3 never tear NAT down; they only request `create`. Demand is `nat_manager._nat_demand_present()`: a live `nat#lease#` entry, a running non-OpenSERP ECS task, an OCR Batch job in flight (spot or on-demand queue), or a pending/ready Grok `batch_job#` row. A submitted Grok batch therefore keeps NAT up until it is retrieved, so the retrieve task has egress when the batch completes. Any error in the check keeps NAT.

---

## Creation Flow

**Triggered by:** trigger Lambda or batch_poller Lambda before launching ECS tasks.

```
nat_manager receives {"action": "create"}
  1. Check for existing NAT (tag: {env}-wwii-nat, state != deleted)
     → If exists and available: return immediately
  2. Look for unassociated EIP with tag {env}-wwii-nat-eip
     → If none: allocate new EIP
  3. Create NAT Gateway in public subnet with EIP
  4. Tag NAT: Name={env}-wwii-nat, CreatedAt=<timestamp>
  5. Wait for NAT state = "available" (up to NatWaitSeconds, default 180)
  6. Add route 0.0.0.0/0 → NAT to private route table
  7. Create the interface VPC endpoints (list above)
  8. Return NAT Gateway ID
```

**EIP reuse:** The Lambda looks for an existing EIP tagged `{env}-wwii-nat-eip` before allocating a new one. This avoids EIP churn and potential account limit issues.

---

## Teardown Triggers

### 1. nat_teardown (every 4 hours)

```
nat_teardown invoked by EventBridge (rate: 4 hours)
  1. If no NAT gateway exists → nothing to do
  2. If demand is present (leases, running tasks, OCR Batch jobs, pending Grok batches)
     → do nothing; schedule a one-shot re-check in 10 minutes (EventBridge Scheduler)
  3. Otherwise → nat_manager(delete)
  Any error → keep NAT
```

### 2. OpenSERP Manager Idle Monitor (every 10 minutes)

```
openserp_manager invoked by EventBridge (rate: 10 min)
  1. List running ECS tasks in cluster (excluding OpenSERP service itself)
  2. If pipeline tasks running → no-op
  3. If idle > 30 min (no pipeline tasks for 30 min):
     → Scale OpenSERP to 0
     → Invoke nat_manager(delete)
  4. GUARDRAIL: If NAT age > 2 hours with no pipeline tasks (a held lock skips it):
     → Force teardown
```

### 3. Idle backstop reaper (hourly)

Independent of the two above. Measures progress by the count of objects under `output/`; a single no-progress hour does nothing, a second consecutive one force-tears-down NAT and Fargate. A long batch that keeps writing is never killed.

The `phase2-complete` SNS subscription on nat_manager is a no-op (`_handle_sns_records`). The `TeardownDelayMinutes` parameter is still passed to the phase task definition as `TEARDOWN_DELAY_MINUTES`, but no Python code reads it.

---

## Deletion Flow

```
nat_manager receives {"action": "delete"}
  1. Find NAT Gateway by tag {env}-wwii-nat
     → If none found or already deleted: return success
  2. Delete NAT Gateway
  3. Wait for deletion (state = "deleted")
  4. Remove 0.0.0.0/0 route from private route table
  5. Delete the interface VPC endpoints (list above)
     → Gateway endpoints (S3, DynamoDB) are NOT deleted (free, always needed)
  6. Disassociate EIP but do NOT release it (reused on next create)
```

---

## Cost Impact

| Scenario | NAT Duration | Cost |
|----------|-------------|------|
| Full pipeline run (Phase 1→2→3) | ~2-4 hours | ~$0.09-0.18 |
| Batch mode | NAT stays up while a Grok batch is pending (demand check) | $0.045/hr while pending |
| Orphaned NAT (guardrail catches at 2h) | 2 hours max | $0.09 |
| NAT left overnight (bug) | Hourly backstop reaper and the 2h guardrail catch it | — |

**Monthly idle cost:** $0 (NAT destroyed when not in use).

---

## Troubleshooting

### NAT Stuck in "creating" State

```bash
# Check NAT status
aws ec2 describe-nat-gateways \
  --filter "Name=tag:Name,Values=dev-wwii-nat" \
  --query 'NatGateways[?State!=`deleted`].[NatGatewayId,State,CreateTime]' \
  --region us-east-1 --output table

# Force delete
aws lambda invoke --function-name dev-wwii-nat-manager \
  --payload '{"action": "delete"}' \
  --cli-binary-format raw-in-base64-out --region us-east-1 /tmp/out.json
cat /tmp/out.json
```

### NAT Won't Tear Down (Tasks Still Running)

```bash
# Check what tasks are running
aws ecs list-tasks --cluster dev-wwii-pipeline --desired-status RUNNING --region us-east-1

# Stop orphaned tasks
aws ecs stop-task --cluster dev-wwii-pipeline --task <task-id> --region us-east-1
```

### Leaked EIPs (Billing for Idle EIPs)

```bash
# Find unassociated EIPs
aws ec2 describe-addresses --filter "Name=domain,Values=vpc" \
  --query 'Addresses[?AssociationId==null].[AllocationId,PublicIp,Tags]' \
  --region us-east-1 --output table

# Release if no longer needed
aws ec2 release-address --allocation-id <eipalloc-xxx> --region us-east-1
```

### VPC Endpoints Orphaned After NAT Deletion

```bash
# List interface endpoints
aws ec2 describe-vpc-endpoints \
  --filters "Name=vpc-id,Values=<vpc-id>" "Name=vpc-endpoint-type,Values=Interface" \
  --query 'VpcEndpoints[].[VpcEndpointId,ServiceName,State]' \
  --region us-east-1 --output table

# Delete orphaned endpoints
aws ec2 delete-vpc-endpoints --vpc-endpoint-ids <vpce-xxx> --region us-east-1
```

---

## CloudFormation Parameters

| Parameter | Default | Description |
|-----------|---------|-------------|
| `NatWaitSeconds` | 180 | Max seconds to wait for NAT to become available |
| `TeardownDelayMinutes` | 30 | Passed as `TEARDOWN_DELAY_MINUTES`; no code reads it any more |

---

## Related

- [LAMBDA_FUNCTIONS.md](LAMBDA_FUNCTIONS.md) — nat_manager, nat_teardown and openserp_manager details
- [RUNBOOK.md](RUNBOOK.md) — Networking issues section
- [AWS_DEPLOYMENT.md](AWS_DEPLOYMENT.md) — Network architecture overview

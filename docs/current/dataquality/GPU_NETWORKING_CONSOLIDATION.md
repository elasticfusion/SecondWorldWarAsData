# GPU + Networking Consolidation (design for approval)

**Date:** 2026-09-29 · **Status (code checked 2026-11-17):** BUILT, with the ECS endpoints created dynamically rather than in `network.yaml`. Built: (1) the deploy-time GPU AZ probe (`scripts/deploy_aws.py::_probe_gpu_azs`, called at `deploy_aws.py:155`) passes `GpuAz1`/`GpuAz2` (`cloudformation/main.yaml:63-80`); `network.yaml` now has exactly two private and two public subnets (`PrivateSubnet1/2`, `PublicSubnet1/2`) in those AZs (empty param falls back to `!GetAZs`). (2) `network.yaml` creates the s3 and dynamodb gateway endpoints and the ecr.api, ecr.dkr, logs and secretsmanager interface endpoints in both private subnets. (3) The ecs, ecs-agent and ecs-telemetry interface endpoints (plus ec2, lambda and scheduler for the NAT-teardown decider) are created by `lambda_handlers/nat_manager.py` (`INTERFACE_ENDPOINTS`, `_ensure_endpoints`, `_create_endpoint`) in `PRIVATE_SUBNET_IDS` (both private subnets, `compute.yaml:667`). They are part of the NAT create/delete cycle, not always-on. **Still true:** OCR requests the NAT before submitting (`trigger_handler._ensure_nat_for_ocr`), and `nat_manager._nat_demand_present` keeps it up while OCR Batch jobs are in flight. The "Findings" below describe the state before the fix (6 private subnets, 2 endpoint subnets, no ECS endpoint); sections 2 and 4 describe the design, with the ECS endpoints implemented as noted above.
**Trigger:** OCR GPU jobs stall RUNNABLE forever. Root cause (diagnosed live): GPU
Batch instances launch into private subnets whose AZs are **not aligned** with the
VPC interface endpoints or a reachable NAT, and **there is no ECS interface
endpoint** — so instances boot (NVIDIA driver loads) but can't reach ECS to
register (`registeredContainerInstancesCount=0`), so no job ever places.

## Findings (evidence)

- Subnets: 6 private, one per AZ (1a–1f), all on ONE shared `PrivateRouteTable`
  (so the S3 + DynamoDB **gateway** endpoints + NAT route already cover all private
  subnets — that part is fine).
- **Interface endpoints** (ecr.api, ecr.dkr, logs, secretsmanager) live in only
  **2 subnets (1b + 1a)** — NOT in every GPU subnet. A GPU instance in 1e/1f has
  no endpoint path.
- **No ECS interface endpoint exists** — ECS-agent registration must traverse NAT.
- GPU family availability (probed): **g4dn ∩ g5 ∩ g6 = {1b, 1c, 1d, 1e, 1f}**
  (1a lacks g5). Existing `deploy_all.sh` GPU probe only checks `g5.xlarge` and
  passes ALL GPU-AZ subnets to Batch without aligning endpoints.

## Design (operator-approved direction)

**Consolidate to 2 AZ-subnets that support GPU in BOTH, and align everything to
them.** Operator constraints: GPU must be supported in both chosen subnets; add
S3 + ECS endpoints; probe for GPU support before building; portable across regions.

### 1. Deploy-time GPU AZ probe (portable, the single source of truth)
- Probe `describe-instance-type-offerings` for the forgiving family set
  (g4dn/g5/g6), **intersect** → AZs supporting all (or ≥1 of) the families.
- Select the first **min(2, len(gpu_azs))** AZs as the canonical GPU+CPU AZs.
- **Fail fast** if 0 GPU AZs ("no GPU capacity in region X"); **warn + single-AZ**
  if only 1 (loses multi-AZ capacity resilience but still works); 2 = ideal.
- Pass the derived AZs as CFN params so **subnets, endpoints, NAT, Batch CE, and
  Fargate all use the SAME 2 AZs**. No hardcoded `!Select` AZ indices for compute.

### 2. Endpoint coverage aligned to the 2 GPU AZs
- Interface endpoints (ecr.api, ecr.dkr, logs, secretsmanager, **+ NEW: ecs,
  ecs-agent, ecs-telemetry**) placed in **both** chosen GPU subnets.
- Adding the **ECS endpoint** lets GPU instances register WITHOUT NAT — fixes the
  stall at the source AND cuts NAT data cost (operator's earlier cost question).
- **S3 + DynamoDB gateway** endpoints already cover all private subnets (shared
  route table) — confirm the 2 chosen subnets are on that route table (they are).
- Endpoint security group must allow 443 from the GPU + Fargate SGs.

### 3. Compute alignment
- Batch GPU CE (spot + on-demand): subnets = the 2 chosen GPU AZ subnets.
- Fargate phases (CPU): same 2 subnets (both support Fargate too).
- Dispatcher SFN + trigger: same 2 subnets (already multi-subnet after the earlier
  multi-AZ fix — repoint to the canonical 2).

### 4. NAT
- With full endpoint coverage (esp. ECS), OCR should run **NAT-free** for AWS
  services. NAT stays ONLY for genuine internet egress (Grok API, OpenSERP). Keep
  the dynamic NAT for those, but GPU registration no longer depends on it.

## Blast radius / risk
- Touches `network.yaml` (endpoint subnets + ECS endpoint), `ocr.yaml` (CE subnets),
  `deploy_all.sh` / `deploy_aws.py` (probe → AZ params), dispatcher/compute subnet
  refs. Shared VPC — affects the whole stack, so deploy carefully + verify
  registration end-to-end.
- Region portability: the probe removes the us-east-1 AZ assumptions.

## Open question for operator
- OK to standardize on **1b + 1c** for dev (both all-GPU-family), or derive purely
  dynamically? Recommend: derive dynamically (portable), default first-2-GPU-AZs.

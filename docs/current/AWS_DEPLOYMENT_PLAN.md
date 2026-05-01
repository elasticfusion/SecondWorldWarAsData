# AWS Deployment Plan

**Status:** Planning  
**Last Updated:** 2026-04-19

---

## Architecture Overview

```
                    ┌─────────────┐
                    │  S3 Bucket  │ (source markdown + output JSON)
                    │  (content)  │
                    └──────┬──────┘
                           │ S3 Event
                           ▼
                    ┌─────────────┐
                    │     SNS     │
                    │   (topic)   │
                    └──────┬──────┘
                           │
              ┌────────────┼────────────┐
              ▼            ▼            ▼
        ┌───────────┐ ┌──────────┐ ┌──────────┐
        │  Lambda   │ │  Lambda  │ │  Lambda  │
        │  Phase 1  │ │ Phase 2  │ │ Phase 3  │
        │  (parse)  │ │(extract) │ │(enrich)  │
        └─────┬─────┘ └────┬─────┘ └────┬─────┘
              │             │            │
              ▼             ▼            ▼
        ┌─────────────────────────────────────┐
        │            S3 Bucket (output)       │
        └─────────────────────────────────────┘
              │             │            │
              ▼             ▼            ▼
        ┌───────────┐ ┌──────────┐ ┌──────────┐
        │ DynamoDB  │ │   ECS    │ │ DynamoDB │
        │  (cache)  │ │(OpenSERP)│ │or MongoDB│
        └───────────┘ └──────────┘ └──────────┘
```

---

## Design Decisions

### OpenSERP → ECS Fargate (not Lambda)

OpenSERP is a Go HTTP server that drives headless Chrome for web scraping. It cannot run on Lambda because:
- It's a long-running HTTP server maintaining a Chrome browser pool
- Chrome is ~400MB and needs warm startup (5-10s cold start per invocation)
- The pipeline makes many sequential HTTP requests to it per chapter

**ECS Fargate** is the right fit: existing Dockerfile works, Chrome stays warm, stateless, single task sufficient.

**On-demand deployment:** If OpenSERP is not running when a Lambda needs it, the Lambda triggers an ECS task start via the AWS SDK and waits for the health check to pass before proceeding.

### Lambda ↔ ECS Networking

Lambdas and ECS tasks run in the same VPC private subnets. Lambdas reach OpenSERP via an internal Application Load Balancer (ALB) that routes to the ECS target group. The ALB provides health checking and publishes `RequestCount` metrics to CloudWatch for idle monitoring.

### Storage: S3

All filesystem paths become S3 prefixes:

| Local Path | S3 Key Prefix | Trigger |
|---|---|---|
| `contentrepository/` | `s3://{bucket}/content/` | Upload triggers Phase 1 |
| `output/{Book}/*-parsed.json` | `s3://{bucket}/output/{Book}/` | Phase 1 write triggers Phase 2 |
| `output/dates/`, `places/`, `people/`, etc. | `s3://{bucket}/output/{entity}/` | Phase 2 writes |
| `output/bibliography/` | `s3://{bucket}/output/bibliography/` | Phase 2 write triggers Phase 3 |
| `filestore/` | `s3://{bucket}/filestore/` | Image downloads |
| `cache/api/` | DynamoDB table (not S3) | API response cache |
| `logs/` | CloudWatch Logs | Automatic |

### Cache: DynamoDB

Replace `diskcache` (SQLite-based) with a DynamoDB table:

```
Table: wwii-api-cache
  Partition Key: cache_key (String) — "{cache_type}#{sha256(prompt+temperature)}"
  Attributes:
    response: (String) — compressed JSON response
    created_at: (Number) — epoch timestamp
    ttl: (Number) — epoch expiry for DynamoDB TTL
```

### Database: DynamoDB option alongside MongoDB

Add a `--database` flag to `import_to_mongodb.py` (or a new `import_to_dynamodb.py`):

| MongoDB Collection | DynamoDB Table | Partition Key | Sort Key |
|---|---|---|---|
| `events` | `wwii-events` | `EventID` | — |
| `people` | `wwii-people` | `PersonID` | — |
| `places` | `wwii-places` | `PlaceID` | — |
| `dates` | `wwii-dates` | `DateID` | — |
| `people_groups` | `wwii-groups` | `GroupID` | — |
| `equipment` | `wwii-equipment` | `EquipmentID` | — |
| `weather` | `wwii-weather` | `WeatherID` | — |
| `logistics` | `wwii-logistics` | `LogisticsID` | — |
| `casualties` | `wwii-casualties` | `CasualtyID` | — |
| `maps` | `wwii-maps` | `MapID` | — |
| `bibliography` | `wwii-bibliography` | `BibliographyID` | — |

Single-table design is also viable but per-entity tables are simpler and match the existing MongoDB collection structure.

---

## Event Flow

### 1. Content Upload → Phase 1 (Parse)

```
User uploads markdown to s3://{bucket}/content/{Book}/
  → S3 Event Notification
    → SNS Topic (content-uploaded)
      → Lambda: phase1-parse
        Reads: s3://{bucket}/content/{Book}/*.md, *-meta.yaml
        Writes: s3://{bucket}/output/{Book}/*-parsed.json
```

### 2. Parsed File → Phase 2 (Extract)

```
Phase 1 writes *-parsed.json to S3
  → S3 Event Notification (suffix: -parsed.json)
    → SNS Topic (chapter-parsed)
      → Lambda: phase2-extract-chapter
        Reads: s3://{bucket}/output/{Book}/chapter*-parsed.json
        Calls: Grok API, Open-Meteo, OpenSERP (ECS)
        Writes: s3://{bucket}/output/{Book}/*-event.json
                s3://{bucket}/output/dates/*.json
                s3://{bucket}/output/places/*.json
                s3://{bucket}/output/people/*.json
                s3://{bucket}/output/people_groups/*.json
        Cache: DynamoDB (wwii-api-cache)
```

**Shared entity handling:** Each Lambda writes entities with unique keys to S3. S3 PutObject is atomic — no file locking needed. The `locked_json` pattern becomes: read from S3, modify, put back with conditional write (ETag-based optimistic locking via `If-Match`).

### 3. Entity Files → Dedup Gate → Phase 3 (Enrich)

```
Phase 2 writes entity files to S3
  → S3 Event Notification (prefix: output/people/, output/places/, etc.)
    → SNS Topic (entity-created)
      → Lambda: dedup-gate
        Reads: dedup/review_status.json
        If NOT complete: event dropped (Phase 3 blocked)
        If complete: forwards to Lambda: phase3-enrich

User reviews duplicates in Dedup Review UI (API Gateway + Basic Auth)
  → Clicks "Mark Complete"
    → SNS Topic (dedup-complete)
      → Lambda: dedup-gate
        Invokes Phase 3 for ALL entity files in output/people/, places/, groups/, bibliography/
```

Phase 3 does not start until the human review is done.

### 4. Enriched Files → Import

```
Manual trigger or scheduled
  → Lambda: import-to-database
    Reads: s3://{bucket}/output/**/*.json
    Writes: DynamoDB tables (or MongoDB via DocumentDB/Atlas)
```

### 5. OpenSERP On-Demand

```
Lambda needs OpenSERP but ECS task is not running
  → Lambda calls ecs:DescribeTasks to check
  → If no running task: ecs:RunTask to start OpenSERP
  → Poll health check (GET /health) with exponential backoff
  → Proceed with search requests
  → (Optional) Scale to zero after idle timeout via ECS auto-scaling
```

---

## Code Changes Required

### 1. Storage Abstraction Layer (new)

Create `src/utils/storage.py` with a `Storage` interface:

```python
class Storage(Protocol):
    def read_json(self, path: str) -> dict: ...
    def write_json(self, path: str, data: dict) -> None: ...
    def list_files(self, prefix: str, pattern: str) -> list[str]: ...
    def exists(self, path: str) -> bool: ...

class LocalStorage:
    """Current filesystem-based storage."""

class S3Storage:
    """S3-backed storage with same interface."""
```

All extraction modules switch from `Path` operations to `Storage` calls. Config determines which backend: `storage.backend: "local"` or `storage.backend: "s3"`.

### 2. Cache Abstraction Layer (new)

Create `src/utils/cache_backend.py`:

```python
class CacheBackend(Protocol):
    def get(self, key: str) -> Optional[str]: ...
    def set(self, key: str, value: str, ttl: int = 0) -> None: ...
    def delete(self, key: str) -> None: ...

class DiskCacheBackend:
    """Current diskcache-based backend."""

class DynamoCacheBackend:
    """DynamoDB-backed cache."""
```

`GrokClient` accepts a `CacheBackend` instead of a `cache_dir` Path.

### 3. Lambda Handlers (new)

Create `lambda_handlers/`:

```
lambda_handlers/
├── phase1_handler.py      # S3 event → parse chapter → write to S3
├── phase2_handler.py      # S3 event → extract entities → write to S3
├── phase3_handler.py      # S3 event → enrich entity → write to S3
├── import_handler.py      # Manual trigger → import to DynamoDB
├── openserp_manager.py    # Start/stop/health-check ECS OpenSERP task
├── dedup_ui_handler.py    # API Gateway → HTML review UI + merge/skip/exclude API
└── dedup_gate_handler.py  # SNS → check review status → conditionally invoke Phase 3
```

Each handler:
- Receives S3 event via SNS
- Initializes `S3Storage` and `DynamoCacheBackend`
- Calls existing extraction functions
- Writes results back to S3

### 4. DynamoDB Import (new)

Create `import_to_dynamodb.py` alongside existing `import_to_mongodb.py`. Same structure, different backend.

### 5. OpenSERP ECS Client (new)

Create `src/utils/openserp_client.py`:
- Check if OpenSERP ECS task is running
- Start task if needed, wait for health check
- Return the service URL for the pipeline to use
- Replace hardcoded `localhost:7001` with configurable endpoint

### 6. Config Changes

```yaml
# New AWS section in config.yaml
aws:
  enabled: false                    # Toggle local vs AWS mode
  region: "us-east-1"
  s3_bucket: "wwii-data-pipeline"
  cache_table: "wwii-api-cache"
  secrets_id: "wwii-pipeline/grok-api-key"
  openserp:
    cluster: "wwii-pipeline"
    service: "openserp"
    task_definition: "openserp"
    container_name: "openserp"
    health_check_url: "/health"
    startup_timeout: 120
  database:
    backend: "dynamodb"             # "dynamodb" or "mongodb"
    dynamodb_table_prefix: "wwii-"
    mongodb_uri: ""                 # Only if backend=mongodb
```

---

## CloudFormation Templates

### Template Structure

```
cloudformation/
├── main.yaml                # Root stack (nested stacks)
├── network.yaml             # VPC, subnets, security groups
├── storage.yaml             # S3 buckets, DynamoDB tables
├── compute.yaml             # Lambda functions, ECS cluster/service
├── events.yaml              # S3 notifications, SNS topics, subscriptions
└── iam.yaml                 # IAM roles and policies
```

### network.yaml

- VPC with 2 private subnets (Lambda + ECS)
- VPC endpoints for S3, DynamoDB, Secrets Manager (avoid NAT Gateway costs)
- Security group for OpenSERP ECS (allow inbound 7001 from Lambda SG)
- NAT Gateway (1 AZ) for outbound internet (Grok API, Wikipedia, search engines)

### storage.yaml

- S3 bucket: `wwii-data-pipeline` with versioning
- DynamoDB table: `wwii-api-cache` (PAY_PER_REQUEST, TTL enabled)
- DynamoDB tables: one per entity type (PAY_PER_REQUEST)
- S3 bucket policy: Lambda role access

### compute.yaml

- **Lambda: phase1-parse**
  - Runtime: python3.12, 512MB, 5min timeout
  - Layers: project dependencies
  - VPC: private subnets
  - Trigger: SNS (content-uploaded)

- **Lambda: phase2-extract**
  - Runtime: python3.12, 2048MB, 15min timeout
  - VPC: private subnets (needs OpenSERP access)
  - Trigger: SNS (chapter-parsed)
  - Environment: GROK_API_KEY from Secrets Manager

- **Lambda: phase3-enrich**
  - Runtime: python3.12, 1024MB, 15min timeout
  - VPC: private subnets
  - Trigger: SNS (entity-created)
  - Environment: GROK_API_KEY from Secrets Manager

- **Lambda: import-to-database**
  - Runtime: python3.12, 2048MB, 15min timeout
  - Trigger: manual (API Gateway or CLI)

- **Lambda: openserp-manager**
  - Runtime: python3.12, 256MB, 2min timeout
  - Permissions: ecs:RunTask, ecs:DescribeTasks

- **ECS Cluster + Fargate Service: openserp**
  - Image: built from `tools/openserp/Dockerfile`
  - CPU: 512, Memory: 1024
  - Port: 7001
  - Internal ALB: routes to target group `openserp-tg` on port 7001
  - Lambdas reach OpenSERP via ALB DNS: `http://openserp-alb.internal:7001`
  - ALB health check: GET /health (also provides CloudWatch `RequestCount` metric for idle monitoring)
  - Auto-scaling: min 0, max 1 (managed by idle monitor Lambda)

### events.yaml

- S3 event notifications → SNS topics
- SNS topics: `content-uploaded`, `chapter-parsed`, `entity-created`
- SNS → Lambda subscriptions with filter policies

### iam.yaml

- Lambda execution role: S3 read/write, DynamoDB read/write, Secrets Manager read, CloudWatch Logs, VPC access, SNS publish
- ECS task role: CloudWatch Logs
- ECS task execution role: ECR pull, CloudWatch Logs

---

## Deployment Script

`scripts/deploy_aws.py` — single entry point for deploying, updating, and tearing down the AWS infrastructure.

```bash
# Validate templates (runs cfn-lint)
python3 scripts/deploy_aws.py validate

# Deploy full stack (or update if exists)
python3 scripts/deploy_aws.py deploy --env dev --region us-east-1

# Deploy specific nested stack only
python3 scripts/deploy_aws.py deploy --env dev --stack network

# Check deployment status
python3 scripts/deploy_aws.py status --env dev

# Tear down everything
python3 scripts/deploy_aws.py destroy --env dev
```

### Parameters

| Flag | Default | Description |
|---|---|---|
| `--env` | `dev` | Environment name (prefixes all resource names) |
| `--region` | `us-east-1` | AWS region |
| `--stack` | (all) | Deploy only a specific nested stack: `network`, `storage`, `compute`, `events`, `iam` |
| `--profile` | (default) | AWS CLI profile name |
| `--dry-run` | false | Show what would be deployed without executing |

### What It Does

**`validate`:**
1. Runs `cfn-lint` on all templates in `cloudformation/`
2. Runs `aws cloudformation validate-template` for each template
3. Reports errors and warnings

**`deploy`:**
1. Validates templates (fails fast on errors)
2. Packages Lambda code into a zip, uploads to S3
3. Builds and pushes OpenSERP Docker image to ECR
4. Creates/updates the CloudFormation stack (`wwii-pipeline-{env}`)
5. Waits for stack completion, streams events
6. Outputs key resource ARNs (S3 bucket, DynamoDB tables, ECS cluster, Lambda functions)

**`status`:**
1. Shows stack status and last event
2. Lists running ECS tasks
3. Shows NAT Gateway state (active/deleted)
4. Shows recent Lambda invocation counts

**`destroy`:**
1. Empties S3 buckets (required before stack deletion)
2. Deletes the CloudFormation stack
3. Waits for deletion, streams events

### Dependencies

```bash
pip install cfn-lint boto3
```

---

## Implementation Phases

### Phase A: Storage Abstraction (local-compatible)

1. Create `Storage` protocol + `LocalStorage` + `S3Storage`
2. Create `CacheBackend` protocol + `DiskCacheBackend` + `DynamoCacheBackend`
3. Refactor `GrokClient` to accept `CacheBackend`
4. Refactor extraction modules to accept `Storage`
5. **Test locally** — everything still works with `LocalStorage` + `DiskCacheBackend`

### Phase B: Lambda Handlers

1. Create `lambda_handlers/` with thin wrappers
2. Create `openserp_client.py` for ECS management
3. Create `import_to_dynamodb.py`
4. **Test locally** with `aws lambda invoke` or SAM CLI

### Phase C: CloudFormation

1. Create templates, validate with `cfn-lint`
2. Deploy to dev account
3. Upload test content to S3, verify end-to-end flow
4. Tune Lambda memory/timeout based on actual execution

### Phase D: Production Hardening

1. Dead-letter queues for failed Lambda invocations
2. CloudWatch alarms for errors, throttling, duration
3. S3 lifecycle rules for cache/temp data
4. Cost monitoring with AWS Budgets

---

## Idle Monitoring and Cost-Saving Teardown

The NAT Gateway costs ~$32/month even when idle. OpenSERP ECS costs ~$0.05/hr when running. A scheduled Lambda monitors activity and tears down expensive resources when idle.

### EventBridge Rule (cron)

```
Schedule: rate(10 minutes)
Target: Lambda openserp-idle-monitor
```

### Lambda: openserp-idle-monitor

Logic:
1. Check ECS service desired count — if already 0, check NAT Gateway
2. Query ALB `RequestCount` metric for the OpenSERP target group over the last 30 minutes:
   ```
   CloudWatch → AWS/ApplicationELB → RequestCount
   Dimension: TargetGroup = openserp-tg
   Period: 1800s, Statistic: Sum
   ```
3. If Sum == 0 (no requests in 30 minutes):
   - Set ECS service desired count to 0 (stops container)
   - Delete NAT Gateway and release Elastic IP
   - Remove NAT Gateway route from private subnet route table
   - Store NAT Gateway config in SSM Parameter Store for re-creation
4. Log action to CloudWatch

### Re-creation on Pipeline Start

When a Lambda needs OpenSERP (or outbound internet):
1. `openserp-manager` Lambda checks if NAT Gateway exists
2. If not: create NAT Gateway, add route, wait for `available` state (~60s)
3. Start ECS task, wait for health check
4. Return OpenSERP endpoint URL

### CloudFormation Support

The NAT Gateway is created by CloudFormation initially but managed dynamically after that. The template uses a `Condition` to optionally create it:

```yaml
Conditions:
  CreateNatGateway: !Equals [!Ref InitialDeploy, "true"]
```

After first deploy, the idle monitor and openserp-manager handle lifecycle.

### What Gets Torn Down vs Kept

| Resource | Idle Cost | Tear Down? |
|---|---|---|
| VPC, subnets, route tables, SGs | $0 | No — free when idle |
| VPC endpoints (S3, DynamoDB) | $0 (gateway type) | No |
| VPC endpoints (Secrets Manager) | ~$7/month | Yes — tear down with NAT GW |
| NAT Gateway + Elastic IP | ~$32/month | **Yes** |
| ALB (internal) | ~$16/month | **Yes** — tear down with NAT GW |
| ECS cluster (no tasks) | $0 | No — free when idle |
| ECS Fargate task (OpenSERP) | ~$0.05/hr | **Yes** — scale to 0 |
| S3, DynamoDB | Pay-per-use | No |
| Lambda functions | $0 when idle | No |
| CloudWatch Logs | Storage only | No |

**Fully idle cost: ~$0/month** (all expensive resources torn down).

---

## Cost Estimate

| Service | Usage | Estimated Monthly Cost |
|---|---|---|
| Lambda | ~1000 invocations × 30s avg × 2GB | ~$1-2 |
| ECS Fargate (OpenSERP) | ~10 hours/month (on-demand) | ~$5-10 |
| S3 | ~10GB storage + requests | ~$1 |
| DynamoDB (cache) | ~10K reads/writes per run | ~$1 |
| DynamoDB (entity tables) | ~50K items, on-demand | ~$1 |
| NAT Gateway | 1 AZ, ~5GB data | ~$35 |
| ALB (internal) | Idle + ~1000 requests/month | ~$16 |
| Secrets Manager | 1 secret | ~$0.40 |
| **Total** | | **~$45-50/month** |

NAT Gateway dominates cost. Consider NAT instances or VPC endpoints for Grok API if cost-sensitive.

---

## Preserving Local Execution

All changes are behind the `aws.enabled` config flag. When `false` (default):
- `Storage` → `LocalStorage` (filesystem)
- `CacheBackend` → `DiskCacheBackend` (diskcache)
- OpenSERP → `localhost:7001`
- Database → MongoDB localhost

No existing CLI workflows change. `python3 phase1_parse.py && python3 phase2_retry.py` continues to work exactly as before.

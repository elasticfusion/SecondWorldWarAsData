# AWS Deployment Plan

**Status:** Implemented (dev)  
**Last Updated:** 2026-06-08

---

## Architecture Overview

```
                    ┌─────────────────┐
                    │   S3 Bucket     │ (source markdown + output JSON)
                    │  (data-pipeline)│
                    └───────┬─────────┘
                            │ S3 Event → SNS
                            ▼
                    ┌─────────────────┐
                    │ Trigger Lambda  │ (orchestrator)
                    └───────┬─────────┘
                            │ ecs:RunTask
              ┌─────────────┼─────────────┐
              ▼             ▼             ▼
        ┌───────────┐ ┌──────────┐ ┌──────────┐
        │ ECS Task  │ │ ECS Task │ │ ECS Task │
        │  Phase 1  │ │ Phase 2  │ │ Phase 3  │
        │  (parse)  │ │(extract) │ │(enrich)  │
        └─────┬─────┘ └────┬─────┘ └────┬─────┘
              │             │            │
              ▼             ▼            ▼
        ┌────────────────────────────────────────┐
        │       S3 Bucket (output JSON)          │
        └────────────────────────────────────────┘
              │             │
              ▼             ▼
        ┌───────────┐ ┌──────────────────┐
        │ DynamoDB  │ │  Batch Poller    │
        │  (cache)  │ │  Lambda (15 min) │
        └───────────┘ └──────────────────┘
```

### Key Architectural Decisions

- **ECS Fargate tasks** (not Lambda) run the pipeline phases — they need >15 min runtime, large memory, and local filesystem for `ecs_entrypoint.py` S3 sync pattern.
- **Trigger Lambda** orchestrates ECS task launches based on S3 events, manual invocations, and scheduled lock checks.
- **Batch API** — Phase 2 and Phase 3 submit to Grok Batch API (50% cost savings). A Batch Poller Lambda checks completion every 15 minutes.
- **NAT Gateway** is dynamically managed (created/destroyed) to avoid idle costs (~$32/month).
- **Fargate Spot** (4:1 ratio over regular Fargate) for cost savings with spot termination recovery.

---

## Implemented Components

### CloudFormation Templates

```
cloudformation/
├── main.yaml          # Root stack (nested stacks)
├── network.yaml       # VPC, subnets, security groups, VPC endpoints, SSM params
├── storage.yaml       # S3 bucket, DynamoDB tables (cache + 10 entity tables), budgets
├── compute.yaml       # ECS cluster, task definitions, Lambda functions, API Gateways
├── events.yaml        # S3 notifications, SNS topics, EventBridge rules
└── iam.yaml           # IAM roles and policies
```

### Lambda Functions

| Function | Trigger | Purpose |
|----------|---------|---------|
| `trigger` | SNS (S3 events), EventBridge (hourly), ECS state changes | Orchestrates ECS task launches, manages locks, spot recovery |
| `batch-poller` | EventBridge (every 15 min) | Polls Grok Batch API for job completion, triggers retrieve tasks |
| `nat-manager` | SNS (phase2-complete), direct invoke | Creates/destroys NAT Gateway and routes dynamically |
| `openserp-manager` | Direct invoke from trigger | Starts/stops OpenSERP ECS service |
| `dedup-ui` | API Gateway (Basic Auth) | HTML review UI for duplicate entity resolution |
| `dedup-gate` | SNS (entity-created) | Gates Phase 3 until dedup review complete |
| `dedup-auth` | API Gateway authorizer | Basic Auth token validation |
| `metrics` | API Gateway (Basic Auth) | Pipeline metrics dashboard API |

### ECS Task Definitions

| Task | CPU/Memory | Command | Purpose |
|------|-----------|---------|---------|
| `phase1-parse` | 512/1024 | `phase1_parse.py` | Parse markdown → JSON |
| `phase2-extract` | 1024/2048 | `phase2_extract.py --batch` | Extract entities via Grok Batch API |
| `phase3-enrich` | 512/1024 | `phase3_enrich_data.py --batch` | Enrich entities with external data |
| `import` | 512/1024 | `import_to_dynamodb.py` | Import JSON to DynamoDB tables |
| `openserp` | 512/1024 | `serve --host 0.0.0.0 --port 7001 --raw` | Web search service (headless Chrome) |

### ECS Entrypoint Pattern

`ecs_entrypoint.py` runs in each pipeline ECS task:
1. Syncs S3 content to local `/tmp/pipeline`
2. Runs the phase script with local filesystem
3. Incrementally syncs output back to S3 (every 120s)
4. Handles SIGTERM for Spot termination (emergency sync in 30s window)
5. Uses S3 lock files to prevent concurrent runs

---

## Network Architecture

- **VPC** with CIDR 10.0.0.0/16
- 2 private subnets (ECS tasks + Lambda) in separate AZs
- 1 public subnet (NAT Gateway when active)
- Internet Gateway for public subnet
- **VPC Endpoints** (Gateway type, free):
  - S3
  - DynamoDB
- **NAT Gateway** — dynamically managed:
  - Created by `nat-manager` Lambda when outbound internet needed (Grok API, Wikipedia, etc.)
  - Destroyed after pipeline completes (via Phase2Complete SNS topic)
  - State stored in SSM Parameter Store for re-creation
- **Security Groups**:
  - `LambdaSG` — Lambda + pipeline ECS tasks (egress all)
  - `OpenSerpSG` — OpenSERP ECS service (ingress 7001 from LambdaSG)

---

## Storage

### S3 Bucket: `{env}-wwii-data-pipeline`

| Prefix | Purpose | Lifecycle |
|--------|---------|-----------|
| `content/` | Source markdown documents | — |
| `output/{Book}/` | Parsed + event JSON per book | → Standard-IA at 30d, Glacier-IR at 90d |
| `output/people/`, `places/`, `dates/`, etc. | Central entity files | → Standard-IA at 30d |
| `cache/` | API response cache | Expires after 90d |
| `tmp/` | Temporary working files | Expires after 7d |
| `batch/` | Grok Batch API job tracking | — |
| `review/` | Dedup review state | — |
| `filestore/` | Downloaded images and maps | — |

Versioning enabled. Old versions expire after 30 days.

### DynamoDB Tables

| Table | Partition Key | Purpose |
|-------|--------------|---------|
| `{env}-wwii-api-cache` | `cache_key` (String) | API response cache with TTL |
| `{env}-wwii-people` | `PersonID` | People entities |
| `{env}-wwii-groups` | `GroupID` | People groups |
| `{env}-wwii-places` | `PlaceID` | Places |
| `{env}-wwii-dates` | `DateID` | Dates |
| `{env}-wwii-equipment` | `EquipmentID` | Equipment |
| `{env}-wwii-weather` | `WeatherID` | Weather |
| `{env}-wwii-logistics` | `LogisticsID` | Logistics |
| `{env}-wwii-casualties` | `CasualtyID` | Casualties |
| `{env}-wwii-maps` | `MapID` | Maps |
| `{env}-wwii-bibliography` | `BibliographyID` | Bibliography/citations |

All tables: PAY_PER_REQUEST billing, point-in-time recovery on cache table.

---

## Event Flow

### 1. Content Upload → Phase 1 (Parse)

```
Upload to s3://{bucket}/content/{Book}/
  → S3 Event Notification
    → SNS: {env}-wwii-content-uploaded
      → Trigger Lambda
        → Invokes nat-manager (ensure NAT exists)
        → ecs:RunTask phase1-parse
          → ecs_entrypoint.py syncs, runs phase1_parse.py, syncs output
```

### 2. Phase 1 Complete → Phase 2 (Extract via Batch API)

```
Phase 1 writes *-parsed.json → S3
  → SNS: {env}-wwii-chapter-parsed
    → Trigger Lambda
      → ecs:RunTask phase2-extract --batch
        → Submits all extraction requests to Grok Batch API
        → Writes batch job IDs to S3 (batch/ prefix)
        → Publishes to Phase2Complete topic when submission done

Batch Poller Lambda (every 15 min):
  → Checks pending batch jobs via Grok API
  → When job complete: triggers ECS retrieve task to process results
  → Runs dedup, writes entities to S3
```

### 3. Dedup Gate → Phase 3 (Enrich)

```
Entity files written to S3
  → SNS: {env}-wwii-entity-created
    → Dedup Gate Lambda
      → If dedup review NOT complete: blocked
      → If complete: triggers Phase 3

Human reviews in Dedup UI (API Gateway + Basic Auth):
  → Marks review complete
    → SNS: {env}-wwii-dedup-complete
      → Trigger Lambda: launches Phase 3 ECS task
```

### 4. Phase Completion → Teardown

```
Phase 2/3 complete
  → SNS: {env}-wwii-phase2-complete
    → nat-manager Lambda (subscriber)
      → Destroys NAT Gateway and Elastic IP
      → Removes route from private route table
    → Email notification (if configured)
```

### 5. Spot Termination Recovery

```
ECS task stopped due to Spot reclamation
  → EventBridge rule detects ECS Task State Change (stoppedReason: "Your Spot Task...")
    → Trigger Lambda
      → Clears stale lock
      → Re-launches the task

Hourly EventBridge rule:
  → Trigger Lambda with action=check_locks
    → Detects locks >2 hours old (stale from crashes)
    → Clears them to unblock pipeline
```

---

## Cost Management

### Dynamic NAT Gateway Lifecycle

NAT Gateway (~$32/month) is NOT persistent. Managed dynamically:
- **Created** by `nat-manager` when pipeline needs outbound internet
- **Destroyed** after pipeline completes (Phase2Complete SNS trigger)
- Config stored in SSM Parameter Store for re-creation

### Fargate Spot

ECS cluster uses Fargate Spot (4:1 weight ratio):
- ~70% cost savings on compute
- Spot termination recovery via EventBridge + trigger Lambda

### AWS Budgets

Monthly budget alert at $75 (80% threshold) via SNS alarm topic.

### Idle Cost: ~$0/month

| Resource | When Idle | Cost |
|----------|-----------|------|
| VPC, subnets, route tables, SGs | Always exist | $0 |
| VPC Endpoints (S3, DynamoDB) | Gateway type | $0 |
| NAT Gateway | Destroyed | $0 |
| ECS Cluster (no tasks) | Exists | $0 |
| OpenSERP (desired=0) | No tasks | $0 |
| Lambda functions | Not invoked | $0 |
| S3 | Storage only | ~$0.50 |
| DynamoDB | No reads/writes | $0 |

### Active Run Cost Estimate

| Service | Per-run usage | Cost |
|---------|--------------|------|
| ECS Fargate Spot (pipeline tasks) | ~2-4 hours | ~$2-5 |
| NAT Gateway (data transfer) | ~5GB | ~$5 |
| Grok Batch API | Per chapter | Variable |
| DynamoDB | ~10K reads/writes | ~$0.01 |
| Lambda invocations | ~100 | ~$0.01 |

---

## Code Abstractions (Implemented)

### Storage (`src/utils/storage.py`)

Protocol-based abstraction — `LocalStorage` or `S3Storage` selected by config:
```python
class Storage(Protocol):
    def read_json(self, path: str) -> dict: ...
    def write_json(self, path: str, data: dict) -> None: ...
    def list_files(self, prefix: str, pattern: str) -> list[str]: ...
    def exists(self, path: str) -> bool: ...
```

### Cache (`src/utils/cache_backend.py`)

Protocol-based abstraction — `DiskCacheBackend` or `DynamoCacheBackend`:
```python
class CacheBackend(Protocol):
    def get(self, key: str) -> Optional[str]: ...
    def set(self, key: str, value: str, ttl: int = 0) -> None: ...
```

### OpenSERP Client (`src/utils/openserp_client.py`)

Handles ECS service start/stop and health checks. Replaces hardcoded localhost.

### Batch API (`src/utils/batch_api.py`)

Grok Batch API submission and result retrieval with S3-based job tracking.

---

## Deployment

### Prerequisites

- AWS account with appropriate permissions
- ECR repositories for pipeline and OpenSERP images
- Secrets Manager secret: `{env}-wwii-pipeline/grok-api-key`
- Secrets Manager secret: `{env}-wwii-pipeline/dedup-auth`
- S3 bucket for CloudFormation templates and Lambda code

### Deploy

```bash
# Package and upload Lambda code
zip -r code.zip lambda_handlers/ src/ scripts/ phase*.py ecs_entrypoint.py config.yaml requirements.txt
aws s3 cp code.zip s3://{template-bucket}/lambda/code.zip

# Build and push Docker images
docker build -t wwii-pipeline .
docker tag wwii-pipeline:latest {account}.dkr.ecr.us-east-1.amazonaws.com/wwii-pipeline:latest
docker push {account}.dkr.ecr.us-east-1.amazonaws.com/wwii-pipeline:latest

# Deploy CloudFormation
aws cloudformation deploy \
  --template-file cloudformation/main.yaml \
  --stack-name dev-wwii-pipeline \
  --parameter-overrides \
    EnvironmentName=dev \
    TemplateBucket={template-bucket} \
    LambdaCodeBucket={template-bucket} \
    OpenSerpImageUri={account}.dkr.ecr.us-east-1.amazonaws.com/wwii-openserp:latest \
    PipelineImageUri={account}.dkr.ecr.us-east-1.amazonaws.com/wwii-pipeline:latest \
  --capabilities CAPABILITY_NAMED_IAM
```

### Trigger a Run

```bash
# Upload content to trigger pipeline
aws s3 sync contentrepository/BookName/ s3://dev-wwii-data-pipeline/content/BookName/

# Or manual trigger via Lambda
aws lambda invoke --function-name dev-wwii-trigger \
  --payload '{"source":"manual","book":"BookName","phase":"1"}' /dev/stdout
```

### Monitor

- **CloudWatch Dashboard**: `dev-wwii-pipeline-logs` — run summaries, batch submissions, errors, token usage
- **Dedup Review UI**: `https://{api-id}.execute-api.us-east-1.amazonaws.com/app/dedup`
- **Metrics API**: `https://{api-id}.execute-api.us-east-1.amazonaws.com/app/metrics`

---

## Preserving Local Execution

All AWS behavior is behind `aws.enabled: false` (default in config.yaml):
- Storage → local filesystem
- Cache → diskcache (SQLite)
- OpenSERP → localhost:7001
- Batch API → direct Grok API calls

```bash
# Local execution unchanged
python phase1_parse.py && python phase2_extract.py && python phase3_enrich_data.py
```

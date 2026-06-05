# AWS Lambda Functions

> **Note:** Function names use the pattern `{env}-wwii-{name}` (e.g., `dev-wwii-trigger`). URLs and ARNs change with each deployment. Use `aws cloudformation describe-stacks` to get current values.

---

## Function Overview

| Function | Trigger | Schedule | Purpose |
|----------|---------|----------|---------|
| trigger | SQS (S3 notifications) | 1 hour (stale lock check) | Orchestrates pipeline — launches ECS tasks |
| batch-poller | — | 15 minutes | Checks Grok batch job status, triggers retrieval |
| nat-manager | SNS, Lambda invoke | — | Creates/deletes NAT gateway + VPC endpoints |
| openserp-manager | — | 10 minutes | Idle monitoring, force-teardown NAT if stale |
| metrics | API Gateway GET | — | Pipeline metrics dashboard |
| dedup-gate | — | — | Blocks Phase 3 until dedup review is complete |
| dedup-ui | API Gateway (GET/POST) | — | Web UI for dedup review |
| dedup-auth | API Gateway Authorizer | — | Basic auth for dedup + metrics APIs |
| s3-notif-setup | CloudFormation Custom Resource | — | Configures S3 bucket notifications |

---

## Detailed Descriptions

### trigger (`dev-wwii-trigger`)

**Purpose:** Central orchestrator. Decides what to run and when.

**Invocation sources:**
- SQS queue (from SNS topics: content-uploaded, chapter-parsed, entity-created, dedup-complete)
- EventBridge schedule (hourly stale lock check + pending queue reconciliation)
- Manual: `aws lambda invoke --function-name dev-wwii-trigger --payload '{"source": "manual", "book": "BookName", "phase": "2"}'`

**What it does:**
- Routes S3 events by topic (content uploaded → queue for Phase 1, parsed file → queue for Phase 2)
- Checks for running ECS tasks before launching new ones (single-task concurrency)
- Extracts `BOOK_NAME` from pending queues and passes to ECS tasks
- Reconciles stale locks (hourly) — detects dead tasks and re-triggers
- Detects "dedup complete but Phase 3 never ran" state

**Environment variables:** `CLUSTER`, `PHASE1_TASK_DEF`, `PHASE2_TASK_DEF`, `PHASE3_TASK_DEF`, `CACHE_TABLE`, `S3_BUCKET`

---

### batch-poller (`dev-wwii-batch-poller`)

**Purpose:** Polls Grok Batch API for completed jobs and triggers ECS retrieve tasks.

**Schedule:** Every 15 minutes.

**What it does:**
- Scans DynamoDB for batch jobs with `status: "pending"`
- Checks each job's status via the Grok API (`GET /batch/{id}`)
- When a batch completes: invokes nat-manager (async), waits for NAT, launches ECS retrieve task
- Updates job status in DynamoDB (pending → complete → retrieved, or failed)

**Environment variables:** `CACHE_TABLE`, `CLUSTER`, `PHASE2_TASK_DEF`, `NAT_MANAGER_ARN`

---

### nat-manager (`dev-wwii-nat-manager`)

**Purpose:** Dynamic networking lifecycle — creates/deletes NAT gateway and VPC endpoints on demand.

**Invocation sources:**
- Lambda invoke from trigger/batch-poller (action: "up")
- SNS subscription (Phase 2 complete → action: "down")
- EventBridge Scheduler (delayed teardown)

**What it does:**
- `{"action": "up"}` — Creates NAT gateway, allocates EIP, updates route tables, ensures VPC endpoints (ECR, logs, S3)
- `{"action": "down"}` — Deletes NAT, releases EIP, removes routes, deletes non-essential VPC endpoints
- `{"action": "status"}` — Returns current NAT state

**Cost impact:** NAT gateway = $0.045/hr + $0.045/GB processed. Only runs during active pipeline execution.

---

### openserp-manager (`dev-wwii-openserp-manager`)

**Purpose:** Cost-saving idle monitor. Tears down networking and OpenSERP when pipeline is idle.

**Schedule:** Every 10 minutes.

**What it does:**
- Checks for running ECS tasks (excluding OpenSERP itself)
- If no pipeline tasks running and NAT has been idle >30 min: scales OpenSERP to 0, tears down NAT
- **Guardrail:** Force teardown if NAT has been up >2 hours with no pipeline tasks (prevents overnight cost leaks)
- Checks NAT status via EC2 `describe_nat_gateways` with `{env}-nat` tag

---

### metrics (`dev-wwii-metrics`)

**Purpose:** Pipeline metrics API for monitoring dashboards.

**Endpoint:** `GET https://{api-id}.execute-api.us-east-1.amazonaws.com/prod/metrics`  
**Auth:** Basic auth (same credentials as dedup UI)

**What it does:**
- Returns pipeline run history, entity counts, cost metrics
- Reads from DynamoDB cache table and S3 output

---

### dedup-gate (`dev-wwii-dedup-gate`)

**Purpose:** Gate between Phase 2 and Phase 3. Blocks enrichment until duplicates are reviewed.

**What it does:**
- Reads `dedup/review_status.json` from S3
- Returns whether review is complete
- Called by trigger Lambda before launching Phase 3

---

### dedup-ui (`dev-wwii-dedup-ui`)

**Purpose:** Web UI for human review of potential duplicates.

**Endpoint:** `GET/POST https://{api-id}.execute-api.us-east-1.amazonaws.com/app/dedup`  
**Auth:** Basic auth (credentials in Secrets Manager: `{env}-wwii-pipeline/dedup-auth`)

**What it does:**
- `GET /dedup` — Renders HTML review page with duplicate pairs
- `POST /dedup` — Processes merge/skip/exclude actions
- Reads dedup reports from S3, writes merge decisions back
- On "mark complete": updates `review_status.json` and publishes to `dedup-complete` SNS topic

---

### dedup-auth (`dev-wwii-dedup-auth`)

**Purpose:** API Gateway custom authorizer for both dedup and metrics APIs.

**What it does:**
- Validates Basic auth credentials from `Authorization` header
- Credentials stored in environment variable `AUTH_TOKEN` (format: `user:pass`)
- Returns IAM policy allowing/denying API access

---

### s3-notif-setup (`dev-wwii-s3-notif-setup`)

**Purpose:** CloudFormation custom resource that configures S3 bucket event notifications.

**What it does:**
- Called by CloudFormation on stack create/update/delete
- Configures two S3 → SNS notifications:
  - `contentrepository/*.md` → `content-uploaded` topic
  - `output/*-parsed.json` → `chapter-parsed` topic
- Bump `ConfigVersion` property in CloudFormation to force re-execution

---

## Endpoints

### Human-Facing (Browser)

| URL | Purpose | Auth |
|-----|---------|------|
| `https://{dedup-api-id}.execute-api.{region}.amazonaws.com/app/dedup` | Dedup review UI | Basic (browser prompt) |
| `https://{metrics-api-id}.execute-api.{region}.amazonaws.com/prod/metrics` | Pipeline metrics dashboard | Basic (browser prompt) |

### Programmatic (Machine-to-Machine)

| Invocation | Purpose | Caller |
|------------|---------|--------|
| `trigger` via SQS | S3 event routing | S3 → SNS → SQS (automatic) |
| `trigger` via EventBridge | Stale lock reconciliation | Scheduled (hourly) |
| `trigger` via `aws lambda invoke` | Manual pipeline trigger | Operator |
| `batch-poller` via EventBridge | Poll Grok batch status | Scheduled (15 min) |
| `nat-manager` via Lambda invoke | Create/delete NAT | batch-poller, trigger |
| `nat-manager` via SNS | Teardown after Phase 2 | phase2-complete topic |
| `openserp-manager` via EventBridge | Idle cost monitoring | Scheduled (10 min) |
| `dedup-gate` via Lambda invoke | Check review status | trigger |
| `s3-notif-setup` via CloudFormation | Configure S3 notifications | Stack deploy only |

Get current URLs:
```bash
aws cloudformation describe-stacks --stack-name wwii-pipeline-dev --region us-east-1 \
  --query "Stacks[0].Outputs[?contains(OutputKey,'Api')].{key:OutputKey,url:OutputValue}" --output table
```

---

## EventBridge Schedules

| Schedule | Rate | Target |
|----------|------|--------|
| `dev-wwii-stale-lock-check` | 1 hour | trigger (with `{"source": "scheduled"}`) |
| `dev-wwii-batch-poller-schedule` | 15 min | batch-poller |
| `dev-wwii-openserp-idle-monitor` | 10 min | openserp-manager |

---

## SNS Topics

| Topic | Publishers | Subscribers |
|-------|-----------|-------------|
| `dev-wwii-content-uploaded` | S3 (contentrepository/*.md) | SQS → trigger |
| `dev-wwii-chapter-parsed` | S3 (output/*-parsed.json) | SQS → trigger |
| `dev-wwii-entity-created` | S3 (output/entity files) | SQS → trigger |
| `dev-wwii-phase2-complete` | ECS task | nat-manager, email |
| `dev-wwii-dedup-complete` | dedup-ui | trigger |
| `dev-wwii-alarms` | CloudWatch Alarms | email |

---

## Cost Notes

- **Batch poller:** Runs every 15 min regardless of activity (~$0.30/month). Cold starts each time.
- **OpenSERP manager:** Runs every 10 min (~$0.20/month). Most invocations are no-ops.
- **Trigger:** Only runs on S3 events + hourly schedule. Minimal cost.
- **NAT gateway:** $0.045/hr when active. The 2-hour guardrail caps worst-case to ~$0.09 per orphan.

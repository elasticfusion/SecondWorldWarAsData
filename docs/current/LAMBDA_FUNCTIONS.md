# AWS Lambda Functions

> **Note:** Function names use the pattern `{env}-wwii-{name}` (e.g., `dev-wwii-trigger`). URLs and ARNs change with each deployment. Use `aws cloudformation describe-stacks` to get current values.

---

## Function Overview

| Function | Trigger | Schedule | Purpose |
|----------|---------|----------|---------|
| trigger | SQS (S3 notifications) | configurable (ReconciliationIntervalMinutes, default 15 min) | Orchestrates pipeline — launches ECS tasks |
| batch-poller | — | 5 minutes | Checks Grok batch job status, triggers retrieval |
| nat-manager | Lambda invoke (SNS records are a no-op) | — | Creates NAT gateway + VPC endpoints; delete primitive (called by nat-teardown, idle-backstop-reaper, openserp-manager) |
| nat-teardown | EventBridge | 4 hours (+ 10-min self-rescheduled re-check while work runs) | Scheduled NAT/endpoint teardown decider (the `nat_teardown.py` docstring names it the sole owner; `openserp_manager` and the reaper still carry their own guardrails) |
| openserp-manager | EventBridge | 10 minutes | Idle monitoring, scales OpenSERP to 0, force-tears-down NAT up >2 h with no pipeline tasks |
| openserp-worker-autoscaler | EventBridge | `WorkerAutoscalerIntervalMinutes` | Scales the OpenSERP SQS worker service on queue depth |
| idle-backstop-reaper | EventBridge | 1 hour | Force-teardown of NAT/Fargate on a confirmed no-progress stall |
| ocr-merge | EventBridge (Batch job SUCCEEDED on a Chandra queue) + ECS task state change | — | Merges OCR chunks, promotes chapters to `contentrepository/` |
| ocr-spot-controller | EventBridge | 1 hour | Routes spot-starved OCR jobs to the on-demand queue (48 h cap) |
| slack-formatter | SNS (`phase2-complete`) | — | Wraps free-form alerts in Chatbot's schema and republishes to the Slack topic |
| s3-validation-backstop | EventBridge rule (S3 `Object Created`, prefix `output/`) | — | Schema-validates every object written under `output/`; tags invalid ones `quarantine=true` |
| metrics | API Gateway GET | — | Pipeline metrics dashboard |
| dedup-gate | none wired in CloudFormation (manual invoke only) | — | Checks `dedup/review_status.json` before forwarding to Phase 3. The live gate is `ecs_entrypoint._dedup_gate_decision`; the `dedup-complete` topic goes to `trigger` |
| dedup-ui | API Gateway (GET/POST) | — | Web UI for dedup review |
| mdreview-ui | API Gateway | — | OCR markdown-review UI (page image beside editable snippet) |
| urlreview-ui | API Gateway | — | Review UI for textual fetch finds (Phase 3) |
| dedup-auth | API Gateway Authorizer | — | Basic auth for the dedup/mdreview/urlreview API (`DedupApi`) and the metrics API |
| enumerate-pending / clamp-pool / human-gate | Step Functions (`cloudformation/dispatcher.yaml`) | — | Dispatcher state-machine helpers (`lambda_handlers/dispatcher_handlers.py`) |
| s3-notif-setup | CloudFormation Custom Resource | — | Configures S3 bucket notifications |

---

## Detailed Descriptions

### trigger (`dev-wwii-trigger`)

**Purpose:** Central orchestrator. Decides what to run and when.

**Invocation sources:**
- SQS queue (from SNS topics: content-uploaded, chapter-parsed, entity-created, dedup-complete)
- EventBridge schedule (configurable stale lock check + pending queue reconciliation, default every 15 min)
- Manual: `aws lambda invoke --function-name dev-wwii-trigger --payload '{"source": "manual", "book": "BookName", "phase": "2"}'`

**What it does:**
- Routes S3 events by topic (content uploaded → ingestion routing by media type: PDF/scan → Chandra OCR, EPUB/DOCX/HTML → convert, video, structured reference docs → OOB track, chapters → Phase 1; parsed file → queue for Phase 2)
- Checks for running ECS tasks before launching new ones (single-task concurrency)
- Extracts `BOOK_NAME` from pending queues and passes to ECS tasks
- Fires an async NAT warm-up and launches immediately; Phase 2/3 check NAT readiness themselves
- Reconciles stale locks — detects dead tasks and re-triggers
- Detects "dedup complete but Phase 3 never ran" state

**Environment variables (see `TriggerFunction` in `cloudformation/compute.yaml`):** `ECS_CLUSTER`, `PHASE0_TASK_DEF`, `PHASE0_VIDEO_TASK_DEF`, `PHASE1_TASK_DEF`, `PHASE2_TASK_DEF`, `PHASE3_TASK_DEF`, `AV_SCAN_ENABLED`, `AV_SCAN_TASK_DEF`, `CACHE_TABLE`, `S3_BUCKET`, `NAT_MANAGER_FN`, `MULTI_DOC_ENABLED`, `DISPATCHER_STATE_MACHINE_ARN`, `POOL_MIN`, `POOL_MAX`

---

### batch-poller (`dev-wwii-batch-poller`)

**Purpose:** Polls Grok Batch API for completed jobs and triggers ECS retrieve tasks.

**Schedule:** Every 5 minutes (configurable via `BatchPollerIntervalMinutes` CloudFormation parameter).

**State machine:** Jobs flow through: `in_task` (a running task is waiting for the batch) → `pending` → `ready` → `retrieved` (set by the retrieve task; or `failed`). An `in_task` job older than `BATCH_IN_TASK_ADOPT_SECONDS` (2 h) is adopted as `pending`. Phase 2 first waits in-task for the batch (`batch.in_task_wait_minutes`, 5 min); only a batch still unanswered is left to this Lambda.

**What it does:**
- Scans DynamoDB for `batch_job#` rows with `status: "pending"` or `status: "ready"`
- For pending jobs: checks status via Grok API (`GET /batch/{id}`)
- When a batch completes: atomically marks as `ready`, invokes nat-manager (synchronous), waits for NAT, launches the Phase 2 ECS task with `--retrieve-only` (the task then starts the next batch round, up to 8 rounds)
- For ready jobs (retrieve previously failed): retries launching the retrieve task
- Guards against duplicate retrieve tasks (checks for running ECS tasks before launching)
- Updates job status in DynamoDB (pending → ready, or failed; the retrieve task sets `retrieved`)

**Environment variables:** `ENV_NAME`, `SECRETS_ID`, `PRIVATE_SUBNET_IDS`, `SECURITY_GROUP_ID`, `CACHE_TABLE`, `ECS_CLUSTER`, `NOTIFICATION_TOPIC_ARN`, `NAT_WAIT_SECONDS`

---

### nat-manager (`dev-wwii-nat-manager`)

**Purpose:** Dynamic networking lifecycle — creates/deletes NAT gateway and VPC endpoints on demand.

**Invocation sources:**
- Lambda invoke from trigger/batch-poller/openserp-worker-autoscaler and the pipeline tasks in `ecs_entrypoint.py` (action: "create")
- Lambda invoke from nat-teardown / idle-backstop-reaper / openserp-manager (action: "delete")
- SNS subscription to `phase2-complete`: still subscribed but a no-op (teardown is owned by nat-teardown)

**What it does:**
- `{"action": "create"}` — Creates NAT gateway, allocates EIP, updates route tables, ensures VPC endpoints (ECR, logs, S3, Secrets Manager)
- `{"action": "delete"}` — Deletes NAT, releases EIP, removes routes, deletes non-essential VPC endpoints
- `{"action": "verify"}` — Returns `{ready, missing}` (NAT and every required interface endpoint available)
- `{"action": "status"}` — Returns current NAT state
- On SNS: no-op. Phases never tear down NAT; `nat-teardown` (every 4 h) deletes only when the DynamoDB queue is empty and no container is processing

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
- Reads `dedup/review_status.json` from S3 and returns `blocked` while review is incomplete
- On a `dedup_complete` SNS record from the trusted `dedup-complete` topic, invokes Phase 3 for all entity files (`PHASE3_FUNCTION_NAME`)
- Not called by `trigger` and has no CloudFormation event source. `dedup-complete` is subscribed by `trigger` (`events.yaml` `DedupCompleteSubscription`), and Phase 3 is gated in `ecs_entrypoint._dedup_gate_decision`

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

**Purpose:** API Gateway custom authorizer for the dedup/mdreview/urlreview API and the metrics API.

**What it does:**
- Validates Basic auth credentials from `Authorization` header
- Credentials are resolved from Secrets Manager (`{env}-wwii-pipeline/dedup-auth`) into the environment variable `AUTH_TOKEN` (format: `user:pass`)
- Returns IAM policy allowing/denying API access

---

### s3-notif-setup (`dev-wwii-s3-notif-setup`)

**Purpose:** CloudFormation custom resource that configures S3 bucket event notifications.

**What it does:**
- Called by CloudFormation on stack create/update/delete
- Configures two S3 → SNS notifications:
  - prefix `contentrepository/` (no suffix filter: raw PDFs, EPUBs, images and markdown all count) → `content-uploaded` topic
  - prefix `output/content/`, suffix `-parsed.json` → `chapter-parsed` topic
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
| `trigger` via EventBridge | Stale lock reconciliation | Scheduled (configurable, default 15 min) |
| `trigger` via `aws lambda invoke` | Manual pipeline trigger | Operator |
| `batch-poller` via EventBridge | Poll Grok batch status | Scheduled (5 min) |
| `nat-manager` via Lambda invoke | Create/delete NAT | create: trigger, batch-poller, openserp-worker-autoscaler, pipeline tasks (`ecs_entrypoint.py`); delete: nat-teardown, idle-backstop-reaper, openserp-manager |
| `nat-teardown` via EventBridge | NAT/endpoint teardown decision | Scheduled (4 h) |
| `openserp-manager` via EventBridge | Idle cost monitoring | Scheduled (10 min) |
| `dedup-gate` via Lambda invoke | Check review status | not wired: nothing invokes it (manual invoke only) |
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
| `dev-wwii-stale-lock-check` | configurable (default 15 min) | trigger (with `{"source": "scheduled"}`) |
| `dev-wwii-batch-poller-schedule` | configurable (default 5 min) | batch-poller |
| `dev-wwii-openserp-idle-monitor` (`events.yaml`) | 10 min | openserp-manager |
| nat-teardown (4 h), idle-backstop-reaper (`rate(1 hour)`), ocr-spot-controller (`rate(1 hour)`) | see Function Overview | `cloudformation/compute.yaml`, `cloudformation/ocr.yaml` |

---

## SNS Topics

| Topic | Publishers | Subscribers |
|-------|-----------|-------------|
| `dev-wwii-content-uploaded` | S3 (`contentrepository/` prefix) | SQS → trigger |
| `dev-wwii-chapter-parsed` | S3 (`output/content/*-parsed.json`) | SQS → trigger |
| `dev-wwii-entity-created` | S3 (output/entity files) | SQS → trigger |
| `dev-wwii-phase2-complete` | ECS tasks, Lambdas (all operator alerts) | email, slack-formatter (→ `dev-wwii-slack` → Chatbot), nat-manager (no-op) |
| `dev-wwii-dedup-complete` | dedup-ui | trigger |
| `dev-wwii-alarms` | CloudWatch Alarms | email |

---

## Cost Notes

- **Batch poller:** Runs every 5 min regardless of activity (~$0.60/month). Cold starts each time.
- **OpenSERP manager:** Runs every 10 min (~$0.20/month). Most invocations are no-ops.
- **Trigger:** Only runs on S3 events + the reconciliation schedule. Minimal cost.
- **NAT gateway:** $0.045/hr when active. The 2-hour guardrail caps worst-case to ~$0.09 per orphan.

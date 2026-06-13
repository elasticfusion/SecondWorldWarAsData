# AWS Deployment Guide

Deploy the WWII Data Extraction Pipeline on AWS using ECS Fargate, S3, and DynamoDB.

**Last Updated:** 2026-05-23

---

## Architecture

```
S3 (content upload) → SNS → SQS (60s batch) → Trigger Lambda → queues pending#content in DynamoDB
                                                                  ↓ launches ECS Phase 1 if pipeline idle
                                                                  ↓ writes parsed JSON to S3 (output/content/{Book}/)
S3 (parsed JSON)    → SNS → SQS (60s batch) → Trigger Lambda → queues pending#parsed in DynamoDB
                                                                  ↓ launches ECS Phase 2 if pipeline idle
Phase 2 (batch:true) → auto-delegates to submit-only mode
                                                                  ↓ submits batch to Grok API
                                                                  ↓ enqueues batch_job#{batch_id} in DynamoDB
                                                                  ↓ tears down NAT + scales OpenSERP to 0
                                                                  ↓ exits
EventBridge (5 min) → batch_poller Lambda → polls Grok batch API
                                                                  ↓ on completion: creates networking
                                                                  ↓ launches ECS retrieve-only task
Retrieve task        → downloads batch results → populates cache
                                                                  ↓ re-runs phase with SKIP_RETRY
                                                                  ↓ downloads full S3 entity inventory + event files
                                                                  ↓ runs cross-book dedup analysis
S3 (dedup reports)  → SNS → SQS (60s batch) → Trigger Lambda → checks dedup/review_status.json
                                                                  ↓ blocks Phase 3 until review complete
                     Dedup Review UI (API Gateway + Lambda)
                                                                  ↓ user merges/skips/reclassifies
                                                                  ↓ clicks "Mark Complete"
SNS (dedup-complete) → Trigger Lambda → ECS Phase 3 (enrich --batch, submit-only)
                                                                  ↓ same submit → poller → retrieve flow
DynamoDB (import)   ← ECS Import (manual trigger)
```

- **Pipeline compute:** ECS Fargate tasks (Phase 1/2/3, import) — no timeout limits, batch mode for Grok API
- **Capacity providers:** Fargate Spot (weight 4) + Fargate (weight 1) — ~70% cost savings with automatic Spot termination recovery via SIGTERM handler and EventBridge task state change rule
- **ECS entrypoint modes:** `run_phase` (default), `--submit-only` (submit batch then exit), `--retrieve-only` (retrieve results and re-run)
- **Trigger:** Single lightweight Lambda receives SQS-batched events, queues content in DynamoDB (`pending#content`, `pending#parsed`), and launches ECS tasks only if pipeline is idle
- **Batch Poller:** Lambda (`dev-wwii-batch-poller`) triggered by EventBridge every 5 min — polls Grok batch API, launches ECS retrieve task on completion
- **Job Queue:** DynamoDB entries (`batch_job#{batch_id}`) track submitted batch jobs with status (pending/complete/failed/retrieved)
- **OpenSERP:** ECS Fargate service with internal ALB (headless Chrome)
- **Storage:** S3 for content and output, DynamoDB for API cache, pipeline locks, manifests, job queue, and entity tables
- **Events:** S3 notifications → SNS → SQS (60s batch window) → Trigger Lambda → ECS RunTask
- **Dedup UI:** API Gateway with Basic Auth → Lambda serving HTML review interface (merge, skip, reclassify). UI actions append changed file keys to DynamoDB manifest for Phase 3.
- **Incremental processing:** Phase 2 only downloads new parsed files (no matching event file). Phase 3 reads DynamoDB manifest to download only changed files. S3 downloads scoped by `BOOK_NAME` env var for Phase 3.
- **Pending content queue:** If pipeline is busy when new content arrives, trigger Lambda queues it in DynamoDB (`pending#content`) and sends email notification. Phase 2 re-triggers after completion.
- **Preflight credit check:** Pipeline verifies Grok API credit balance before starting extraction.
- **Metrics:** API Gateway → Lambda serving pipeline metrics from DynamoDB
- **Monitoring:** CloudWatch alarms, dashboard, ECS task logs, per-chapter heartbeat progress
- **Prompts:** YAML templates in `prompts/`, overridable from S3 without container rebuild
- **Cost control:** Submit-only task tears down NAT Gateway + scales OpenSERP to 0 after batch submission. Poller re-creates networking before launching retrieve task.

See [AWS Architecture Plan](AWS_DEPLOYMENT_PLAN.md) for detailed design decisions.

---

## Prerequisites

- **AWS account** with admin access
- **AWS CLI v2** configured (`aws configure`)
- **Python 3.12+** with boto3
- **Docker** (for building container images)
- **cfn-lint** (`pipx install cfn-lint`)

---

## Quick Start

### Quick Deploy (All-in-One)

For subsequent deployments after initial setup, use the all-in-one script:

```bash
bash scripts/deploy_all.sh
```

This stops running tasks, clears locks, runs QA, builds and pushes the container, deploys CloudFormation, updates Lambdas, and fixes auth.

### Monitoring and Log Analysis

```bash
# Live monitoring (polling-based, color-coded)
bash scripts/monitor_logs.sh

# Or direct CloudWatch tail
aws logs tail /ecs/dev-wwii-pipeline --follow --region us-east-1 --since 2m

# Analyze last 24h of logs
bash scripts/analyze_logs.sh

# JSON extraction quality report
python3 scripts/json_quality_report.py
```

---

### 1. Store Secrets in Secrets Manager

```bash
# Grok API key
aws secretsmanager create-secret \
  --name dev-wwii-pipeline/grok-api-key \
  --secret-string "your-grok-api-key" \
  --region us-east-1

# Dedup review UI password (username:password format)
aws secretsmanager create-secret \
  --name dev-wwii-pipeline/dedup-auth \
  --secret-string "admin:your-review-password" \
  --region us-east-1
```

### 2. Validate Templates

```bash
python3 scripts/deploy_aws.py validate
```

### 3. Build and Push Container Images

#### Pipeline Image

```bash
# Create ECR repository
aws ecr create-repository --repository-name wwii-pipeline --region us-east-1

# Login to ECR
aws ecr get-login-password --region us-east-1 | docker login --username AWS --password-stdin <account-id>.dkr.ecr.us-east-1.amazonaws.com

# Build and push
docker build -t wwii-pipeline .
docker tag wwii-pipeline:latest <account-id>.dkr.ecr.us-east-1.amazonaws.com/wwii-pipeline:latest
docker push <account-id>.dkr.ecr.us-east-1.amazonaws.com/wwii-pipeline:latest
```

#### OpenSERP Image

```bash
# Clone OpenSERP (if not already done)
git clone https://github.com/karust/openserp.git openserp

# Create ECR repository
aws ecr create-repository --repository-name wwii-openserp --region us-east-1

# Build and push
cd openserp
docker build -t wwii-openserp .
docker tag wwii-openserp:latest <account-id>.dkr.ecr.us-east-1.amazonaws.com/wwii-openserp:latest
docker push <account-id>.dkr.ecr.us-east-1.amazonaws.com/wwii-openserp:latest
cd ..
```

> **Note:** OpenSERP defaults to port 7000. The pipeline expects port 7001 (configured in
> `config.yaml` under `openserp_url`). Set the `OPENSERP_SERVER_PORT=7001` environment variable
> in the ECS task definition, or update `config.yaml` to use port 7000.

### 4. Upload Templates and Lambda Code

```bash
# Create deployment bucket
aws s3 mb s3://wwii-pipeline-deploy --region us-east-1

# Upload CloudFormation templates
aws s3 sync cloudformation/ s3://wwii-pipeline-deploy/cloudformation/

# Package and upload Lambda code (dedup UI, auth, openserp manager only)
bash scripts/update_lambdas.sh dev us-east-1 wwii-pipeline-deploy
```

### 5. Deploy Stack

```bash
python3 scripts/deploy_aws.py deploy \
  --env dev \
  --region us-east-1 \
  --template-bucket wwii-pipeline-deploy \
  --pipeline-image <account-id>.dkr.ecr.us-east-1.amazonaws.com/wwii-pipeline:latest \
  --openserp-image <account-id>.dkr.ecr.us-east-1.amazonaws.com/wwii-openserp:latest
```

The deploy script reads `notification_email` from `config.yaml` automatically. You can also pass `--notification-email you@example.com` to override. You'll receive a confirmation email from SNS that you must click to activate notifications.

### 6. Upload Content

```bash
# Upload source documents to trigger the pipeline
aws s3 sync contentrepository/ s3://dev-wwii-data-pipeline/content/
```

The pipeline runs automatically: S3 upload → Phase 1 → Phase 2 (batch) → **dedup review gate** → Phase 3 (batch).

Phase 3 is blocked until you review duplicates in the Dedup Review UI (see below).

### What's Next?

The pipeline phases, entity types, and output format are the same for local and AWS. See:

- **[Adding Data Sources](pipeline/ADDING_DATA_SOURCES.md)** — How to prepare a book for the pipeline: directory structure, metadata YAML, markdown format, and content requirements
- **[HyperWar HTML Import](pipeline/HYPERWAR_HTML_IMPORT.md)** — Import books from HyperWar web pages
- **[PDF Conversion](pipeline/PDF_CONVERSION.md)** — Convert PDF source documents to markdown
- **[Pipeline Overview](core/PIPELINE.md)** — Phase 1/2/3 workflow, what each phase does, expected inputs and outputs
- **[Schema Reference](SCHEMA_REFERENCE.md)** — JSON output format for all 11 entity types, cross-referencing conventions
- **[Feature Index](features/README.md)** — Detailed docs for each entity type (events, people, places, etc.)
- **[Configuration](core/CONFIGURATION.md)** — All `config.yaml` options (shared between local and AWS)

For AWS-specific operations after deployment, continue with the sections below (Configuration, Dedup Review UI, Monitoring, Import to DynamoDB).

---

## Configuration

Set `aws.enabled: true` in `config.yaml`:

```yaml
aws:
  enabled: true
  region: "us-east-1"
  s3_bucket: "dev-wwii-data-pipeline"
  cache_table: "dev-wwii-api-cache"
  secrets_id: "dev-wwii-pipeline/grok-api-key"
  notification_email: "you@example.com"    # Pipeline completion notifications
  openserp:
    cluster: "dev-wwii-pipeline"
    service: "dev-wwii-openserp"
    health_check_url: "/health"
    startup_timeout: 120
  database:
    backend: "dynamodb"
    dynamodb_table_prefix: "dev-wwii-"
```

---

## Infrastructure

### CloudFormation Templates

| Template | Resources |
|----------|-----------|
| `network.yaml` | VPC, subnets, NAT Gateway, security groups, VPC endpoints |
| `storage.yaml` | S3 bucket (DeletionPolicy: Retain, PublicAccessBlock enabled), 11 DynamoDB tables, AWS Budget |
| `iam.yaml` | Lambda and ECS IAM roles |
| `compute.yaml` | ECS cluster, 4 pipeline task definitions, trigger Lambda, batch poller Lambda, OpenSERP service, ALB, dedup UI/gate/auth Lambdas |
| `events.yaml` | SNS topics, S3→SNS notifications, EventBridge schedules (5-min poller, 15-min reconciliation), CloudWatch alarms + dashboard |
| `main.yaml` | Root stack (nests all above) |

**Key settings:**
- **SQS VisibilityTimeout:** 300s (allows trigger Lambda to process large batches of S3 events)
- **S3 DeletionPolicy:** Retain (bucket preserved on stack deletion to prevent data loss)
- **S3 PublicAccessBlock:** All four block settings enabled
- **Failure notifications:** SNS messages sent on phase errors (ECS task failures, batch job failures)
- **Batch job TTL:** 30 days (DynamoDB auto-cleanup of completed job entries)

### Updating Pipeline Code

After code changes, rebuild and push the pipeline container:

```bash
docker build -t wwii-pipeline .
docker tag wwii-pipeline:latest <account-id>.dkr.ecr.us-east-1.amazonaws.com/wwii-pipeline:latest
docker push <account-id>.dkr.ecr.us-east-1.amazonaws.com/wwii-pipeline:latest
```

ECS tasks pull the `:latest` tag on each run, so new tasks automatically use the updated image.

For Lambda changes (dedup UI, openserp manager, batch poller):

```bash
bash scripts/update_lambdas.sh dev us-east-1 wwii-pipeline-deploy
```

### Batch Poller Lambda

**Name:** `dev-wwii-batch-poller`  
**Source:** `lambda_handlers/batch_poller.py`  
**Triggered by:** EventBridge schedule (`rate(5 minutes)`, configurable via `BatchPollerIntervalMinutes`) OR direct invoke with `{action: "submit"}`

Polls the Grok batch API for pending jobs and launches ECS retrieve tasks on completion.

**Flow:**
1. Scans DynamoDB for `batch_job#*` entries with status `pending`
2. Checks Grok batch API for each job's completion status
3. On completion: updates job status, creates NAT Gateway networking, launches ECS retrieve-only task
4. On failure: updates job status to `failed`, sends SNS notification
5. **24h timeout:** Jobs pending for >24 hours are marked `failed` (stuck batch protection)

**Environment Variables:**
| Variable | Description |
|----------|-------------|
| `CACHE_TABLE` | DynamoDB table for job queue entries |
| `ECS_CLUSTER` | ECS cluster name |
| `PRIVATE_SUBNET_IDS` | Comma-separated subnet IDs for ECS tasks |
| `SECURITY_GROUP_ID` | Security group for ECS tasks |
| `SECRETS_ID` | Secrets Manager secret for Grok API key |
| `NOTIFICATION_TOPIC_ARN` | SNS topic for notifications |

**IAM Permissions:**
- DynamoDB: Scan, GetItem, PutItem, UpdateItem, DeleteItem
- ECS: RunTask
- Lambda: InvokeFunction (NAT manager)
- Secrets Manager: GetSecretValue
- SNS: Publish

### Management

```bash
# Check status
python3 scripts/deploy_aws.py status --env dev

# Destroy everything
python3 scripts/deploy_aws.py destroy --env dev
```

---

## Dedup Review UI

After Phase 2 completes, Phase 3 is blocked until you review and merge duplicates. The review UI is a password-protected web page served by API Gateway + Lambda.

### Access

The URL is in the stack outputs:
```bash
python3 scripts/deploy_aws.py status --env dev
# Look for: DedupReviewUrl
```

Open the URL in a browser. You'll be prompted for Basic Auth credentials (the `dedup-auth` secret you created in step 1).

### Workflow

1. **Review** — tabs for People, Places, and Groups. Each duplicate group shows confidence score, match reasons, and the records involved. Click **▶ Details** to view entity JSON including event mentions.
2. **Merge Selected** — check entries to include, select the primary (radio button), click Merge. Unchecked entries with 2+ members are re-grouped for separate review.
3. **Skip** — remove the group from review without merging.
4. **Not Duplicates** — add to exclusion list so they won't appear in future reports.
5. **Reclassify** — use the **↗ Move to...** dropdown to move a misclassified entity between categories (e.g., military unit from "places" to "groups"). The schema is automatically transformed.
6. **Mark Complete** — click "Mark Review Complete & Start Phase 3" when done. This unblocks Phase 3, which then processes all entity files.

### How the Gate Works

```
Phase 2 writes entity files → S3 event → SNS → Trigger Lambda
  → Checks dedup/review_status.json
  → If complete: false → event dropped (Phase 3 blocked)

User clicks "Mark Complete" in UI → SNS (dedup-complete) → Trigger Lambda
  → Launches ECS Phase 3 task for all entities
```

### Changing the Password

```bash
aws secretsmanager update-secret \
  --secret-id dev-wwii-pipeline/dedup-auth \
  --secret-string "admin:new-password"
```

---

## Cost Management

### Idle Cost: ~$0/month

The submit-only ECS task tears down NAT Gateway and scales OpenSERP to 0 immediately after batch submission. The batch poller Lambda re-creates networking only when a batch job completes and a retrieve task is needed. This means:
- NAT Gateway (~$32/month) only exists during active pipeline runs
- OpenSERP only runs during Phase 3 enrichment
- No idle monitor needed — infrastructure is torn down deterministically

VPC, subnets, S3, DynamoDB, and Lambda functions cost $0 when idle. Pipeline ECS tasks are one-shot — they stop when done and cost nothing between runs.

### Active Cost: ~$50-75/month

| Service | Estimated Cost |
|---------|---------------|
| ECS Fargate (pipeline tasks) | ~$2-5 |
| ECS Fargate (OpenSERP) | ~$5-10 |
| S3 (10GB) | ~$1 |
| DynamoDB (on-demand) | ~$2 |
| NAT Gateway | ~$35 |
| ALB | ~$16 |
| Lambda (trigger + dedup UI) | ~$0.10 |
| Budget alert at $75 | — |

### S3 Lifecycle

| Prefix | Rule |
|--------|------|
| `tmp/` | Delete after 7 days |
| `cache/` | Delete after 90 days |
| `output/` | → Standard-IA at 30 days → Glacier IR at 90 days |
| All (noncurrent versions) | Expire after 30 days |

### S3 Directory Structure

```
s3://dev-wwii-data-pipeline/
├── content/                        # Source documents (markdown)
│   └── {Book}/chapter*/
├── output/
│   ├── content/                    # Book-specific output (parsed/event files)
│   │   └── {Book}/chapter*-parsed.json, chapter*-event.json
│   ├── people/                     # Entity directories
│   ├── places/
│   ├── people_groups/
│   ├── equipment/
│   ├── casualties/
│   ├── dates/
│   ├── weather/
│   ├── bibliography/
│   └── dedup/review_status.json
├── prompts/                        # S3-overridable prompt templates
└── cache/                          # API response cache (DynamoDB primary)
```

S3 notifications for `-parsed.json` and `-event.json` files are scoped to the `output/content/` prefix. Entity uploads to `output/people/` etc. do not trigger pipeline re-runs.

---

## Monitoring

### CloudWatch Logs

Pipeline ECS tasks log to `/ecs/dev-wwii-pipeline`:

```bash
# Tail pipeline logs
aws logs tail /ecs/dev-wwii-pipeline --follow --region us-east-1

# Filter by phase
aws logs tail /ecs/dev-wwii-pipeline --follow --region us-east-1 --filter-pattern "phase2"

# Check incremental processing
aws logs tail /ecs/dev-wwii-pipeline --region us-east-1 --since 10m --filter-pattern "incremental"

# Check trigger Lambda
aws logs tail /aws/lambda/dev-wwii-trigger --region us-east-1 --since 5m | tail -10

# Verify no feedback loop (should be empty after Phase 2)
aws logs tail /aws/lambda/dev-wwii-trigger --region us-east-1 --since 5m --filter-pattern "chapter-parsed"
```

### Pipeline State Checks

```bash
# Check dedup review status
aws s3 cp s3://dev-wwii-data-pipeline/dedup/review_status.json - --region us-east-1

# Check for pending content
aws dynamodb get-item --table-name dev-wwii-api-cache --key '{"cache_key":{"S":"pending#content"}}' --region us-east-1

# Check for pending parsed files
aws dynamodb get-item --table-name dev-wwii-api-cache --key '{"cache_key":{"S":"pending#parsed"}}' --region us-east-1

# Check batch job status (pending jobs waiting for Grok API)
aws dynamodb scan --table-name dev-wwii-api-cache --filter-expression "begins_with(cache_key, :prefix)" --expression-attribute-values '{":prefix":{"S":"batch_job#"}}' --region us-east-1

# Check Phase 2 manifest
aws dynamodb get-item --table-name dev-wwii-api-cache --key '{"cache_key":{"S":"manifest#phase2"}}' --region us-east-1 --query "Item.keys.L | length(@)"

# Clear stale locks (emergency)
aws s3 rm s3://dev-wwii-data-pipeline/locks/ --recursive --region us-east-1
```

### CloudWatch Dashboard

`dev-wwii-pipeline` dashboard shows Lambda invocations, errors, and duration.

### Alarms (→ SNS topic `dev-wwii-alarms`)

- Lambda errors (≥3 in 5 min) for trigger, batch-poller, nat-manager
- ECS task failures (phase errors trigger SNS notification)
- Batch job failures (poller sends SNS on Grok API errors)

See [MONITORING.md](MONITORING.md) for full alarm details and management commands.

### Email Notifications

- **Phase 1 completion:** Includes list of parsed files produced
- **Content queued:** Sent when pipeline is busy and content is queued for later
- **Phase errors:** Sent on ECS task failure or batch job failure
- **Dedup review ready:** Sent when Phase 2 completes and review is needed

### Checking ECS Task Status

```bash
# List running tasks
aws ecs list-tasks --cluster dev-wwii-pipeline --region us-east-1

# Describe a task
aws ecs describe-tasks --cluster dev-wwii-pipeline \
  --tasks <task-id> --region us-east-1
```

---

## Import to DynamoDB

```bash
# Run as ECS task
aws ecs run-task \
  --cluster dev-wwii-pipeline \
  --task-definition dev-wwii-import \
  --launch-type FARGATE \
  --network-configuration "awsvpcConfiguration={subnets=[<subnet-id>],securityGroups=[<sg-id>],assignPublicIp=DISABLED}" \
  --region us-east-1

# Or from local machine
python3 import_to_dynamodb.py --region us-east-1 --prefix dev-wwii-
```

---

## Switching Between Local and AWS

The same codebase supports both modes:

```yaml
# Local mode (default)
aws:
  enabled: false

# AWS mode
aws:
  enabled: true
  s3_bucket: "dev-wwii-data-pipeline"
  # ...
```

When `aws.enabled` is false:
- Storage → local filesystem
- Cache → diskcache (SQLite)
- OpenSERP → localhost:7001
- All CLI commands work unchanged

When `aws.enabled` is true:
- Storage → S3
- Cache → DynamoDB
- OpenSERP → ECS Fargate (auto-started)
- Pipeline triggered by S3 events → ECS tasks

---

## Troubleshooting

### ECS task not starting
```bash
# Check recent task failures
aws ecs list-tasks --cluster dev-wwii-pipeline --desired-status STOPPED --region us-east-1

# Describe the stopped task for error details
aws ecs describe-tasks --cluster dev-wwii-pipeline --tasks <task-id> --region us-east-1
```

### OpenSERP not starting
```bash
# Check ECS service
aws ecs describe-services --cluster dev-wwii-pipeline --services dev-wwii-openserp --region us-east-1

# Check task logs
aws logs tail /ecs/dev-wwii-openserp --follow --region us-east-1
```

### Pipeline not triggering
```bash
# Verify S3 notifications are configured
aws s3api get-bucket-notification-configuration --bucket dev-wwii-data-pipeline --region us-east-1

# Check trigger Lambda logs
aws logs tail /aws/lambda/dev-wwii-trigger --follow --region us-east-1
```

### NAT Gateway not re-created
The openserp-manager Lambda re-creates it on demand. Check its CloudWatch logs if outbound internet isn't working.

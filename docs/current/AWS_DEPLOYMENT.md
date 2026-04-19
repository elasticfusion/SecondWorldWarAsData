# AWS Deployment Guide

Deploy the WWII Data Extraction Pipeline on AWS using Lambda, ECS, S3, and DynamoDB.

**Last Updated:** 2026-04-19

---

## Architecture

```
S3 (content upload) → SNS → Lambda Phase 1 (parse)
                                ↓ writes parsed JSON to S3
S3 (parsed JSON)    → SNS → Lambda Phase 2 (extract)
                                ↓ calls Grok API + OpenSERP (ECS)
S3 (entity files)   → SNS → Lambda Phase 3 (enrich)
                                ↓ calls Grok/Wikipedia/Grokipedia
DynamoDB (import)   ← Lambda Import (manual trigger)
```

- **Compute:** Lambda functions (Phase 1/2/3, import, OpenSERP manager)
- **OpenSERP:** ECS Fargate with internal ALB (headless Chrome, can't run on Lambda)
- **Storage:** S3 for content and output, DynamoDB for API cache and entity tables
- **Events:** S3 notifications → SNS → Lambda
- **Monitoring:** CloudWatch alarms, dashboard, DLQs, idle monitor
- **Cost control:** NAT Gateway + ALB + ECS auto-teardown after 30 min idle

See [AWS Architecture Plan](AWS_DEPLOYMENT_PLAN.md) for detailed design decisions.

---

## Prerequisites

- **AWS account** with admin access
- **AWS CLI** configured (`aws configure`)
- **Python 3.12+** with boto3
- **Docker** (for building OpenSERP container image)
- **cfn-lint** (`pip install cfn-lint`)

---

## Quick Start

### 1. Store API Key in Secrets Manager

```bash
aws secretsmanager create-secret \
  --name dev-wwii-pipeline/grok-api-key \
  --secret-string "your-grok-api-key" \
  --region us-east-1
```

### 2. Validate Templates

```bash
python3 scripts/deploy_aws.py validate
```

### 3. Build and Push OpenSERP Image

```bash
# Create ECR repository
aws ecr create-repository --repository-name wwii-openserp --region us-east-1

# Login to ECR
aws ecr get-login-password --region us-east-1 | docker login --username AWS --password-stdin <account-id>.dkr.ecr.us-east-1.amazonaws.com

# Build and push
cd openserp
docker build -t wwii-openserp .
docker tag wwii-openserp:latest <account-id>.dkr.ecr.us-east-1.amazonaws.com/wwii-openserp:latest
docker push <account-id>.dkr.ecr.us-east-1.amazonaws.com/wwii-openserp:latest
cd ..
```

### 4. Upload Templates and Lambda Code

```bash
# Create deployment bucket
aws s3 mb s3://wwii-pipeline-deploy --region us-east-1

# Upload CloudFormation templates
aws s3 sync cloudformation/ s3://wwii-pipeline-deploy/cloudformation/

# Package and upload Lambda code
zip -r lambda-code.zip src/ lambda_handlers/ scripts/ config.yaml -x "*.pyc" "*__pycache__*"
aws s3 cp lambda-code.zip s3://wwii-pipeline-deploy/lambda/code.zip
```

### 5. Deploy Stack

```bash
python3 scripts/deploy_aws.py deploy \
  --env dev \
  --region us-east-1 \
  --template-bucket wwii-pipeline-deploy \
  --openserp-image <account-id>.dkr.ecr.us-east-1.amazonaws.com/wwii-openserp:latest
```

### 6. Upload Content

```bash
# Upload source documents to trigger the pipeline
aws s3 sync contentrepository/ s3://dev-wwii-data-pipeline/content/
```

The pipeline runs automatically: S3 upload → SNS → Phase 1 → Phase 2 → Phase 3.

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
| `storage.yaml` | S3 bucket, 11 DynamoDB tables, AWS Budget |
| `iam.yaml` | Lambda and ECS IAM roles |
| `compute.yaml` | 5 Lambda functions, ECS cluster, Fargate service, ALB, DLQs |
| `events.yaml` | SNS topics, S3→SNS notifications, EventBridge idle monitor, CloudWatch alarms + dashboard |
| `main.yaml` | Root stack (nests all above) |

### Management

```bash
# Check status
python3 scripts/deploy_aws.py status --env dev

# Update after code changes
zip -r lambda-code.zip src/ lambda_handlers/ scripts/ config.yaml -x "*.pyc" "*__pycache__*"
aws s3 cp lambda-code.zip s3://wwii-pipeline-deploy/lambda/code.zip
aws lambda update-function-code --function-name dev-wwii-phase2-extract \
  --s3-bucket wwii-pipeline-deploy --s3-key lambda/code.zip

# Destroy everything
python3 scripts/deploy_aws.py destroy --env dev
```

---

## Cost Management

### Idle Cost: ~$0/month

The idle monitor Lambda (runs every 10 min) checks ALB request count. After 30 minutes of no activity, it tears down:
- ECS Fargate task (OpenSERP)
- NAT Gateway + Elastic IP (~$32/month)
- Internal ALB (~$16/month)

VPC, subnets, S3, DynamoDB, and Lambda functions cost $0 when idle.

### Active Cost: ~$50-75/month

| Service | Estimated Cost |
|---------|---------------|
| Lambda (pipeline) | ~$1-2 |
| ECS Fargate (OpenSERP) | ~$5-10 |
| S3 (10GB) | ~$1 |
| DynamoDB (on-demand) | ~$2 |
| NAT Gateway | ~$35 |
| ALB | ~$16 |
| Budget alert at $75 | — |

### S3 Lifecycle

| Prefix | Rule |
|--------|------|
| `tmp/` | Delete after 7 days |
| `cache/` | Delete after 90 days |
| `output/` | → Standard-IA at 30 days → Glacier IR at 90 days |

---

## Monitoring

### CloudWatch Dashboard

`dev-wwii-pipeline` dashboard shows:
- Lambda invocations, errors, duration
- DLQ message depth

### Alarms (→ SNS topic `dev-wwii-alarms`)

- Phase 1/2/3 Lambda errors (≥1 in 5 min)
- Phase 2 duration approaching limit (>14 min)
- Any Lambda throttles
- Any DLQ messages

### Dead Letter Queues

Failed Lambda invocations go to SQS DLQs (14-day retention):
- `dev-wwii-phase1-dlq`
- `dev-wwii-phase2-dlq`
- `dev-wwii-phase3-dlq`
- `dev-wwii-import-dlq`

---

## Import to DynamoDB

```bash
# From Lambda (manual trigger)
aws lambda invoke --function-name dev-wwii-import /dev/stdout

# From local machine (reads output/ directory)
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
- Pipeline triggered by S3 events

---

## Troubleshooting

### Lambda timeout
Phase 2 has a 15-min limit. If chapters are too large, they'll timeout. Check CloudWatch logs and consider splitting large chapters with `scripts/split_chapters.py`.

### OpenSERP not starting
```bash
# Check ECS service
aws ecs describe-services --cluster dev-wwii-pipeline --services dev-wwii-openserp

# Check task logs
aws logs tail /ecs/dev-wwii-openserp --follow
```

### DLQ messages
```bash
# Check DLQ depth
aws sqs get-queue-attributes \
  --queue-url https://sqs.us-east-1.amazonaws.com/<account>/dev-wwii-phase2-dlq \
  --attribute-names ApproximateNumberOfMessages
```

### NAT Gateway not re-created
The openserp-manager Lambda re-creates it on demand. Check its CloudWatch logs if outbound internet isn't working.

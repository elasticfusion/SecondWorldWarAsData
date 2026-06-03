# DevOps Recommendations

**Date:** 2026-05-24  
**Scope:** Infrastructure, CI/CD, containers, security, operational tooling  
**Status:** Review of current state with prioritized recommendations

---

## Executive Summary

The project has a solid foundation: well-structured CloudFormation nested stacks, GitHub Actions CI/CD with OIDC, Trivy scanning, comprehensive linting, and cost-conscious architecture (PAY_PER_REQUEST DynamoDB, dynamic NAT lifecycle, S3 lifecycle rules). The main risks are **hardcoded secrets**, **overly broad IAM permissions**, and **operational fragility** in the ECS entrypoint.

---

## Critical Issues (Fix Immediately)

### 1. Hardcoded Secrets in Source Control

**Status:** ✅ Partially resolved — `config.yaml` removed from tracking, `.gitignore` entry active, `config.yaml.example` updated with empty key placeholders.

**Remaining issues:** `scripts/deploy_all.sh`

| Secret | Location | Status |
|--------|----------|--------|
| NARA API key | `config.yaml` | ✅ File removed from git tracking |
| NOAA API token | `config.yaml` | ✅ File removed from git tracking |
| Auth credentials `admin:ReviewPass2026` | `scripts/deploy_all.sh` step 7 | ❌ Still hardcoded |
| AWS Account ID `340339225515` | `scripts/deploy_all.sh` | ❌ Still hardcoded |

**Remaining work:**
- Replace hardcoded auth token in deploy script with `aws secretsmanager get-secret-value` call
- Use `aws sts get-caller-identity --query Account` instead of hardcoded account ID
- Rotate NARA/NOAA keys (they remain in git history)
- Consider `git filter-repo` or BFG Repo Cleaner to purge secrets from history

### 2. Overly Broad IAM Permissions

**File:** `cloudformation/iam.yaml`

```yaml
# These use Resource: '*' — too permissive
- Sid: ECSManagement → Resource: '*'
- Sid: EC2ForNatGateway → Resource: '*'
- Sid: CloudWatch → Resource: '*'
- Sid: NestedStackResources → Resource: '*' (deploy role)
```

**Recommendation:**
- Scope ECS actions to the specific cluster ARN: `arn:aws:ecs:*:*:cluster/${EnvironmentName}-wwii-*`
- Scope EC2 NAT/VPC actions with condition keys (`ec2:Vpc`, `ec2:ResourceTag/ManagedBy`)
- The deploy role's `NestedStackResources` statement is especially dangerous — it grants `s3:CreateBucket`, `s3:DeleteBucket`, `dynamodb:DeleteTable`, etc. on `*`. Add resource constraints or at minimum tag-based conditions.

---

## High Priority (This Sprint)

### 3. Container Security Hardening

**File:** `Dockerfile`

Current state: runs as root, no health check, no pinned base image digest.

```dockerfile
# Recommended additions:
FROM python:3.12-slim@sha256:<pin-digest> AS builder
# ...
FROM python:3.12-slim@sha256:<pin-digest>

# Add non-root user
RUN useradd -r -s /bin/false pipeline
USER pipeline

# Add health check for ECS
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD python3 -c "import sys; sys.exit(0)"
```

Also:
- Pin `requirements.txt` versions (currently uses `>=` ranges for pydantic, requests, Pillow, imagehash)
- The container copies `config.yaml` with plaintext API keys into the image — the entrypoint should fetch config at runtime instead

### 4. ECS Task Timeout and Error Handling

**File:** `ecs_entrypoint.py`

Issues:
- No ECS task-level timeout — a stuck task runs indefinitely (and costs money)
- `BackgroundSync._sync()` catches all exceptions with a warning log but no alerting
- No structured error handling — subprocess exit codes are the only signal
- `_get_openserp_alb_dns()` polls for 12 minutes with no circuit breaker

**Recommendation:**
- Add `stopTimeout` to ECS task definitions (e.g., 4 hours for Phase 2, 1 hour for Phase 1/3)
- Add CloudWatch metric filter on "Background sync error" log pattern → alarm
- Implement a watchdog: if no S3 upload in N minutes, self-terminate with notification
- Add structured JSON logging for easier CloudWatch Insights queries

### 5. Trigger Lambda Complexity

**File:** `cloudformation/compute.yaml` (inline ZipFile, ~200 lines)

The trigger Lambda is defined as inline `ZipFile` in CloudFormation. This:
- Cannot be tested independently
- Has no version control separate from the template
- Is hard to debug (no local execution)
- Approaches the 4096-byte ZipFile limit

**Recommendation:**
- Extract to `lambda_handlers/trigger_handler.py`
- Deploy via S3 code package (same as other Lambdas)
- Add unit tests with moto

### 6. No Staging Environment Gate

The deploy workflow pushes directly to `dev` on every main branch push. There's no staging environment or manual approval gate.

**Recommendation:**
- Add a `staging` environment in GitHub Actions with required reviewers
- Deploy flow: `main push → build → deploy staging → manual approval → deploy prod`
- The CloudFormation already supports `AllowedValues: [dev, staging, prod]` — use it
- At minimum, add a `workflow_dispatch` confirmation for production deploys

---

## Medium Priority (Next 2-4 Weeks)

### 7. DynamoDB Encryption and Backup

**File:** `cloudformation/storage.yaml`

- No `SSESpecification` on DynamoDB tables (defaults to AWS-owned key, which is fine for most cases, but explicit is better)
- No point-in-time recovery enabled on entity tables
- `DeletionPolicy: Retain` only on CacheTable and DataBucket — entity tables would be deleted on stack delete

**Recommendation:**
```yaml
PointInTimeRecoverySpecification:
  PointInTimeRecoveryEnabled: true
DeletionPolicy: Retain
```

### 8. API Gateway Security

**Files:** `cloudformation/compute.yaml` (DedupApi, MetricsApi)

- No WAF attached to API Gateway endpoints
- No rate limiting (API Gateway throttling defaults are generous)
- Basic auth over HTTPS is acceptable but consider upgrading to Cognito or IAM auth
- `AuthorizerResultTtlInSeconds: 0` means every request invokes the auth Lambda — adds latency and cost

**Recommendation:**
- Add AWS WAF with rate-limiting rule (e.g., 100 requests/minute per IP)
- Set authorizer TTL to 300 seconds (credentials don't change frequently)
- Consider adding CloudFront in front for DDoS protection

### 9. Dedup UI Lambda Size

**File:** `lambda_handlers/dedup_ui_handler.py` (47KB)

This is very large for a Lambda handler. It likely contains HTML templates inline.

**Recommendation:**
- Extract HTML/CSS/JS to S3 static hosting or CloudFront
- Lambda should only serve API endpoints
- This reduces cold start time and makes the UI independently deployable

### 10. Observability Gaps

Current state: CloudWatch alarms exist for Lambda errors/duration/throttles and DLQ depth. Missing:

- **ECS task failures** — no alarm when tasks exit non-zero
- **Pipeline end-to-end latency** — no metric for "time from content upload to Phase 3 complete"
- **Cost anomaly detection** — budget alert at 80% but no anomaly detection
- **Log retention** — 14 days may be too short for debugging intermittent issues

**Recommendation:**
- Add ECS task state change EventBridge rule → SNS on STOPPED with non-zero exit
- Emit custom CloudWatch metrics from entrypoint (phase_duration, files_processed, api_calls_made)
- Enable AWS Cost Anomaly Detection
- Increase log retention to 30 days (cost is minimal for this volume)

---

## Low Priority (Backlog)

### 11. Operational Scripts Cleanup

- `scripts/__pycache__/` is committed — add to `.gitignore`
- 60+ scripts with no categorization beyond the archive folder
- Several scripts duplicate functionality (e.g., `find_duplicate_places.py` and `find_duplicate_places_v2.py`)
- `deploy_all.sh` duplicates CI/CD workflow logic — consider deprecating in favor of `gh workflow run`

### 12. Multi-Region / DR Considerations

- All resources in `us-east-1` with no cross-region replication
- S3 versioning is enabled (good) but no cross-region replication rule
- For a data pipeline (not user-facing), this is acceptable but document the RPO/RTO

### 13. Infrastructure Testing

- No infrastructure tests (e.g., `taskcat`, `cfn-test`)
- CloudFormation drift detection not configured
- Consider adding a scheduled drift detection check

### 14. Dependency Management

- `requirements.txt` uses open ranges (`>=`) — builds are not reproducible
- No `requirements.lock` or `pip-compile` output
- Lambda package includes entire `scripts/` directory (unnecessary bloat)

**Recommendation:**
- Use `pip-compile` to generate locked requirements
- Trim Lambda package to only include needed modules

---

## What's Working Well

| Area | Assessment |
|------|-----------|
| **CloudFormation structure** | Clean nested stacks with proper parameter passing |
| **Cost optimization** | Dynamic NAT lifecycle, PAY_PER_REQUEST DynamoDB, S3 lifecycle rules, budget alerts |
| **CI/CD pipeline** | OIDC auth (no long-lived keys), Trivy scanning, comprehensive linting (7 tools) |
| **Event-driven architecture** | SNS → SQS → Lambda with batching window for debounce |
| **VPC design** | Gateway endpoints for S3/DynamoDB, interface endpoints for ECR/Secrets Manager |
| **Testing** | pytest with moto mocks, coverage reporting, nightly validation |
| **S3 bucket security** | Public access fully blocked, versioning enabled |
| **Separation of concerns** | Trigger/orchestration in Lambda, heavy processing in ECS |

---

## Priority Matrix

| # | Issue | Severity | Effort | Impact |
|---|-------|----------|--------|--------|
| 1 | Hardcoded secrets | 🟡 Partial | Low | ✅ config.yaml removed; deploy script still exposed |
| 2 | Broad IAM permissions | 🔴 Critical | Medium | Blast radius on compromise |
| 3 | Container security | 🟠 High | Low | Supply chain + privilege escalation |
| 4 | ECS timeout/error handling | 🟠 High | Medium | Runaway costs, silent failures |
| 5 | Trigger Lambda extraction | 🟠 High | Medium | Testability, maintainability |
| 6 | Staging environment | 🟠 High | Medium | Deployment safety |
| 7 | DynamoDB backup/retention | 🟡 Medium | Low | Data loss risk |
| 8 | API Gateway security | 🟡 Medium | Medium | Abuse/DDoS risk |
| 9 | Dedup UI refactor | 🟡 Medium | High | Performance, maintainability |
| 10 | Observability gaps | 🟡 Medium | Medium | Debugging difficulty |
| 11 | Scripts cleanup | 🟢 Low | Low | Developer experience |
| 12 | Multi-region/DR | 🟢 Low | High | Disaster recovery |
| 13 | Infrastructure testing | 🟢 Low | Medium | Confidence in changes |
| 14 | Dependency pinning | 🟢 Low | Low | Reproducibility |

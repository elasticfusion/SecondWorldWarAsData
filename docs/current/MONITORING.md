# Monitoring & Alerting

CloudWatch alarms, dashboard, notifications, and cost controls.

**Last Updated:** 2026-06-13

---

## CloudWatch Dashboard

**Name:** `dev-wwii-pipeline`

Displays:
- Lambda invocations, errors, and duration (trigger, batch-poller, nat-manager)
- ECS task count (running/stopped)
- Pipeline run history
- Batch job status timeline
- Container Insights metrics (CPU, memory, network I/O)

**Access:**
```bash
# Open in browser
echo "https://us-east-1.console.aws.amazon.com/cloudwatch/home?region=us-east-1#dashboards:name=dev-wwii-pipeline"
```

---

## CloudWatch Alarms

All alarms publish to the SNS topic `dev-wwii-alarms` → email notification.

| Alarm | Metric | Threshold | Period | Description |
|-------|--------|-----------|--------|-------------|
| Trigger Lambda Errors | Errors | ≥ 3 | 5 min | Pipeline orchestrator failing |
| Batch Poller Errors | Errors | ≥ 3 | 5 min | Batch job polling failing |
| NAT Manager Errors | Errors | ≥ 3 | 5 min | Networking lifecycle failing |
| ECS Task Failures | — | SNS publish on task stop | — | Pipeline task crashed (SNS notification from ECS entrypoint) |
| Batch Job Failures | — | SNS publish from poller | — | Grok batch job failed (24h timeout or API error) |

### Check Alarm State

```bash
aws cloudwatch describe-alarms \
  --alarm-name-prefix "dev-wwii" \
  --query 'MetricAlarms[].[AlarmName,StateValue,StateReason]' \
  --region us-east-1 --output table
```

### Silence an Alarm Temporarily

```bash
# Disable alarm actions (stops email notifications)
aws cloudwatch disable-alarm-actions \
  --alarm-names "dev-wwii-trigger-errors" --region us-east-1

# Re-enable
aws cloudwatch enable-alarm-actions \
  --alarm-names "dev-wwii-trigger-errors" --region us-east-1
```

---

## Email Notifications (SNS)

### Topics

| Topic | Subscribers | Triggers |
|-------|-------------|----------|
| `dev-wwii-alarms` | Email | CloudWatch alarms (Lambda errors, threshold breaches) |
| `dev-wwii-phase2-complete` | Email, nat-manager Lambda | Phase 2 ECS task completes |
| `dev-wwii-dedup-complete` | Trigger Lambda | Dedup review marked complete in UI |

### Notification Events

| Event | Content | Source |
|-------|---------|--------|
| Pipeline phase completion | Phase name, duration, entity counts | ECS entrypoint `_send_notification()` |
| Content queued | File list, "pipeline busy" message | Trigger Lambda |
| Dedup review ready | Duplicate count, review URL | Phase 2 post-process |
| Phase error | Error message, task ID, log stream | ECS entrypoint error handler |
| Batch job failure | Batch ID, failure reason | Batch poller Lambda |
| Alarm triggered | Alarm name, metric, threshold | CloudWatch → SNS |

### Verify Email Subscription

```bash
# List subscriptions
aws sns list-subscriptions-by-topic \
  --topic-arn arn:aws:sns:us-east-1:340339225515:dev-wwii-alarms \
  --region us-east-1

# Add new subscriber
aws sns subscribe \
  --topic-arn arn:aws:sns:us-east-1:340339225515:dev-wwii-alarms \
  --protocol email --notification-endpoint you@example.com \
  --region us-east-1
```

---

## Container Insights

Enabled on the ECS cluster. Provides:
- Per-task CPU and memory utilization
- Network I/O (useful for detecting high NAT data transfer costs)
- Task lifecycle events
- Container-level metrics

**View:**
```bash
echo "https://us-east-1.console.aws.amazon.com/ecs/v2/clusters/dev-wwii-pipeline/metrics?region=us-east-1"
```

---

## Cost Monitoring

### AWS Budget

- **Monthly budget:** $75
- **Alert threshold:** 80% ($60)
- **Notification:** SNS → email

### Key Cost Drivers

| Service | Risk | Mitigation |
|---------|------|-----------|
| NAT Gateway | $0.045/hr if left running | 2h max-age guardrail in openserp_manager |
| ECS Fargate | Spot interruption → relaunch → double cost | SIGTERM handler saves progress |
| Grok API | Large books = many API calls | Batch API (50% discount), DynamoDB cache |
| S3 data transfer | Phase 2/3 downloads | Incremental processing, manifest-scoped downloads |

### Check Current Month Spend

```bash
# Quick cost check (requires Cost Explorer API access)
aws ce get-cost-and-usage \
  --time-period Start=$(date -d "$(date +%Y-%m-01)" +%Y-%m-%d),End=$(date +%Y-%m-%d) \
  --granularity MONTHLY \
  --metrics BlendedCost \
  --region us-east-1 \
  --query 'ResultsByTime[0].Total.BlendedCost'
```

### Cost Runaway Detection

Signs of cost runaway:
1. NAT Gateway running for hours with no pipeline tasks
2. ECS tasks stuck in RUNNING (infinite loop or hung API call)
3. Repeated Spot interruptions causing relaunch loops

**Emergency cost stop:**
```bash
# Stop all tasks + tear down networking
bash scripts/stop_all_tasks.sh

aws lambda invoke --function-name dev-wwii-nat-manager \
  --payload '{"action": "delete"}' \
  --cli-binary-format raw-in-base64-out --region us-east-1 /tmp/out.json
```

---

## Log Analysis

### Log Groups

| Log Group | Source |
|-----------|--------|
| `/ecs/dev-wwii-pipeline` | Pipeline ECS tasks (Phase 1/2/3, import) |
| `/ecs/dev-wwii-openserp` | OpenSERP service |
| `/aws/lambda/dev-wwii-trigger` | Trigger Lambda |
| `/aws/lambda/dev-wwii-batch-poller` | Batch poller Lambda |
| `/aws/lambda/dev-wwii-nat-manager` | NAT manager Lambda |
| `/aws/lambda/dev-wwii-openserp-manager` | OpenSERP idle monitor |
| `/aws/lambda/dev-wwii-dedup-ui` | Dedup UI Lambda |

### Common Log Queries

```bash
# Tail pipeline logs (last 5 min)
aws logs tail /ecs/dev-wwii-pipeline --follow --since 5m --region us-east-1

# Find errors in last hour
aws logs filter-log-events \
  --log-group-name /ecs/dev-wwii-pipeline \
  --start-time $(date -d '1 hour ago' +%s)000 \
  --filter-pattern "ERROR" \
  --region us-east-1 \
  --query 'events[].[timestamp,message]' --output text

# Check NAT lifecycle events
aws logs filter-log-events \
  --log-group-name /aws/lambda/dev-wwii-nat-manager \
  --start-time $(date -d '24 hours ago' +%s)000 \
  --filter-pattern "?create ?delete ?NAT" \
  --region us-east-1

# Pipeline status script (color-coded, polling)
bash scripts/monitor_logs.sh 5m
```

### CloudWatch Insights Query

```
# Find long-running phases
fields @timestamp, @message
| filter @message like /phase.*complete/
| parse @message "* complete in *s" as phase, duration
| sort duration desc
| limit 20
```

---

## Metrics API

**Endpoint:** `GET https://{api-id}.execute-api.us-east-1.amazonaws.com/prod/metrics`  
**Auth:** Basic auth (same credentials as dedup UI)

Returns pipeline run history, entity counts, batch job metrics, and cost estimates.

```bash
# Get metrics URL
aws cloudformation describe-stacks --stack-name wwii-pipeline-dev --region us-east-1 \
  --query "Stacks[0].Outputs[?contains(OutputKey,'MetricsApi')].OutputValue" --output text
```

---

## S3 Lifecycle Rules (Cost Optimization)

| Rule | Prefix | Transition/Expiration |
|------|--------|----------------------|
| Temp cleanup | `tmp/` | Delete after 7 days |
| Cache expiry | `cache/` | Delete after 90 days |
| Output tiering | `output/` | → Standard-IA at 30 days → Glacier IR at 90 days |
| Version expiry | All | Noncurrent versions expire after 30 days |

---

## Health Checks

### Quick Pipeline Health

```bash
# 1. Any tasks running?
aws ecs list-tasks --cluster dev-wwii-pipeline --desired-status RUNNING --region us-east-1

# 2. NAT Gateway active?
aws ec2 describe-nat-gateways --filter "Name=tag:Name,Values=dev-wwii-nat" \
  --query 'NatGateways[?State!=`deleted`].[State,CreateTime]' --region us-east-1

# 3. Any stale locks?
aws dynamodb scan --table-name dev-wwii-api-cache \
  --filter-expression "begins_with(cache_key, :p)" \
  --expression-attribute-values '{":p":{"S":"lock#"}}' \
  --projection-expression "cache_key" --region us-east-1

# 4. Pending batch jobs?
aws dynamodb scan --table-name dev-wwii-api-cache \
  --filter-expression "begins_with(cache_key, :p) AND #s = :s" \
  --expression-attribute-values '{":p":{"S":"batch_job#"}, ":s":{"S":"pending"}}' \
  --expression-attribute-names '{"#s":"status"}' \
  --projection-expression "cache_key,batch_id" --region us-east-1

# 5. Alarms in ALARM state?
aws cloudwatch describe-alarms --state-value ALARM \
  --alarm-name-prefix "dev-wwii" --region us-east-1 \
  --query 'MetricAlarms[].AlarmName'
```

---

## Related

- [RUNBOOK.md](RUNBOOK.md) — Operational procedures
- [NETWORKING_LIFECYCLE.md](NETWORKING_LIFECYCLE.md) — NAT/VPC endpoint management
- [LAMBDA_FUNCTIONS.md](LAMBDA_FUNCTIONS.md) — EventBridge schedules and SNS topics

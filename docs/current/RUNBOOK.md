# Operations Runbook

Common operational tasks for the WWII Data Extraction Pipeline.

---

## Re-run a Failed Phase

### Local

```bash
# Re-run Phase 1 (parse)
python phase1_parse.py

# Re-run Phase 2 (extract) — uses cache, only processes new/failed files
python phase2_extract.py

# Re-run Phase 3 (enrich) — skips already-enriched entities
python phase3_enrich_data.py

# Force full re-process (ignores cache — expensive)
python phase2_extract.py --force
```

### AWS

```bash
# Manual trigger for a specific book and phase
aws lambda invoke --function-name dev-wwii-trigger \
  --payload '{"source": "manual", "book": "TheSiegfriedLineCampaign", "phase": "2"}' \
  --cli-binary-format raw-in-base64-out --invocation-type Event \
  --region us-east-1 /tmp/out.json

# Check if a task is already running (avoid duplicates)
aws ecs list-tasks --cluster dev-wwii-pipeline --desired-status RUNNING --region us-east-1
```

---

## Clear Locks

Pipeline locks prevent concurrent runs. If a task crashes without cleanup:

### AWS (DynamoDB locks)

```bash
TABLE=dev-wwii-api-cache
REGION=us-east-1

# List all locks
aws dynamodb scan --table-name $TABLE \
  --filter-expression "begins_with(cache_key, :p)" \
  --expression-attribute-values '{":p":{"S":"lock#"}}' \
  --projection-expression "cache_key" --region $REGION

# Delete a specific lock
aws dynamodb delete-item --table-name $TABLE \
  --key '{"cache_key":{"S":"lock#phase2-extract"}}' --region $REGION

# Nuclear: delete ALL locks
for key in lock#phase1-parse lock#phase2-extract lock#phase3-enrich lock#nat-manager; do
  aws dynamodb delete-item --table-name $TABLE --key "{\"cache_key\":{\"S\":\"$key\"}}" --region $REGION
done
```

### Local

No lock files — just re-run the phase.

---

## Dedup Review

### Access the UI (AWS)

```bash
# Get the function URL
aws lambda get-function-url-config --function-name dev-wwii-dedup-ui --region us-east-1
```

Open the URL in a browser. Review groups, merge/skip/exclude, then click "Mark Review Complete" to unblock Phase 3.

### Force-skip dedup review

```bash
aws dynamodb put-item --table-name dev-wwii-api-cache \
  --item '{"cache_key":{"S":"dedup_review"},"complete":{"BOOL":true},"reviewed":{"M":{}}}' \
  --region us-east-1
```

### Re-run dedup detection (local)

```bash
python scripts/find_duplicate_people.py
python scripts/find_duplicate_places_v2.py
python scripts/find_duplicate_equipment.py
python scripts/find_duplicate_groups.py
```

---

## Debug Batch Failures

### Check batch job status

```bash
# List pending/failed jobs in DynamoDB
aws dynamodb scan --table-name dev-wwii-api-cache \
  --filter-expression "begins_with(cache_key, :p)" \
  --expression-attribute-values '{":p":{"S":"batch_job#"}}' \
  --projection-expression "cache_key, #s, batch_id, request_count" \
  --expression-attribute-names '{"#s":"status"}' \
  --region us-east-1
```

### Reset a failed batch job for retry

```bash
# Set to "ready" — poller will retry on next cycle (5 min)
aws dynamodb update-item --table-name dev-wwii-api-cache \
  --key '{"cache_key":{"S":"batch_job#BATCH_ID_HERE"}}' \
  --update-expression "SET #s = :s" \
  --expression-attribute-names '{"#s":"status"}' \
  --expression-attribute-values '{":s":{"S":"ready"}}' \
  --region us-east-1
```

### Batch job state machine

```
pending  → poller checks xAI API
ready    → poller confirmed complete, launches ECS retrieve task
retrieved → ECS task downloaded results, processed entities
failed   → xAI batch failed (check API dashboard)
```

If retrieve fails, job stays "ready" and poller retries next cycle.
If batch never submitted (all cached), submit-only falls through to non-batch mode automatically.

### Check Grok batch API status directly

```bash
curl -s https://api.x.ai/v1/batch/jobs/BATCH_ID_HERE \
  -H "Authorization: Bearer $GROK_API_KEY" | python -m json.tool
```

### View batch metrics

```bash
python scripts/view_metrics.py
```

---

## Networking Issues

### NAT Gateway stuck / won't tear down

```bash
# Force delete via Lambda
aws lambda invoke --function-name dev-wwii-nat-manager \
  --payload '{"action": "delete"}' \
  --cli-binary-format raw-in-base64-out --region us-east-1 /tmp/out.json

# Verify NAT is gone
aws ec2 describe-nat-gateways --filter "Name=tag:Name,Values=dev-wwii-nat" \
  --region us-east-1 --query 'NatGateways[?State!=`deleted`]'

# Check for leaked EIPs
aws ec2 describe-addresses --filter "Name=domain,Values=vpc" \
  --query 'Addresses[?AssociationId==null]' --region us-east-1
```

### OpenSERP won't start

```bash
# Check ECS service status
aws ecs describe-services --cluster dev-wwii-pipeline \
  --services dev-wwii-openserp --region us-east-1 \
  --query 'services[0].{desired:desiredCount,running:runningCount,events:events[:3]}'
```

---

## Clear Pending Queues

If stale items are stuck in the queue:

```bash
TABLE=dev-wwii-api-cache
REGION=us-east-1

# Clear pending content (Phase 1 queue)
aws dynamodb delete-item --table-name $TABLE \
  --key '{"cache_key":{"S":"pending#content"}}' --region $REGION

# Clear pending parsed (Phase 2 queue)
aws dynamodb delete-item --table-name $TABLE \
  --key '{"cache_key":{"S":"pending#parsed"}}' --region $REGION
```

---

## Monitor Running Pipeline

```bash
# Tail logs (last 5 minutes)
bash scripts/monitor_logs.sh 5m

# Pipeline status summary
python scripts/pipeline_status.py

# Quick CLI status
bash scripts/pipeline_status.sh
```

---

## Emergency Stop

```bash
# Stop all ECS tasks immediately
bash scripts/stop_all_tasks.sh

# Tear down networking (stop $$ bleeding)
aws lambda invoke --function-name dev-wwii-nat-manager \
  --payload '{"action": "delete"}' \
  --cli-binary-format raw-in-base64-out --region us-east-1 /tmp/out.json
```

---

## Local Troubleshooting

### API cache issues

```bash
# View cache contents
python scripts/review_cache.py

# Clear cache for a specific book
python scripts/cleanup_book_cache.py --book TheSiegfriedLineCampaign
```

### Validation failures

```bash
# Full schema validation
python scripts/validate_all_output.py

# Quick quality report
python scripts/json_quality_report.py
```

### Enrichment not progressing

```bash
# Check enrichment coverage
python scripts/enrichment_stats.py

# Diagnose specific entity
python scripts/diagnose_enrichment.py output/people/some_person.json
```

---

## Undo a Merge

If a dedup merge was incorrect, use the snapshot to restore:

```bash
# Via the dedup UI API (snapshot_id is returned by the merge action)
curl -X POST https://{dedup-api-url}/dedup/api/undo \
  -H "Authorization: Basic $(echo -n user:pass | base64)" \
  -d '{"snapshot_id": "1718300000000"}'

# Or manually: list snapshots in S3
aws s3 ls s3://dev-wwii-data-pipeline/dedup/history/ --region us-east-1

# Restore a specific file from snapshot
aws s3 cp s3://dev-wwii-data-pipeline/dedup/history/{snapshot_id}/people/filename.json \
  s3://dev-wwii-data-pipeline/output/people/filename.json --region us-east-1
```

---

## OpenSERP Circuit Breaker

If OpenSERP is failing and you see "circuit breaker OPEN" in logs:

```bash
# Check if OpenSERP is actually running
aws ecs describe-services --cluster dev-wwii-pipeline \
  --services dev-wwii-openserp --region us-east-1 \
  --query 'services[0].{desired:desiredCount,running:runningCount}'

# The circuit breaker resets automatically on next task start.
# To force reset mid-run: restart the ECS task (kills and relaunches).
```

---

## Spot Interruption Recovery

Fargate Spot tasks (4:1 weight over regular) can be interrupted with 30s warning (SIGTERM).

### What Happens Automatically

1. SIGTERM → emergency S3 sync (all entity + event files uploaded)
2. Lock cleared in SIGTERM handler
3. Task exits cleanly
4. EventBridge ECS Task State Change rule detects `stoppedReason: "Your Spot Task was interrupted"`
5. Trigger Lambda clears any residual lock and re-launches the task
6. New task downloads from S3, hits DynamoDB cache → resumes without API cost

### If Auto-Recovery Fails

```bash
# Check if task was spot-interrupted
aws ecs list-tasks --cluster dev-wwii-pipeline --desired-status STOPPED --region us-east-1 \
  --query 'taskArns[:3]' --output text | xargs -I{} \
  aws ecs describe-tasks --cluster dev-wwii-pipeline --tasks {} --region us-east-1 \
  --query 'tasks[].[taskDefinitionArn,stoppedReason,stoppedAt]' --output table

# Clear stale lock manually
aws dynamodb delete-item --table-name dev-wwii-api-cache \
  --key '{"cache_key":{"S":"lock#phase2-extract"}}' --region us-east-1

# Re-trigger manually
aws lambda invoke --function-name dev-wwii-trigger \
  --payload '{"source": "manual", "phase": "2"}' \
  --cli-binary-format raw-in-base64-out --invocation-type Event \
  --region us-east-1 /tmp/out.json
```

### Data Loss Assessment

| Component | Protected By | Max Loss |
|-----------|-------------|----------|
| Grok API responses | DynamoDB cache (written per call) | None |
| Entity files | Background sync (120s) + SIGTERM handler | None if SIGTERM works |
| Batch job state | DynamoDB | None |
| Event files | Background sync + SIGTERM handler | None if SIGTERM works |

---

## Cost Runaway

### Symptoms

- Budget alert email (>$60/month)
- NAT Gateway running for hours with no pipeline tasks
- ECS tasks stuck in RUNNING state
- Repeated Spot interruptions causing launch loops

### Immediate Actions

```bash
# 1. Stop all pipeline tasks
bash scripts/stop_all_tasks.sh

# 2. Tear down NAT (stops $0.045/hr bleeding)
aws lambda invoke --function-name dev-wwii-nat-manager \
  --payload '{"action": "delete"}' \
  --cli-binary-format raw-in-base64-out --region us-east-1 /tmp/out.json

# 3. Scale OpenSERP to 0
aws ecs update-service --cluster dev-wwii-pipeline \
  --service dev-wwii-openserp --desired-count 0 --region us-east-1

# 4. Check what's been costing money
aws ce get-cost-and-usage \
  --time-period Start=$(date -d "7 days ago" +%Y-%m-%d),End=$(date +%Y-%m-%d) \
  --granularity DAILY --metrics BlendedCost \
  --group-by Type=DIMENSION,Key=SERVICE \
  --region us-east-1
```

### Common Causes

| Cause | Fix |
|-------|-----|
| NAT stuck up (openserp_manager not running) | Force delete via nat_manager |
| ECS task in infinite loop | Stop task, check logs, fix bug |
| Batch poller launching tasks repeatedly | Check `batch_job#` entries in DynamoDB — mark as `failed` |
| Spot interruption loop (task keeps getting interrupted) | Temporarily switch to regular Fargate in task definition |

### Preventive Controls

- **2h NAT guardrail:** openserp_manager force-tears down NAT after 2h regardless of state
- **AWS Budget:** $75/month with 80% alert
- **Batch job 24h timeout:** Poller marks jobs `failed` after 24 hours
- **S3 lifecycle rules:** Temp files expire after 7 days, cache after 90 days

---

## Batch Job Stuck

### Symptoms

- Batch poller running every 5 min but job stays `pending` for hours
- No retrieve task launching
- Phase 2/3 completion blocked

### Diagnosis

```bash
# List all batch jobs and their status
aws dynamodb scan --table-name dev-wwii-api-cache \
  --filter-expression "begins_with(cache_key, :p)" \
  --expression-attribute-values '{":p":{"S":"batch_job#"}}' \
  --projection-expression "cache_key, #s, batch_id, request_count, created_at" \
  --expression-attribute-names '{"#s":"status"}' \
  --region us-east-1

# Check Grok Batch API directly
curl -s https://api.x.ai/v1/batch/jobs/BATCH_ID_HERE \
  -H "Authorization: Bearer $GROK_API_KEY" | python3 -m json.tool
```

### Fixes

```bash
# Option 1: Reset to "ready" (poller will launch retrieve task on next cycle)
aws dynamodb update-item --table-name dev-wwii-api-cache \
  --key '{"cache_key":{"S":"batch_job#BATCH_ID_HERE"}}' \
  --update-expression "SET #s = :s" \
  --expression-attribute-names '{"#s":"status"}' \
  --expression-attribute-values '{":s":{"S":"ready"}}' \
  --region us-east-1

# Option 2: Mark as failed and re-run Phase 2 (resubmits batch)
aws dynamodb update-item --table-name dev-wwii-api-cache \
  --key '{"cache_key":{"S":"batch_job#BATCH_ID_HERE"}}' \
  --update-expression "SET #s = :s" \
  --expression-attribute-names '{"#s":"status"}' \
  --expression-attribute-values '{":s":{"S":"failed"}}' \
  --region us-east-1

# Then re-trigger Phase 2
aws lambda invoke --function-name dev-wwii-trigger \
  --payload '{"source": "manual", "phase": "2"}' \
  --cli-binary-format raw-in-base64-out --invocation-type Event \
  --region us-east-1 /tmp/out.json

# Option 3: Delete the batch job entry entirely (nuclear — loses tracking)
aws dynamodb delete-item --table-name dev-wwii-api-cache \
  --key '{"cache_key":{"S":"batch_job#BATCH_ID_HERE"}}' --region us-east-1
```

### Batch Job State Machine

```
pending  → poller checks xAI API status every 5 min
ready    → poller confirmed complete, launches ECS retrieve task
complete → retrieve task processed results successfully
failed   → xAI batch failed OR 24h timeout exceeded
```

If retrieve fails, job stays `ready` and poller retries on next cycle without re-checking Grok.

---

## Force Re-extraction of Specific Entity Type

If an entity type produced zero or low results:

```yaml
# Add to config.yaml:
processing:
  reprocess_types: ["people"]  # Forces re-extraction even if already processed
```

Then re-run Phase 2. Remove the config entry after.

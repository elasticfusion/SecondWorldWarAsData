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
aws dynamodb update-item --table-name dev-wwii-api-cache \
  --key '{"cache_key":{"S":"batch_job#BATCH_ID_HERE"}}' \
  --update-expression "SET #s = :s" \
  --expression-attribute-names '{"#s":"status"}' \
  --expression-attribute-values '{":s":{"S":"pending"}}' \
  --region us-east-1
```

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

# Operations Runbook

Common operational tasks for the WWII Data Extraction Pipeline.
Key catalogue for locks, leases and queues: [core/LOCKS_AND_LEASES.md](core/LOCKS_AND_LEASES.md). Document states and off-ramps: [core/DOC_LIFECYCLE.md](core/DOC_LIFECYCLE.md).

---

## Re-run a Failed Phase

### Local

```bash
# Re-run Phase 1 (parse)
python phase1_parse.py

# Re-run Phase 2 (extract) for one book — uses the cache, only processes new/failed files.
# Without --book ALL parsed chapters are processed; --expected-count N aborts if the
# number of chapters to process differs (run-scope guard).
python phase2_extract.py --book TheSiegfriedLineCampaign

# Re-run Phase 3 (enrich) — skips already-enriched entities
python phase3_enrich_data.py
```

To force re-extraction of an entity type, see "Force Re-extraction of Specific Entity Type" below. For a full dev reset use `scripts/e2e_reset.sh --env dev --full`: it keeps the api-cache `grok`/`fetch`/`search`/`url`/`place_coords`/`metrics` rows (Wikipedia and Grokipedia lookups are `search#wikipedia#…` / `search#grokipedia#…` rows), the human dedup `exclusion#` rows and `contentrepository/` (`scripts/e2e_reset_data.py`).

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

## Snapshot and restore a data set

`scripts/phase_snapshot.py` saves the pipeline's data set and later returns the live data to it, for
example to re-run Phase 3 from the same Phase 2 output while Phase 3 is being fixed. Every mode refuses
to run while any ECS task in the pipeline cluster is running or starting, except the OpenSERP search
server (it writes nothing; the OpenSERP worker does, so it counts), and exits non-zero on any AWS
error. Lambdas are not checked: do not run a restore while a batch is pending (`batch_job#` rows, the
batch poller resumes a book) or uploads are arriving. Use the AWS prefix from this runbook
(`AWS_PROFILE=wwii`). A backup is complete once `backups/<name>/manifest.json` exists (written last);
restore and verify refuse a backup without it.

```bash
# save: S3 output/ dedup/ manifests/ -> backups/<name>/, DynamoDB on-demand backup <name>,
# every DynamoDB key + manifest.json. Names: 3-64 of [A-Za-z0-9._-]; a used name is refused.
python3 scripts/phase_snapshot.py backup --name phase2-2026-11-17

# compare live data with the backup (exit 1 on any difference); changes nothing
python3 scripts/phase_snapshot.py verify --name phase2-2026-11-17

# restore: prints the plan only; --apply changes data, then re-checks and prints "restored"
python3 scripts/phase_snapshot.py restore --name phase2-2026-11-17
python3 scripts/phase_snapshot.py restore --name phase2-2026-11-17 --apply
```

What a restore does:

| Data | Rule |
|---|---|
| S3 `output/`, `dedup/`, `manifests/` | Made an exact copy of the backup: changed or missing objects copied back, objects added since deleted. The bucket is versioned, so anything deleted or overwritten stays recoverable as an older version. |
| DynamoDB cached Grok answers (`<cache_type>#<64-hex>`, also under `books/<book>/`) | Kept as they are, so a re-run does not pay for them again. |
| DynamoDB run state, locks, verdict and registry caches, fetch events and claims, dedup rows (`RESET_PREFIXES` in the script) | Returned to the snapshot: rows added since are deleted; rows changed or deleted since are put back. To find those, the table backup is restored to a temporary table `<table>-snap-<name>` (a few minutes, well under a cent), compared and dropped. `--no-table-restore` skips that and only deletes added rows. |
| `reaper#state` (the idle reaper's hourly counter) | Ignored. |
| A snapshot row whose DynamoDB TTL has passed | Treated as absent (put back, TTL would delete it again). |
| Any other key | Unclassified: left alone and listed; `verify` fails until its prefix is added to the script's rules. |

Copying a `output/content/*-parsed.json` file back fires the bucket's Phase 2 trigger (S3 -> SNS ->
trigger Lambda). `restore --apply` refuses while its plan copies any, unless `--allow-phase2-trigger`
is given. Every other object copied back under `output/` is re-checked by the S3 validation backstop
Lambda (EventBridge "Object Created"); a valid record causes no write or alert, but if the schemas
changed since the backup, a restored record that no longer validates is flagged and alerted.

If a run is interrupted while the temporary table exists, the next run refuses and prints the
`aws dynamodb delete-table --table-name <table>-snap-<name>` command to clean it up.

Permissions needed: S3 list/get/put/copy/delete on the bucket; DynamoDB `CreateBackup`,
`DescribeBackup`, `ListBackups`, `RestoreTableFromBackup`, `ListTables`, `DeleteTable`, `Scan`,
`BatchWriteItem` (live and temporary table); ECS `ListTasks`, `DescribeTasks`. A table backup can only restore into a new table, so the script never replaces the live
table. Whole-table recovery (from the backup, or from point-in-time recovery, which keeps 35 days)
also lands in a new table, which the pipeline would then have to be pointed at: on ECS the task's
`CACHE_TABLE` env var (the entrypoint copies it into `aws.cache_table`, `ecs_entrypoint.py:507-509`);
locally `aws.cache_table` in `config.yaml`.

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

# Delete a specific lock (key = lock#{env}-wwii-<phase>, plus #<book> when multi-doc mode is on)
aws dynamodb delete-item --table-name $TABLE \
  --key '{"cache_key":{"S":"lock#dev-wwii-phase2-extract"}}' --region $REGION

# Nuclear: delete ALL phase locks (the phase suffixes are ecs_entrypoint.PHASE_SUFFIXES)
for key in lock#dev-wwii-phase1-parse lock#dev-wwii-phase2-extract lock#dev-wwii-phase3-enrich lock#dev-wwii-import; do
  aws dynamodb delete-item --table-name $TABLE --key "{\"cache_key\":{\"S\":\"$key\"}}" --region $REGION
done
```

### Local

No lock files — just re-run the phase.

---

## Dedup Review

### Access the UI (AWS)

```bash
# The UI is behind API Gateway; its URL is the DedupReviewUrl stack output
python3 scripts/deploy_aws.py status --env dev
```

Open the URL in a browser. Review groups, merge/skip/exclude, then click "Mark Review Complete" to unblock Phase 3.

### Force-skip dedup review

```bash
# Phase 3 is launched by the trigger Lambda when the dedup-complete topic fires (what the UI's
# "Mark Review Complete" publishes after writing dedup/review_status.json). Publish it directly:
aws sns publish --region us-east-1 \
  --topic-arn arn:aws:sns:us-east-1:<account-id>:dev-wwii-dedup-complete \
  --message '{"dedup_complete": true}'
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
in_task  → a running Phase 2 task is waiting for the batch (batch.in_task_wait_minutes, 5); the poller adopts it as pending after 2 h
pending  → poller checks xAI API (5-min schedule)
ready    → poller confirmed complete, launches ECS --retrieve-only task
retrieved → ECS task downloaded results, processed entities (a later round may submit again; cap 8 rounds)
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
  --payload '{"action": "delete", "force": true}' \
  --cli-binary-format raw-in-base64-out --region us-east-1 /tmp/out.json

# Verify NAT is gone
aws ec2 describe-nat-gateways --filter "Name=tag:Name,Values=dev-nat" \
  --region us-east-1 --query 'NatGateways[?State!=`deleted`]'

# Check for leaked EIPs
aws ec2 describe-addresses --filter "Name=domain,Values=vpc" \
  --query 'Addresses[?AssociationId==null]' --region us-east-1
```

### OpenSERP won't start

The `dev-wwii-openserp` ECS service sits at desired 0 and is scaled to 1 by `ecs_entrypoint._start_openserp_if_needed` at the start of Phase 3 only (waits up to 12 min for the task IP). `openserp-manager` scales it back to 0 when idle.

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

# Clear pending parsed (Phase 2 queue; one row per book: pending#parsed#<book>)
aws dynamodb delete-item --table-name $TABLE \
  --key '{"cache_key":{"S":"pending#parsed#BOOK_NAME"}}' --region $REGION
```

---

## Monitor Running Pipeline

```bash
# Tail logs (last 5 minutes)
bash scripts/utils/monitor_logs.sh 5m

# Pipeline status summary
python scripts/utils/pipeline_status.py

# Quick CLI status
bash scripts/utils/pipeline_status.sh
```

---

## Emergency Stop

```bash
# Stop all ECS tasks immediately
bash scripts/stop_all_tasks.sh

# Tear down networking (stop $$ bleeding; force skips the demand check)
aws lambda invoke --function-name dev-wwii-nat-manager \
  --payload '{"action": "delete", "force": true}' \
  --cli-binary-format raw-in-base64-out --region us-east-1 /tmp/out.json
```

---

## Local Troubleshooting

### API cache issues

```bash
# View cache contents
python scripts/utils/review_cache.py

# Clear cache for a specific book
python scripts/cleanup_book_cache.py --book TheSiegfriedLineCampaign
```

### Validation failures

```bash
# Full schema validation
python scripts/utils/validate_all_output.py

# Quick quality report
python scripts/utils/json_quality_report.py
```

### Enrichment not progressing

```bash
# Check enrichment coverage
python scripts/utils/enrichment_stats.py

# Diagnose specific entity
python scripts/utils/diagnose_enrichment.py output/people/some_person.json
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
4. EventBridge rule `dev-wwii-spot-recovery` detects an ECS Task State Change with a `stoppedReason` starting `Your Spot Task`
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
  --key '{"cache_key":{"S":"lock#dev-wwii-phase2-extract"}}' --region us-east-1

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

# 2. Tear down NAT (stops $0.045/hr bleeding; force skips the demand check)
aws lambda invoke --function-name dev-wwii-nat-manager \
  --payload '{"action": "delete", "force": true}' \
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
| NAT stuck up (nat-teardown / openserp_manager not running) | Force delete via nat_manager |
| ECS task in infinite loop | Stop task, check logs, fix bug |
| Batch poller launching tasks repeatedly | Check `batch_job#` entries in DynamoDB — mark as `failed` |
| Spot interruption loop (task keeps getting interrupted) | Temporarily switch to regular Fargate in task definition |

### Preventive Controls

- **NAT teardown:** `nat-teardown` (every 4 h) deletes NAT when nothing is queued or running; `idle-backstop-reaper` (hourly) force-tears-down on a confirmed no-progress stall
- **2h NAT guardrail:** openserp_manager force-tears down NAT after 2h with no pipeline tasks
- **AWS Budget:** $75/month with 80% alert
- **Batch rounds cap:** Phase 2 stops after `batch.max_rounds` (8) rounds and raises `batch.rounds_over_cap`
- **Batch job 24h rule:** a batch over 24 h old with some successes is forced `complete` by the poller
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
retrieved → retrieve task processed results successfully
failed   → xAI batch failed
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

See also: [Dedup review gate (Phase 2 to Phase 3)](core/DEDUP_REVIEW_GATE.md) for detection, auto-merge, the gate, the review UI and the operator runbook.

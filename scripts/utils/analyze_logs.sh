#!/bin/bash
# Temporary script: fetch and analyze last 24h of pipeline logs
REGION="us-east-1"
OUT="/tmp/log_analysis_$(date +%Y%m%d_%H%M%S)"
mkdir -p "$OUT"

echo "Fetching pipeline logs..."
aws logs tail /ecs/dev-wwii-pipeline --region $REGION --since 24h --no-cli-pager > "$OUT/pipeline.txt" 2>&1
echo "Fetching trigger logs..."
aws logs tail /aws/lambda/dev-wwii-trigger --region $REGION --since 24h --no-cli-pager > "$OUT/trigger.txt" 2>&1

echo ""
echo "=== SUMMARY ==="
echo "Pipeline log lines: $(wc -l < "$OUT/pipeline.txt")"
echo "Trigger log lines: $(wc -l < "$OUT/trigger.txt")"

echo ""
echo "=== PHASE 1 RUNS ==="
grep -c "Phase 1 complete\|Phase 1 already running\|Patched config" "$OUT/pipeline.txt" | head -1
grep "Phase 1 complete\|Phase 1 already running\|Reset dedup\|Cleared lock\|Cleared manifest" "$OUT/pipeline.txt"

echo ""
echo "=== PHASE 2 RUNS ==="
grep "Phase 2 incremental\|Created batch\|Batch.*complete\|phase2.*Patched config" "$OUT/pipeline.txt"

echo ""
echo "=== PHASE 3 RUNS ==="
grep "phase3.*Patched config\|Enrichment complete\|Enriched.*people\|Downloaded.*manifest" "$OUT/pipeline.txt"

echo ""
echo "=== ERRORS ==="
grep -i "ERROR\|FAILED\|exception\|Traceback" "$OUT/pipeline.txt" "$OUT/trigger.txt" | grep -v "already locked"

echo ""
echo "=== TRIGGER LAMBDA ==="
grep "Launching\|already locked\|Dedup complete\|queue\|pending" "$OUT/trigger.txt"

echo ""
echo "=== BATCH API STATUS ==="
grep "Batch.*complete\|success.*error.*pending\|poll_batch\|success + error\|timed out" "$OUT/pipeline.txt"

echo ""
echo "=== CACHE STATS ==="
grep "Cache stats:" "$OUT/pipeline.txt"

echo ""
echo "=== LOCKS ==="
grep "lock\|Lock" "$OUT/pipeline.txt" "$OUT/trigger.txt" | grep -v "awslogs"

echo ""
echo "=== TIMELINE ==="
grep "Patched config\|Phase.*complete\|Final sync\|Sent completion\|already running" "$OUT/pipeline.txt" | sed 's/T/ /' | cut -c1-80

echo ""
echo "Raw logs saved to: $OUT/"

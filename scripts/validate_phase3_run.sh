#!/bin/bash
# Validate Phase 3 run results after completion.
# Run after receiving the completion email notification.

REGION="us-east-1"
BUCKET="dev-wwii-data-pipeline"
PASS=0
FAIL=0

echo "=== Phase 3 Post-Run Validation ==="
echo ""

# Sync from S3
echo "--- Syncing from S3 ---"
aws s3 sync s3://$BUCKET/output/ output/ --region $REGION --delete --quiet
echo "  Done"
echo ""

# 1. Schema versioning
echo "--- 1. Schema Versioning ---"
SCHEMA=$(python3 -c "
import json
from pathlib import Path
f = next((f for f in Path('output/weather').glob('*.json') if f.name != 'index.json'), None)
if f: d = json.loads(f.read_text()); print(d.get('_schema_version', 'MISSING'))
else: print('NO_FILES')
")
if [ "$SCHEMA" = "2.3" ]; then
    echo "  ✓ Schema version: $SCHEMA"
    PASS=$((PASS+1))
else
    echo "  ✗ Schema version: $SCHEMA (expected 2.3)"
    FAIL=$((FAIL+1))
fi
echo ""

# 2. Enrichment status
echo "--- 2. Enrichment Status ---"
python3 scripts/enrichment_stats.py
ENRICHED=$(python3 -c "
import json
from pathlib import Path
count = sum(1 for f in Path('output/people').glob('*.json') if f.name not in ('index.json','duplicate_report.json','not_duplicates.json') and json.loads(f.read_text()).get('enrichment_status') in ('enriched','not_found'))
print(count)
")
if [ "$ENRICHED" -gt "0" ]; then
    echo "  ✓ $ENRICHED people have enrichment_status set"
    PASS=$((PASS+1))
else
    echo "  ✗ No enrichment_status found on people files"
    FAIL=$((FAIL+1))
fi
echo ""

# 3. Actual data
echo "--- 3. Actual Data Presence ---"
python3 scripts/enrichment_data_check.py
echo ""

# 4. Schema validation
echo "--- 4. Schema Validation ---"
RESULT=$(python3 scripts/validate_all_output.py 2>&1 | tail -1)
echo "  $RESULT"
if echo "$RESULT" | grep -q "need migration"; then
    MIGRATE=$(echo "$RESULT" | grep -oP '\d+ need migration')
    if echo "$MIGRATE" | grep -q "^0 "; then
        PASS=$((PASS+1))
    else
        echo "  ✗ Files still need migration"
        FAIL=$((FAIL+1))
    fi
fi
echo ""

# 5. NAT teardown
echo "--- 5. NAT Teardown ---"
NAT=$(aws ec2 describe-nat-gateways --filter "Name=state,Values=available" "Name=tag:ManagedBy,Values=dev-wwii-pipeline" --region $REGION --query "NatGateways" --output text 2>/dev/null)
if [ -z "$NAT" ]; then
    echo "  ✓ NAT torn down (no active NAT)"
    PASS=$((PASS+1))
else
    echo "  ✗ NAT still active"
    FAIL=$((FAIL+1))
fi
echo ""

# 6. OpenSERP
echo "--- 6. OpenSERP Discovery ---"
OPENSERP=$(aws logs filter-log-events --log-group-name /ecs/dev-wwii-pipeline --region $REGION --start-time $(date -d '3 hours ago' +%s000) --filter-pattern "OpenSERP running at" --query "events[].message" --output text 2>/dev/null | head -1)
if [ -n "$OPENSERP" ]; then
    echo "  ✓ $OPENSERP"
    PASS=$((PASS+1))
else
    echo "  ✗ OpenSERP not discovered (check logs)"
    FAIL=$((FAIL+1))
fi
echo ""

# 7. No LOC timeouts
echo "--- 7. No LOC Timeouts ---"
LOC=$(aws logs filter-log-events --log-group-name /ecs/dev-wwii-pipeline --region $REGION --start-time $(date -d '3 hours ago' +%s000) --filter-pattern "loc.gov" --query "events[].message" --output text 2>/dev/null | wc -l)
if [ "$LOC" -eq "0" ]; then
    echo "  ✓ No LOC timeout errors"
    PASS=$((PASS+1))
else
    echo "  ✗ $LOC LOC timeout entries found"
    FAIL=$((FAIL+1))
fi
echo ""

# 8. Final sync
echo "--- 8. Final Sync ---"
SYNC=$(aws logs filter-log-events --log-group-name /ecs/dev-wwii-pipeline --region $REGION --start-time $(date -d '3 hours ago' +%s000) --filter-pattern "Final sync" --query "events[].message" --output text 2>/dev/null)
if echo "$SYNC" | grep -qP "uploaded \d{3,} entity"; then
    echo "  ✓ $SYNC"
    PASS=$((PASS+1))
else
    echo "  ✗ Final sync: $SYNC"
    FAIL=$((FAIL+1))
fi
echo ""

# Summary
echo "========================================"
echo "Results: $PASS passed, $FAIL failed"
if [ "$FAIL" -eq "0" ]; then
    echo "✓ All checks passed!"
else
    echo "✗ Some checks failed — review above"
fi

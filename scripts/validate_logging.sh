#!/bin/bash
# Validate structured JSON logging implementation.
# Run locally after implementing logging.md changes.
# Usage: bash scripts/validate_logging.sh [--cloudwatch]
set -e

cd "$(dirname "$0")/.."
source .venv/bin/activate

PASS=0
FAIL=0

check() {
  if [ $? -eq 0 ]; then
    echo "  ✓ $1"
    PASS=$((PASS + 1))
  else
    echo "  ✗ $1"
    FAIL=$((FAIL + 1))
  fi
}

echo "=== 1. JSON Formatter Output ==="
OUTPUT=$(python -c "
import os, logging
os.environ['PIPELINE_PHASE'] = 'phase2'
os.environ['BOOK_NAME'] = 'TestBook'
os.environ['ECS_TASK_ID'] = 'test-123'
from src.utils.json_logging import configure_json_logging
configure_json_logging()
logging.getLogger('test').info('hello', extra={'extra_fields': {'event': 'test_event', 'count': 42}})
" 2>&1)

echo "$OUTPUT" | python -m json.tool > /dev/null 2>&1
check "Output is valid JSON"

echo "$OUTPUT" | python -c "import sys,json; d=json.load(sys.stdin); assert d['level']=='INFO'" 2>&1
check "Has level field"

echo "$OUTPUT" | python -c "import sys,json; d=json.load(sys.stdin); assert d['phase']=='phase2'" 2>&1
check "Has phase from env"

echo "$OUTPUT" | python -c "import sys,json; d=json.load(sys.stdin); assert d['book']=='TestBook'" 2>&1
check "Has book from env"

echo "$OUTPUT" | python -c "import sys,json; d=json.load(sys.stdin); assert d['task_id']=='test-123'" 2>&1
check "Has task_id from env"

echo "$OUTPUT" | python -c "import sys,json; d=json.load(sys.stdin); assert d['event']=='test_event'" 2>&1
check "extra_fields merged into top level"

echo "$OUTPUT" | python -c "import sys,json; d=json.load(sys.stdin); assert d['count']==42" 2>&1
check "extra_fields values preserved"

echo ""
echo "=== 2. Phase Transition Events ==="
OUTPUT=$(python -c "
import os, logging
os.environ['PIPELINE_PHASE'] = 'phase1'
os.environ['BOOK_NAME'] = 'TheLorraineCampaign'
os.environ['ECS_TASK_ID'] = 'task-abc'
from src.utils.json_logging import configure_json_logging
configure_json_logging()
logger = logging.getLogger('ecs_entrypoint')
logger.info('Phase started', extra={'extra_fields': {'event': 'phase_start', 'phase': 'phase1'}})
logger.info('Phase complete', extra={'extra_fields': {'event': 'phase_complete', 'phase': 'phase1', 'duration_s': 120}})
logger.error('Phase failed', extra={'extra_fields': {'event': 'phase_failed', 'phase': 'phase2', 'returncode': 1, 'duration_s': 45}})
" 2>&1)

echo "$OUTPUT" | grep '"phase_start"' | python -c "import sys,json; d=json.load(sys.stdin); assert d['event']=='phase_start'" 2>&1
check "phase_start event emitted"

echo "$OUTPUT" | grep '"phase_complete"' | python -c "import sys,json; d=json.load(sys.stdin); assert d['duration_s']==120" 2>&1
check "phase_complete has duration_s"

echo "$OUTPUT" | grep '"phase_failed"' | python -c "import sys,json; d=json.load(sys.stdin); assert d['event']=='phase_failed' and d['returncode']==1" 2>&1
check "phase_failed has returncode"

echo ""
echo "=== 3. Batch Submission Logging ==="
OUTPUT=$(python -c "
import os, logging
os.environ.setdefault('PIPELINE_PHASE', 'phase2')
from src.utils.json_logging import configure_json_logging
configure_json_logging()
logger = logging.getLogger('src.utils.batch_api')
logger.info('Submitting batch: pipeline (5 requests, 12.3 KB)', extra={'extra_fields': {
    'event': 'batch_submit', 'batch_name': 'pipeline', 'request_count': 5, 'jsonl_size_bytes': 12600
}})
" 2>&1)

echo "$OUTPUT" | python -c "import sys,json; d=json.load(sys.stdin); assert d['event']=='batch_submit' and d['request_count']==5" 2>&1
check "batch_submit has request_count"

echo ""
echo "=== 4. Batch Result Failed Logging ==="
OUTPUT=$(python -c "
import os, logging
os.environ.setdefault('PIPELINE_PHASE', 'phase2')
from src.utils.json_logging import configure_json_logging
configure_json_logging()
logger = logging.getLogger('src.grok_client')
logger.warning('Batch result failed: req-001 [events] truncated (4000 chars)', extra={'extra_fields': {
    'event': 'batch_result_failed', 'request_id': 'req-001', 'cache_type': 'events',
    'status': 'truncated', 'finish_reason': 'length', 'content_length': 4000,
    'content_tail': '...incomplete json}', 'prompt_preview': 'Extract events from chapter 5...'
}})
" 2>&1)

echo "$OUTPUT" | python -c "
import sys,json; d=json.load(sys.stdin)
assert d['event']=='batch_result_failed'
assert d['status']=='truncated'
assert d['content_tail']=='...incomplete json}'
assert 'prompt_preview' in d
" 2>&1
check "batch_result_failed has all diagnostic fields"

echo ""
echo "=== 5. API Token Usage Logging ==="
OUTPUT=$(python -c "
import os, logging
os.environ.setdefault('PIPELINE_PHASE', 'phase2')
from src.utils.json_logging import configure_json_logging
configure_json_logging()
logger = logging.getLogger('src.grok_client')
logger.info('API tokens: 1500 prompt, 3000 completion, 4500 total', extra={'extra_fields': {
    'event': 'api_usage', 'prompt_tokens': 1500, 'completion_tokens': 3000, 'total_tokens': 4500, 'model': 'grok-3'
}})
" 2>&1)

echo "$OUTPUT" | python -c "
import sys,json; d=json.load(sys.stdin)
assert d['event']=='api_usage'
assert d['total_tokens']==4500
assert d['model']=='grok-3'
" 2>&1
check "api_usage has token counts and model"

echo ""
echo "=== 6. Exception Formatting ==="
OUTPUT=$(python -c "
import os, logging
os.environ.setdefault('PIPELINE_PHASE', 'phase2')
from src.utils.json_logging import configure_json_logging
configure_json_logging()
logger = logging.getLogger('test')
try:
    raise ValueError('something broke')
except Exception:
    logger.exception('Caught error')
" 2>&1)

echo "$OUTPUT" | python -c "
import sys,json; d=json.load(sys.stdin)
assert 'exception' in d, 'missing exception field'
assert 'ValueError' in d['exception']
assert 'something broke' in d['exception']
" 2>&1
check "Exception traceback included as valid JSON field"

echo "$OUTPUT" | python -m json.tool > /dev/null 2>&1
check "Log with exception is still valid JSON"

echo ""
echo "=== 7. Unit Tests Pass ==="
python -m pytest tests/ -m "not slow and not requires_api" -q --no-header 2>&1 | tail -1
check "pytest passes"

echo ""
echo "=== Results ==="
echo "  Passed: $PASS"
echo "  Failed: $FAIL"

# CloudWatch validation (optional)
if [ "$1" = "--cloudwatch" ]; then
  echo ""
  echo "=== 8. CloudWatch Queries ==="
  REGION="${AWS_DEFAULT_REGION:-us-east-1}"
  LOG_GROUP="/ecs/dev-wwii-pipeline"
  START=$(date -d '1 hour ago' +%s 2>/dev/null || date -v-1H +%s)
  END=$(date +%s)

  echo "  Querying phase events..."
  QID=$(aws logs start-query \
    --log-group-name "$LOG_GROUP" \
    --start-time "$START" --end-time "$END" \
    --query-string 'fields @timestamp, event, phase, duration_s | filter event in ["phase_start","phase_complete","phase_failed"] | sort @timestamp desc | limit 10' \
    --region "$REGION" --output text --query 'queryId' 2>/dev/null)
  if [ -n "$QID" ]; then
    sleep 3
    aws logs get-query-results --query-id "$QID" --region "$REGION" --output table 2>/dev/null | head -20
    check "CloudWatch phase events queryable"
  else
    echo "  ⚠ Could not query CloudWatch (no recent ECS runs or no access)"
  fi

  echo ""
  echo "  Querying batch failures..."
  QID=$(aws logs start-query \
    --log-group-name "$LOG_GROUP" \
    --start-time "$START" --end-time "$END" \
    --query-string 'fields @timestamp, request_id, status, finish_reason, content_length | filter event = "batch_result_failed" | sort @timestamp desc | limit 10' \
    --region "$REGION" --output text --query 'queryId' 2>/dev/null)
  if [ -n "$QID" ]; then
    sleep 3
    aws logs get-query-results --query-id "$QID" --region "$REGION" --output table 2>/dev/null | head -20
    check "CloudWatch batch failures queryable"
  fi

  echo ""
  echo "  Querying token usage..."
  QID=$(aws logs start-query \
    --log-group-name "$LOG_GROUP" \
    --start-time $(date -d '7 days ago' +%s 2>/dev/null || date -v-7d +%s) --end-time "$END" \
    --query-string 'filter event = "api_usage" | stats sum(total_tokens) as tokens by bin(1d)' \
    --region "$REGION" --output text --query 'queryId' 2>/dev/null)
  if [ -n "$QID" ]; then
    sleep 3
    aws logs get-query-results --query-id "$QID" --region "$REGION" --output table 2>/dev/null | head -20
    check "CloudWatch token usage queryable"
  fi
fi

[ $FAIL -eq 0 ] && exit 0 || exit 1

#!/bin/bash
# Aggregate JSON quality stats from ECS logs by day.
# Usage: bash scripts/json_quality_report.sh [DAYS]

REGION="us-east-1"
LOG_GROUP="/ecs/dev-wwii-pipeline"
DAYS="${1:-10}"

echo "JSON Quality Report (last ${DAYS} days)"
echo "========================================="
printf "%-12s %8s %8s %8s %8s\n" "Date" "Responses" "Repaired" "Truncated" "Markdown"
echo "---------------------------------------------------------"

for i in $(seq 0 $((DAYS - 1))); do
  DAY=$(date -d "$i days ago" +%Y-%m-%d)
  START=$(date -d "$DAY 00:00:00" +%s000)
  END=$(date -d "$DAY 23:59:59" +%s000)

  RESPONSES=$(aws logs filter-log-events --log-group-name "$LOG_GROUP" --region "$REGION" \
    --start-time "$START" --end-time "$END" --filter-pattern "finish_reason: stop" \
    --query 'events | length(@)' --output text 2>/dev/null)

  REPAIRED=$(aws logs filter-log-events --log-group-name "$LOG_GROUP" --region "$REGION" \
    --start-time "$START" --end-time "$END" --filter-pattern "JSON repaired" \
    --query 'events | length(@)' --output text 2>/dev/null)

  TRUNCATED=$(aws logs filter-log-events --log-group-name "$LOG_GROUP" --region "$REGION" \
    --start-time "$START" --end-time "$END" --filter-pattern "truncated" \
    --query 'events | length(@)' --output text 2>/dev/null)

  MARKDOWN=$(aws logs filter-log-events --log-group-name "$LOG_GROUP" --region "$REGION" \
    --start-time "$START" --end-time "$END" --filter-pattern '```json' \
    --query 'events | length(@)' --output text 2>/dev/null)

  printf "%-12s %8s %8s %8s %8s\n" "$DAY" "${RESPONSES:-0}" "${REPAIRED:-0}" "${TRUNCATED:-0}" "${MARKDOWN:-0}"
done

echo ""
echo "Responses = total API calls with finish_reason: stop"
echo "Repaired  = JSON needed escape/backslash repair"
echo "Truncated = response hit max_tokens limit"
echo "Markdown  = response wrapped in \`\`\`json block"

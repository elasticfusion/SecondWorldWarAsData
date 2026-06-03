#!/bin/bash
# Monitor all pipeline log groups concurrently.
# Usage: bash scripts/monitor_logs.sh [SINCE]

REGION="us-east-1"
SINCE="${1:-5m}"

cleanup() { kill "$(jobs -p)" 2>/dev/null; exit; }
trap cleanup INT TERM

# Format JSON log lines for readability, pass through non-JSON as-is
fmt() {
  local color="$1"
  local label="$2"
  while IFS= read -r line; do
    # Strip the CloudWatch timestamp prefix (everything before the JSON/message)
    msg="${line#* }"
    if echo "$msg" | jq -e . >/dev/null 2>&1; then
      ts=$(echo "$msg" | jq -r '.timestamp // empty' 2>/dev/null | cut -c12-19)
      level=$(echo "$msg" | jq -r '.level // empty' 2>/dev/null)
      text=$(echo "$msg" | jq -r '.message // empty' 2>/dev/null)
      phase=$(echo "$msg" | jq -r '.phase // empty' 2>/dev/null)
      printf "${color}[${label}]${NC} %s %-5s %s %s\n" "${ts:-??:??:??}" "${level}" "${phase:+[$phase] }" "$text"
    else
      printf "${color}[${label}]${NC} %s\n" "$line"
    fi
  done
}

NC=$'\033[0m'

echo "Monitoring: ECS POLLER NAT TRIGGER OPENSERP (since ${SINCE}, Ctrl+C to stop)"
echo "---"

while true; do
  aws logs tail "/ecs/dev-wwii-pipeline" --region "$REGION" --since "$SINCE" 2>/dev/null | fmt $'\033[32m' "ECS"
  aws logs tail "/aws/lambda/dev-wwii-batch-poller" --region "$REGION" --since "$SINCE" 2>/dev/null | fmt $'\033[36m' "POLLER"
  aws logs tail "/aws/lambda/dev-wwii-nat-manager" --region "$REGION" --since "$SINCE" 2>/dev/null | fmt $'\033[33m' "NAT"
  aws logs tail "/aws/lambda/dev-wwii-trigger" --region "$REGION" --since "$SINCE" 2>/dev/null | fmt $'\033[35m' "TRIGGER"
  aws logs tail "/ecs/dev-wwii-openserp" --region "$REGION" --since "$SINCE" 2>/dev/null | fmt $'\033[34m' "SERP"
  SINCE="10s"
  sleep 10
done

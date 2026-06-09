#!/bin/bash
# Monitor all pipeline log groups.
# Usage: bash scripts/monitor_logs.sh [SINCE]
#        bash scripts/monitor_logs.sh --since 2m

REGION="${AWS_DEFAULT_REGION:-us-east-1}"
ENV="${ENV_NAME:-dev}"
SINCE="${1:-5m}"
if [ "$1" = "--since" ] && [ -n "$2" ]; then
    SINCE="$2"
fi

cleanup() { kill $(jobs -p) 2>/dev/null; exit; }
trap cleanup INT TERM

# Format JSON log lines for readability
fmt() {
  local color="$1"
  local label="$2"
  while IFS= read -r line; do
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
CLUSTER="${ENV}-wwii-pipeline"

echo "Monitoring pipeline logs (env=${ENV}, since=${SINCE}, Ctrl+C to stop)"
echo "---"

while true; do
  # Show running tasks
  TASKS=$(aws ecs list-tasks --cluster "$CLUSTER" --region "$REGION" --query "taskArns[]" --output text 2>/dev/null | tr '\t' '\n' | sort -u)
  if [ -n "$TASKS" ] && [ "$TASKS" != "None" ]; then
    for task in $TASKS; do
      [ -z "$task" ] && continue
      info=$(aws ecs describe-tasks --cluster "$CLUSTER" --tasks "$task" --region "$REGION" \
        --query "tasks[0].[group,lastStatus,startedAt]" --output text 2>/dev/null)
      echo "$info" | grep -qi openserp && continue
      printf "\033[1m[TASK]\033[0m %s\n" "$info"
    done
  fi

  # Tail all log groups
  aws logs tail "/ecs/${ENV}-wwii-pipeline" --region "$REGION" --since "$SINCE" 2>/dev/null | fmt $'\033[32m' "ECS"
  aws logs tail "/aws/lambda/${ENV}-wwii-batch-poller" --region "$REGION" --since "$SINCE" 2>/dev/null | fmt $'\033[36m' "POLLER"
  aws logs tail "/aws/lambda/${ENV}-wwii-nat-manager" --region "$REGION" --since "$SINCE" 2>/dev/null | fmt $'\033[33m' "NAT"
  aws logs tail "/aws/lambda/${ENV}-wwii-trigger" --region "$REGION" --since "$SINCE" 2>/dev/null | fmt $'\033[35m' "TRIGGER"
  aws logs tail "/aws/lambda/${ENV}-wwii-openserp-manager" --region "$REGION" --since "$SINCE" 2>/dev/null | fmt $'\033[34m' "SERP-MGR"
  aws logs tail "/aws/lambda/${ENV}-wwii-dedup-ui" --region "$REGION" --since "$SINCE" 2>/dev/null | fmt $'\033[31m' "DEDUP"
  SINCE="10s"
  sleep 10
done

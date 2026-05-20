#!/bin/bash
# Monitor all pipeline log groups concurrently.
# Usage: bash scripts/monitor_logs.sh [SINCE]

REGION="us-east-1"
SINCE="${1:-5m}"

cleanup() { kill "$(jobs -p)" 2>/dev/null; exit; }
trap cleanup INT TERM

echo "Monitoring: ECS POLLER NAT (since ${SINCE}, Ctrl+C to stop)"
echo "---"

while true; do
  aws logs tail "/ecs/dev-wwii-pipeline" --region "$REGION" --since "$SINCE" 2>/dev/null | sed $'s/^/\033[32m[ECS]\033[0m /'
  aws logs tail "/aws/lambda/dev-wwii-batch-poller" --region "$REGION" --since "$SINCE" 2>/dev/null | sed $'s/^/\033[36m[POLLER]\033[0m /'
  aws logs tail "/aws/lambda/dev-wwii-nat-manager" --region "$REGION" --since "$SINCE" 2>/dev/null | sed $'s/^/\033[33m[NAT]\033[0m /'
  SINCE="10s"
  sleep 10
done

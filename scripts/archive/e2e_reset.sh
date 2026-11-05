#!/bin/bash
# e2e_reset.sh — reliable one-command clean slate for an end-to-end test run.
#
# WHY: a clean E2E must start from a verified-idle, residue-free state. In dev,
# aborted runs leave behind: parked content in DynamoDB (pending#*), the title
# registry (title#*), phase manifests (manifest#* + s3://.../manifests/), stale
# per-title output, lingering ECS tasks, and a running NAT. On top of that, two
# SCHEDULED reconcile backstops (stale-lock-check, batch-poller) re-inject parked
# work on a timer and race any manual reset. This script does the whole dance
# atomically and idempotently, with the backstops PAUSED while it works.
#
# Usage:
#   bash scripts/e2e_reset.sh [--env dev] [--region us-east-1]
#        [--wipe-title TITLE]...   # also delete content+output for these titles
#        [--keep-backstops-off]    # leave the reconcile backstops DISABLED (so a
#                                  #   subsequent single upload runs without the
#                                  #   timer re-injecting). Re-enable later with
#                                  #   --enable-backstops.
#        [--enable-backstops]      # just re-enable the backstops and exit.
#
# Requires AWS creds in the environment (e.g.
#   eval "$(AWS_PROFILE=wwii aws configure export-credentials --profile wwii --format env)"; unset AWS_PROFILE
# ).
set -euo pipefail

ENV="dev"
REGION="us-east-1"
WIPE_TITLES=()
KEEP_OFF=0
ENABLE_ONLY=0

while [ $# -gt 0 ]; do
  case "$1" in
    --env) ENV="$2"; shift 2 ;;
    --region) REGION="$2"; shift 2 ;;
    --wipe-title) WIPE_TITLES+=("$2"); shift 2 ;;
    --keep-backstops-off) KEEP_OFF=1; shift ;;
    --enable-backstops) ENABLE_ONLY=1; shift ;;
    *) echo "Unknown arg: $1" >&2; exit 2 ;;
  esac
done

CLUSTER="${ENV}-wwii-pipeline"
TABLE="${ENV}-wwii-api-cache"
BUCKET="${ENV}-wwii-data-pipeline"
OPENSERP_SVC="${ENV}-wwii-openserp"
NAT_FN="${ENV}-wwii-nat-manager"
# Scheduled rules that re-inject parked work (the backstops that race a reset).
BACKSTOPS=("${ENV}-wwii-stale-lock-check" "${ENV}-wwii-batch-poller-schedule")

_rule_exists() { aws events describe-rule --name "$1" --region "$REGION" >/dev/null 2>&1; }

enable_backstops() {
  for r in "${BACKSTOPS[@]}"; do
    _rule_exists "$r" && aws events enable-rule --name "$r" --region "$REGION" 2>/dev/null \
      && echo "  re-enabled $r" || true
  done
}

disable_backstops() {
  for r in "${BACKSTOPS[@]}"; do
    _rule_exists "$r" && aws events disable-rule --name "$r" --region "$REGION" 2>/dev/null \
      && echo "  disabled $r" || true
  done
}

if [ "$ENABLE_ONLY" = "1" ]; then
  echo "=== re-enabling reconcile backstops only ==="
  enable_backstops
  exit 0
fi

echo "=== e2e_reset: env=$ENV region=$REGION ==="

echo "[1/6] Pausing reconcile backstops (so the timer can't re-inject during reset)"
disable_backstops

echo "[2/6] Stopping pipeline tasks + scaling OpenSERP to 0"
# Scale the OpenSERP service down first so it isn't counted as active work.
aws ecs update-service --cluster "$CLUSTER" --service "$OPENSERP_SVC" \
  --desired-count 0 --region "$REGION" >/dev/null 2>&1 || true
for arn in $(aws ecs list-tasks --cluster "$CLUSTER" --region "$REGION" \
             --query 'taskArns[]' --output text 2>/dev/null); do
  grp=$(aws ecs describe-tasks --cluster "$CLUSTER" --tasks "$arn" --region "$REGION" \
        --query 'tasks[0].group' --output text 2>/dev/null || echo "")
  # Only stop one-shot pipeline tasks (family:*); leave service tasks to the scale-down.
  if [[ "$grp" == family:* ]]; then
    aws ecs stop-task --cluster "$CLUSTER" --task "$arn" --reason "e2e_reset" \
      --region "$REGION" >/dev/null 2>&1 && echo "  stopped $grp" || true
  fi
done

echo "[3/6] Deleting NAT gateway (nat-manager, demand-checked)"
aws lambda invoke --function-name "$NAT_FN" --payload '{"action": "delete"}' \
  --cli-binary-format raw-in-base64-out --region "$REGION" /tmp/e2e_reset_nat.json \
  >/dev/null 2>&1 && cat /tmp/e2e_reset_nat.json 2>/dev/null | head -c 200 && echo || true

echo "[4/6] Sweeping DynamoDB run-state (title#, pending#, manifest#) — paginated"
# The AWS CLI auto-paginates a scan; --output text yields ALL matching keys across
# pages (one per line via the newline-join on the projected single attribute).
mapfile -t _keys < <(aws dynamodb scan --table-name "$TABLE" \
  --filter-expression "begins_with(cache_key,:a) OR begins_with(cache_key,:b) OR begins_with(cache_key,:c)" \
  --expression-attribute-values '{":a":{"S":"title#"},":b":{"S":"pending#"},":c":{"S":"manifest#"}}' \
  --projection-expression "cache_key" \
  --region "$REGION" --query 'Items[].cache_key.S' --output text 2>/dev/null | tr '\t' '\n')
n=0
for k in "${_keys[@]}"; do
  [ -z "$k" ] && continue
  aws dynamodb delete-item --table-name "$TABLE" \
    --key "{\"cache_key\":{\"S\":\"$k\"}}" --region "$REGION" 2>/dev/null && n=$((n+1)) || true
done
echo "  deleted $n run-state item(s)"
# S3 incremental content manifest.
aws s3 rm "s3://$BUCKET/manifests/" --recursive --region "$REGION" >/dev/null 2>&1 || true

if [ "${#WIPE_TITLES[@]}" -gt 0 ]; then
  echo "[4b] Wiping content + output for titles: ${WIPE_TITLES[*]}"
  for t in "${WIPE_TITLES[@]}"; do
    aws s3 rm "s3://$BUCKET/contentrepository/$t/" --recursive --region "$REGION" >/dev/null 2>&1 || true
    aws s3 rm "s3://$BUCKET/output/content/$t/" --recursive --region "$REGION" >/dev/null 2>&1 || true
    echo "  wiped $t"
  done
fi

echo "[5/6] Clearing pending queue again (post-teardown, so re-enabled backstop finds nothing)"
for k in "pending#content" "pending#parsed" "manifest#phase2"; do
  aws dynamodb delete-item --table-name "$TABLE" \
    --key "{\"cache_key\":{\"S\":\"$k\"}}" --region "$REGION" 2>/dev/null || true
done

echo "[6/6] Verifying idle"
sleep 15
tasks=$(aws ecs list-tasks --cluster "$CLUSTER" --region "$REGION" --query 'length(taskArns)' --output text 2>/dev/null || echo "?")
nat=$(aws ec2 describe-nat-gateways --filter Name=state,Values=available --region "$REGION" \
      --query 'length(NatGateways)' --output text 2>/dev/null || echo "?")
pend=$(aws dynamodb scan --table-name "$TABLE" \
  --filter-expression "begins_with(cache_key,:a) OR begins_with(cache_key,:b)" \
  --expression-attribute-values '{":a":{"S":"title#"},":b":{"S":"pending#"}}' \
  --projection-expression "cache_key" \
  --region "$REGION" --query 'Items[].cache_key.S' --output text 2>/dev/null | wc -w | tr -d ' ')
echo "  tasks=$tasks  NAT=$nat  run-state-items=$pend"

if [ "$KEEP_OFF" = "1" ]; then
  echo "  (backstops left DISABLED — run your single upload now, then:"
  echo "     bash scripts/e2e_reset.sh --env $ENV --enable-backstops )"
else
  echo "Re-enabling reconcile backstops"
  enable_backstops
fi

if [ "$tasks" = "0" ] && { [ "$nat" = "0" ] || [ "$nat" = "None" ]; } && [ "$pend" = "0" ]; then
  echo "=== CLEAN: idle, no NAT, no run-state. Ready for a clean E2E upload. ==="
else
  echo "=== NOT fully clean (tasks=$tasks NAT=$nat items=$pend) — re-run or investigate. ===" >&2
  exit 1
fi

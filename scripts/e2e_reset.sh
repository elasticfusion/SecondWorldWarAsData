#!/bin/bash
# e2e_reset.sh — reliable one-command clean slate for an end-to-end test run.
#
# WHY: a clean E2E must start from a verified-idle, residue-free state. In dev,
# aborted runs leave behind: parked content in DynamoDB (pending#*), the title
# registry (title#*), phase manifests (manifest#* + s3://.../manifests/), stale
# per-title output, lingering ECS tasks, and a running NAT. On top of that, SCHEDULED
# reconcile backstops (stale-lock-check, batch-poller, openserp-worker-autoscaler)
# re-inject parked work on a timer and race any manual reset. This script does the
# whole dance idempotently, with the backstops PAUSED while it works.
#
# This file ORCHESTRATES (backstops, ECS tasks/services, NAT). Every DynamoDB / S3 / SQS
# sweep and the final verification live in scripts/e2e_reset_data.py (tested, batched,
# fail-closed: an AWS error or an unreadable resource is NEVER reported as clean).
#
# Usage:
#   bash scripts/e2e_reset.sh [--env dev] [--region us-east-1]
#        [--wipe-title TITLE]...   # also delete content+output for these titles
#        [--full]                  # FULL clean slate: in addition to run-state, empties the
#                                  #   derived-data S3 prefixes (output/ needs-review/ dedup/
#                                  #   person_photos/ — every object version), purges the SQS
#                                  #   work queues + DLQs, clears every per-entity table
#                                  #   (people/places/maps/...) and the lock#/batch_job#/
#                                  #   dedup_run#/reaper#/nat# run-state keys. PRESERVED: the
#                                  #   api-cache grok/fetch/search/url/place_coords/metrics rows
#                                  #   (re-paying Grok is the biggest cost), the human dedup
#                                  #   exclusion# rows, and contentrepository/ (re-uploaded
#                                  #   sources keep their source-url metadata) except
#                                  #   --wipe-title titles. Refused for envs other than
#                                  #   dev/test unless --allow-nondev-full.
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
FULL=0
ALLOW_NONDEV_FULL=0

_need_value() { [ $# -ge 2 ] && [[ "$2" != --* ]] || { echo "$1 needs a value" >&2; exit 2; }; }

while [ $# -gt 0 ]; do
  case "$1" in
    --env) _need_value "$@"; ENV="$2"; shift 2 ;;
    --region) _need_value "$@"; REGION="$2"; shift 2 ;;
    --wipe-title) _need_value "$@"; WIPE_TITLES+=("$2"); shift 2 ;;
    --full) FULL=1; shift ;;
    --allow-nondev-full) ALLOW_NONDEV_FULL=1; shift ;;
    --keep-backstops-off) KEEP_OFF=1; shift ;;
    --enable-backstops) ENABLE_ONLY=1; shift ;;
    *) echo "Unknown arg: $1" >&2; exit 2 ;;
  esac
done

[[ "$ENV" =~ ^[a-z0-9-]{1,20}$ ]] || { echo "invalid --env: $ENV" >&2; exit 2; }

CLUSTER="${ENV}-wwii-pipeline"
OPENSERP_SVC="${ENV}-wwii-openserp"
WORKER_SVC="${ENV}-wwii-openserp-worker"   # SQS consumer of openserp-work
NAT_FN="${ENV}-wwii-nat-manager"
# Scheduled rules that re-inject parked work or re-scale workers (they race a reset).
BACKSTOPS=("${ENV}-wwii-stale-lock-check" "${ENV}-wwii-batch-poller-schedule"
           "${ENV}-wwii-openserp-worker-autoscaler-schedule")

DATA_PY=(python3 "$(dirname "$0")/e2e_reset_data.py" --env "$ENV" --region "$REGION")
[ "$FULL" = "1" ] && DATA_PY+=(--full)
[ "$ALLOW_NONDEV_FULL" = "1" ] && DATA_PY+=(--allow-nondev-full)
for t in "${WIPE_TITLES[@]+"${WIPE_TITLES[@]}"}"; do DATA_PY+=(--wipe-title "$t"); done

# Refuse a bad invocation (e.g. --env prod --full) BEFORE touching anything.
# (--enable-backstops only re-enables schedules: no Python/boto3 needed, nothing destructive.)
if [ "$ENABLE_ONLY" != "1" ]; then
  "${DATA_PY[@]}" validate || { echo "refusing to run (see message above)" >&2; exit 2; }
fi

_rule_exists() { aws events describe-rule --name "$1" --region "$REGION" >/dev/null 2>&1; }

enable_backstops() {
  for r in "${BACKSTOPS[@]}"; do
    _rule_exists "$r" && aws events enable-rule --name "$r" --region "$REGION" 2>/dev/null \
      && echo "  re-enabled $r" || true
  done
}

disable_backstops() {
  for r in "${BACKSTOPS[@]}"; do
    # A rule that exists but cannot be disabled would race the sweep: stop, don't carry on.
    if _rule_exists "$r"; then
      aws events disable-rule --name "$r" --region "$REGION" \
        && echo "  disabled $r" || { echo "FAILED to disable $r" >&2; exit 1; }
    fi
  done
}

if [ "$ENABLE_ONLY" = "1" ]; then
  echo "=== re-enabling reconcile backstops only ==="
  enable_backstops
  exit 0
fi

echo "=== e2e_reset: env=$ENV region=$REGION full=$FULL account=$(aws sts get-caller-identity --query Account --output text --region "$REGION" 2>/dev/null || echo '?') ==="

echo "[1/6] Pausing reconcile backstops (so a timer can't re-inject or re-scale during reset)"
# If anything below aborts (error, Ctrl-C), put the backstops back unless asked to keep them off.
_restore_backstops_on_exit() {
  rc=$?
  if [ "$KEEP_OFF" != "1" ] && [ "${BACKSTOPS_RESTORED:-0}" != "1" ]; then
    echo "  (exit rc=$rc) restoring backstops" >&2
    enable_backstops || true
  fi
}
trap _restore_backstops_on_exit EXIT
disable_backstops

echo "[2/6] Stopping pipeline tasks + scaling OpenSERP and its queue worker to 0"
for svc in "$OPENSERP_SVC" "$WORKER_SVC"; do
  aws ecs update-service --cluster "$CLUSTER" --service "$svc" \
    --desired-count 0 --region "$REGION" >/dev/null 2>&1 && echo "  scaled $svc to 0" || true
done
for arn in $(aws ecs list-tasks --cluster "$CLUSTER" --region "$REGION" \
             --query 'taskArns[]' --output text 2>/dev/null); do
  grp=$(aws ecs describe-tasks --cluster "$CLUSTER" --tasks "$arn" --region "$REGION" \
        --query 'tasks[0].group' --output text 2>/dev/null || echo "")
  # Stop one-shot pipeline tasks (family:*); the services above were scaled to 0 and drain.
  if [[ "$grp" == family:* ]]; then
    aws ecs stop-task --cluster "$CLUSTER" --task "$arn" --reason "e2e_reset" \
      --region "$REGION" >/dev/null 2>&1 && echo "  stopped $grp" || true
  fi
done
# Let stopping tasks write their last bytes BEFORE the sweep, or they would re-create state
# (ECS stopTimeout defaults to 30s).
sleep 35

echo "[3/6] Sweeping run-state, derived data and queues (scripts/e2e_reset_data.py)"
SWEEP_RC=0
"${DATA_PY[@]}" sweep || SWEEP_RC=$?
# Stopped tasks may have flushed once more; a second pass (no queue purge) catches stragglers.
sleep 10
echo "  second pass (stragglers from stopped tasks):"
"${DATA_PY[@]}" sweep --skip-queues || SWEEP_RC=$?

echo "[4/6] Deleting NAT gateway (nat-manager is demand-checked on lease/batch/lock rows,"
echo "      which step 3 just cleared — so this runs AFTER the sweep)"
NAT_OUT=$(mktemp)
aws lambda invoke --function-name "$NAT_FN" --payload '{"action": "delete"}' \
  --cli-binary-format raw-in-base64-out --region "$REGION" "$NAT_OUT" \
  >/dev/null 2>&1 && head -c 300 "$NAT_OUT" 2>/dev/null && echo || true
rm -f "$NAT_OUT"

echo "[5/6] Verifying idle + clean (fail-closed: unreadable == not clean)"
sleep 15
tasks=$(aws ecs list-tasks --cluster "$CLUSTER" --region "$REGION" --query 'length(taskArns)' --output text 2>/dev/null || echo "?")
nat=$(aws ec2 describe-nat-gateways --filter Name=state,Values=available --region "$REGION" \
      --query 'length(NatGateways)' --output text 2>/dev/null || echo "?")
VERIFY_RC=0
"${DATA_PY[@]}" verify || VERIFY_RC=$?
echo "  tasks=$tasks  NAT=$nat  sweep_rc=$SWEEP_RC  verify_rc=$VERIFY_RC"

echo "[6/6] Backstops"
if [ "$KEEP_OFF" = "1" ]; then
  echo "  (backstops left DISABLED — run your single upload now, then:"
  echo "     bash scripts/e2e_reset.sh --env $ENV --enable-backstops )"
else
  echo "Re-enabling reconcile backstops (and restoring service desired counts is left to the"
  echo "autoscaler / next Phase 3 launch, which scale OpenSERP and the worker on demand)"
  enable_backstops
fi
BACKSTOPS_RESTORED=1

if [ "$tasks" = "0" ] && { [ "$nat" = "0" ] || [ "$nat" = "None" ]; } \
   && [ "$SWEEP_RC" = "0" ] && [ "$VERIFY_RC" = "0" ]; then
  echo "=== CLEAN: idle, no NAT, no run-state$( [ "$FULL" = "1" ] && echo ', derived data and queues empty'). Ready for a clean E2E upload. ==="
else
  echo "=== NOT fully clean (tasks=$tasks NAT=$nat sweep_rc=$SWEEP_RC verify_rc=$VERIFY_RC) — re-run or investigate. ===" >&2
  exit 1
fi

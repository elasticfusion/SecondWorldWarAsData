#!/usr/bin/env bash
# Monitor the Patton epub through the pipeline; append status to a log every run.
# Self-removes its own crontab entry once the doc reaches a terminal state.
# Scheduled every 20 min (see install below). NOTE: this logs to a file — it does
# not (cannot) push into the chat session.
set -uo pipefail

REPO="/home/dchristian/projects/SecondWorldWarAsData"
LOG="$REPO/logs/epub_monitor.log"
BOOK="Patton_at_the_Battle_of_the_Bul_-_Leo_Barron"
REGION="us-east-1"
mkdir -p "$(dirname "$LOG")"

# Load wwii creds (same pattern used interactively).
eval "$(AWS_PROFILE=wwii aws configure export-credentials --profile wwii --format env 2>/dev/null)"
unset AWS_PROFILE
export AWS_DEFAULT_REGION="$REGION"

ts() { date -u +%Y-%m-%dT%H:%M:%SZ; }

doc=$(aws dynamodb get-item --table-name dev-wwii-api-cache --region "$REGION" \
  --key "{\"cache_key\":{\"S\":\"doc#$BOOK\"}}" \
  --query 'Item.[status.S,next_phase.S]' --output text 2>/dev/null | tr '\t' '/')
[ -z "$doc" ] && doc="(no-doc-record)"

events=$(aws s3 ls "s3://dev-wwii-data-pipeline/output/content/$BOOK/" --recursive --region "$REGION" 2>/dev/null | grep -c 'event\.json')
tasks=$(aws ecs list-tasks --cluster dev-wwii-pipeline --desired-status RUNNING --region "$REGION" --query 'length(taskArns)' --output text 2>/dev/null)
nat=$(aws ec2 describe-nat-gateways --region "$REGION" --filter 'Name=state,Values=available' --query 'length(NatGateways)' --output text 2>/dev/null)

echo "$(ts) doc=$doc events=$events/72 running_tasks=$tasks nat=$nat" >> "$LOG"

# Terminal? -> record + notify locally + remove this cron entry so it stops.
case "$doc" in
  done*|failed*|needs-review*)
    echo "$(ts) TERMINAL ($doc) — notifying + removing epub-monitor cron entry" >> "$LOG"
    # Local desktop popup (backstop to the pipeline's SNS email+Slack).
    if command -v notify-send >/dev/null 2>&1; then
      DISPLAY="${DISPLAY:-:0}" notify-send -u critical \
        "WWII epub pipeline: $doc" \
        "Patton epub reached '$doc' (events=$events/72). See email/Slack for details." \
        >/dev/null 2>&1 || true
    fi
    printf '\a' 2>/dev/null || true  # terminal bell
    crontab -l 2>/dev/null | grep -v 'epub_monitor.sh' | crontab - 2>/dev/null
    ;;
esac

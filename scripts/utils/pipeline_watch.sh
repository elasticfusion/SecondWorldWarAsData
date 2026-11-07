#!/bin/bash
# pipeline_watch.sh — one-screen view of a running (dev) pipeline: ECS tasks, CloudWatch alarms,
# S3 output counts, and the notable log lines from EVERY pipeline log group (the ECS tasks and all
# {env}-wwii-* Lambdas), merged into one time-ordered stream.
#
# Why: during an E2E run the signal is spread across ~15 log groups (trigger, dispatcher, NAT,
# batch-poller, dedup-gate, ... plus /ecs/{env}-wwii-pipeline). Watching only the ECS log misses
# launches, gate decisions and Lambda-side failures.
#
# Dedup: shows each duplicate_report.json in S3 (group count, upload time), the related-groups
# report, dedup/review_status.json, and dedup log lines (detectors, detector_failed, gate decision).
#
# Usage:
#   bash scripts/utils/pipeline_watch.sh                 # last 15 min, once
#   bash scripts/utils/pipeline_watch.sh -m 60 -n 100    # last 60 min, 100 lines
#   bash scripts/utils/pipeline_watch.sh -f              # follow: refresh every 60s (Ctrl-C to stop)
#   bash scripts/utils/pipeline_watch.sh -e              # errors/warnings only
#   bash scripts/utils/pipeline_watch.sh -b USA-E-XChannel-8   # only lines mentioning this book
#
# Options:
#   -m MIN    look back MIN minutes (default 15)
#   -n N      show the last N log lines (default 40)
#   -f        follow mode (refresh every -i seconds)
#   -i SEC    follow interval (default 60)
#   -e        errors/warnings only (status=error|warn, ERROR, Traceback, BLOCKED, AccessDenied)
#   -b BOOK   filter log lines to those mentioning BOOK
#   -E ENV    environment (default dev)
#   -p PROF   AWS profile (default wwii; set -p '' to use ambient credentials)
#
# Read-only: it only lists/describes/filters; it never changes AWS state.

set -uo pipefail

MIN=15; LINES=40; FOLLOW=0; INTERVAL=60; ERRORS_ONLY=0; BOOK=""; ENV="dev"; PROFILE="wwii"
REGION="us-east-1"
while getopts "m:n:fi:eb:E:p:h" opt; do
  case "$opt" in
    m) MIN="$OPTARG" ;; n) LINES="$OPTARG" ;; f) FOLLOW=1 ;; i) INTERVAL="$OPTARG" ;;
    e) ERRORS_ONLY=1 ;; b) BOOK="$OPTARG" ;; E) ENV="$OPTARG" ;; p) PROFILE="$OPTARG" ;;
    h|*) sed -n '2,30p' "$0"; exit 0 ;;
  esac
done

# Stale exported SSO env credentials override the profile and fail with "refreshed credentials
# are still expired"; drop them when a profile is used.
aws_() {
  if [ -n "$PROFILE" ]; then
    env -u AWS_ACCESS_KEY_ID -u AWS_SECRET_ACCESS_KEY -u AWS_SESSION_TOKEN \
        -u AWS_CREDENTIAL_EXPIRATION timeout 60 aws --profile "$PROFILE" --region "$REGION" "$@"
  else
    timeout 60 aws --region "$REGION" "$@"
  fi
}

CLUSTER="${ENV}-wwii-pipeline"
BUCKET="${ENV}-wwii-data-pipeline"

snapshot() {
  local start=$(( ($(date +%s) - MIN * 60) * 1000 ))
  echo "================ $(date -u +%FT%TZ)  env=$ENV  window=${MIN}m ================"

  if ! aws_ sts get-caller-identity >/dev/null 2>&1; then
    echo "✗ AWS credentials not usable (try: aws sso login --sso-session wwii)"; return 1
  fi

  echo "-- ECS tasks (running + recently stopped)"
  local any=0
  for st in RUNNING STOPPED; do
    for a in $(aws_ ecs list-tasks --cluster "$CLUSTER" --desired-status "$st" --query 'taskArns[]' --output text); do
      [ "$a" = "None" ] && continue
      any=1
      aws_ ecs describe-tasks --cluster "$CLUSTER" --tasks "$a" \
        --query 'tasks[0].[group,lastStatus,startedAt,stoppedAt,containers[0].exitCode,stoppedReason]' --output text \
        | awk -F'\t' '{ printf "   %-34s %-14s start=%-25s stop=%-25s exit=%s %s\n", $1,$2,$3,$4,$5,($6=="None"?"":$6) }'
    done
  done
  [ "$any" = 0 ] && echo "   (none)"

  echo "-- CloudWatch alarms in ALARM: $(aws_ cloudwatch describe-alarms --state-value ALARM --query 'MetricAlarms[].AlarmName' --output text | tr '\t' ' ' | sed 's/^$/(none)/')"

  echo "-- S3 objects by prefix (s3://$BUCKET)"
  aws_ s3 ls "s3://$BUCKET/" --recursive 2>/dev/null | awk '{print $4}' \
    | awk -F/ '{ p=$1; if ($1=="output" && NF>2) p=$1"/"$2; print p }' | sort | uniq -c \
    | awk '{ printf "   %6d  %s\n", $1, $2 }'

  echo "-- Dedup (reports in S3 + gate/review status)"
  local sub rep when
  for sub in people people_groups places equipment; do
    when=$(aws_ s3 ls "s3://$BUCKET/output/$sub/duplicate_report.json" 2>/dev/null | awk '{print $1" "$2}')
    rep=$(aws_ s3 cp "s3://$BUCKET/output/$sub/duplicate_report.json" - 2>/dev/null \
      | python3 -c 'import json,sys
try:
    d=json.load(sys.stdin); print("groups=%s" % d.get("duplicate_groups", len(d.get("duplicates", []))))
except Exception as e:
    print("UNREADABLE (%s)" % type(e).__name__)' 2>/dev/null)
    printf "   %-14s %s\n" "$sub" "${when:+$rep  (uploaded $when)}${when:-no report yet}"
  done
  when=$(aws_ s3 ls "s3://$BUCKET/output/people_groups/related_groups_report.json" 2>/dev/null | awk '{print $1" "$2}')
  rep=$(aws_ s3 cp "s3://$BUCKET/output/people_groups/related_groups_report.json" - 2>/dev/null \
    | python3 -c 'import json,sys
try:
    d=json.load(sys.stdin); print("clusters=%s" % d.get("related_clusters", "?"))
except Exception as e:
    print("UNREADABLE (%s)" % type(e).__name__)' 2>/dev/null)
  printf "   %-14s %s\n" "related_groups" "${when:+$rep  (uploaded $when)}${when:-no report yet}"
  printf "   %-14s %s\n" "review_status" "$(aws_ s3 cp "s3://$BUCKET/dedup/review_status.json" - 2>/dev/null | tr -d '\n' | cut -c1-160 || true)"

  echo "-- Log lines (last $LINES)"
  local pat
  if [ "$ERRORS_ONLY" = 1 ]; then
    pat='?"status=error" ?"status=warn" ?"ERROR" ?"Traceback" ?"BLOCKED" ?"AccessDenied" ?"exited with code"'
  else
    pat='?"OBSERV" ?"Phase complete" ?"Phase started" ?"Launching" ?"Running dedup" ?"Dedup" ?"dedup report" ?"ERROR" ?"Traceback" ?"BLOCKED" ?"AccessDenied"'
  fi
  local groups
  groups="/ecs/${ENV}-wwii-pipeline $(aws_ logs describe-log-groups --log-group-name-prefix "/aws/lambda/${ENV}-wwii" --query 'logGroups[].logGroupName' --output text)"
  for g in $groups; do
    aws_ logs filter-log-events --log-group-name "$g" --start-time "$start" --filter-pattern "$pat" --output json 2>/dev/null \
      | sed "1s|^|{\"_group\": \"$g\", \"_body\": |; \$s|\$|}|"
  done | python3 -c '
import datetime, json, re, sys
book, limit = sys.argv[1], int(sys.argv[2])
dec, buf, i, rows = json.JSONDecoder(), sys.stdin.read(), 0, []
NOISE = re.compile(r"heartbeat|subscriber_ok|OBSERV autoscale|nat\.(lease|check)|av\.gate_enabled")
while i < len(buf):
    while i < len(buf) and buf[i].isspace():
        i += 1
    if i >= len(buf):
        break
    try:
        obj, i = dec.raw_decode(buf, i)
    except ValueError:
        break
    group = obj.get("_group", "?").replace("/aws/lambda/", "").replace("/ecs/", "ecs:")
    for e in (obj.get("_body") or {}).get("events", []):
        raw = e["message"]
        try:  # ECS tasks log structured JSON; Lambdas log tab-separated text
            m = json.loads(raw).get("message", raw)
        except (ValueError, AttributeError):
            m = " ".join(raw.split("\t")[3:]).strip() or raw.strip()
        if NOISE.search(m) or (book and book not in m):
            continue
        src = e["logStreamName"].split("/")[0] if group.startswith("ecs:") else group
        t = datetime.datetime.fromtimestamp(e["timestamp"] / 1000, datetime.UTC).strftime("%H:%M:%S")
        rows.append((t, src[:22], m.replace("\\n", " ")[:220]))
rows.sort()
for t, src, m in rows[-limit:]:
    flag = "!" if re.search(r"status=error|ERROR|Traceback|BLOCKED|AccessDenied", m) else ("~" if "status=warn" in m else " ")
    print(f"{flag} {t} {src:<22} {m}")
if not rows:
    print("   (no matching lines in window)")
' "$BOOK" "$LINES"
}

if [ "$FOLLOW" = 1 ]; then
  while true; do clear 2>/dev/null || true; snapshot; sleep "$INTERVAL"; done
else
  snapshot
  echo "(single snapshot — the pipeline keeps running; use -f to refresh every ${INTERVAL}s)"
fi

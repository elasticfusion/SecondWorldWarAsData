#!/usr/bin/env bash
# Operator-requested: stop NAT after the running pipeline tasks complete.
# Waits for all non-openserp pipeline tasks to drain, then invokes nat_manager delete.
set -u
LOG=/tmp/nat_teardown.log
echo "$(date -u +%FT%TZ) watcher start" >> "$LOG"
export AWS_DEFAULT_REGION=us-east-1
creds() { eval "$(AWS_PROFILE=wwii aws configure export-credentials --profile wwii --format env 2>/dev/null)"; unset AWS_PROFILE; }
for i in $(seq 1 180); do   # up to ~6h
  creds
  work=0
  for t in $(aws ecs list-tasks --cluster dev-wwii-pipeline --desired-status RUNNING --region us-east-1 --query 'taskArns' --output text 2>/dev/null | tr '\t' '\n'); do
    [ -z "$t" ] && continue
    g=$(aws ecs describe-tasks --cluster dev-wwii-pipeline --tasks "$t" --region us-east-1 --query 'tasks[0].group' --output text 2>/dev/null)
    case "$g" in *openserp*) :;; *) work=$((work+1));; esac
  done
  echo "$(date -u +%FT%TZ) work_tasks=$work" >> "$LOG"
  if [ "$work" = "0" ]; then
    echo "$(date -u +%FT%TZ) drained -> invoking nat_manager delete" >> "$LOG"
    aws lambda invoke --function-name dev-wwii-nat-manager --payload "$(printf '{"action":"delete","force":true}' | base64)" --region us-east-1 /tmp/nat_del_resp.json >> "$LOG" 2>&1
    cat /tmp/nat_del_resp.json >> "$LOG" 2>&1
    echo "" >> "$LOG"
    # verify
    sleep 20; creds
    echo "$(date -u +%FT%TZ) NAT available count=$(aws ec2 describe-nat-gateways --region us-east-1 --filter 'Name=state,Values=available,pending' --query 'length(NatGateways)' --output text 2>/dev/null)" >> "$LOG"
    break
  fi
  sleep 120
done
echo "$(date -u +%FT%TZ) watcher done" >> "$LOG"

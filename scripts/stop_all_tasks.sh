#!/bin/bash
REGION="us-east-1"
CLUSTER="dev-wwii-pipeline"
QUIET_SECONDS=180
last_found=$(date +%s)

aws ecs update-service --cluster $CLUSTER --service dev-wwii-openserp --desired-count 0 --region $REGION --no-cli-pager > /dev/null && echo "OpenSERP scaled to 0"

while true; do
    tasks=$(aws ecs list-tasks --cluster $CLUSTER --region $REGION --desired-status RUNNING --query "taskArns[]" --output text)
    if [ -n "$tasks" ] && [ "$tasks" != "None" ]; then
        last_found=$(date +%s)
        for task in $tasks; do
            aws ecs stop-task --cluster $CLUSTER --task "$task" --region $REGION --no-cli-pager > /dev/null
            echo "$(date +%H:%M:%S) Stopped: ${task##*/}"
        done
    fi

    elapsed=$(( $(date +%s) - last_found ))
    if [ $elapsed -ge $QUIET_SECONDS ]; then
        echo "No tasks for 3 minutes. Done."
        break
    fi
    sleep 30
done

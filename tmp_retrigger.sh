#!/bin/bash
set -e
REGION="us-east-1"
CLUSTER="dev-wwii-pipeline"

echo "=== 1. Stopping pipeline tasks (keeping OpenSERP) ==="
for task in $(aws ecs list-tasks --cluster $CLUSTER --region $REGION --query "taskArns[]" --output text); do
    group=$(aws ecs describe-tasks --cluster $CLUSTER --tasks "$task" --region $REGION --query "tasks[0].group" --output text 2>/dev/null)
    if echo "$group" | grep -q openserp; then
        echo "  Keeping OpenSERP: ${task##*/}"
    else
        aws ecs stop-task --cluster $CLUSTER --task "$task" --region $REGION --no-cli-pager > /dev/null
        echo "  Stopped: ${task##*/}"
    fi
done

echo "=== 2. Updating Lambdas ==="
bash scripts/update_lambdas.sh

echo "=== 3. Clearing locks ==="
for key in "lock#dev-wwii-phase1-parse" "lock#dev-wwii-phase2-extract" "lock#dev-wwii-phase3-enrich"; do
    aws dynamodb delete-item --table-name dev-wwii-api-cache --key "{\"cache_key\":{\"S\":\"$key\"}}" --region $REGION 2>/dev/null || true
done
echo "  Done"

echo "=== 4. Triggering TheSiegfriedLineCampaign (async) ==="
aws lambda invoke --function-name dev-wwii-trigger \
  --payload '{"source": "manual", "book": "TheSiegfriedLineCampaign"}' \
  --cli-binary-format raw-in-base64-out \
  --invocation-type Event \
  --region $REGION /tmp/out.json
echo "  Triggered (async)"

echo ""
echo "Monitor: aws logs tail /ecs/dev-wwii-pipeline --region $REGION --since 5m --follow"

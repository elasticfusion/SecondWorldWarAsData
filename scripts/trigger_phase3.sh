#!/bin/bash
REGION="us-east-1"
TABLE="dev-wwii-api-cache"
CLUSTER="dev-wwii-pipeline"
TOPIC="arn:aws:sns:us-east-1:REDACTED:dev-wwii-dedup-complete"
IDLE_RULE="dev-wwii-openserp-idle-monitor"

echo "=== 1. Clearing all locks ==="
for key in "lock#dev-wwii-phase1-parse" "lock#dev-wwii-phase2-extract" "lock#dev-wwii-phase3-enrich" "lock#nat-manager"; do
    aws dynamodb delete-item --table-name $TABLE --key "{\"cache_key\":{\"S\":\"$key\"}}" --region $REGION 2>/dev/null
done
echo "  Done"

echo ""
echo "=== 2. Disabling idle monitor ==="
aws events disable-rule --name $IDLE_RULE --region $REGION
echo "  Done"

echo ""
echo "=== 3. Triggering Phase 3 ==="
aws sns publish --topic-arn $TOPIC --message '{"dedup_complete":true}' --region $REGION --query "MessageId" --output text
echo "  Published"

echo ""
echo "=== 4. Waiting for ECS task to start ==="
for i in $(seq 1 30); do
    TASKS=$(aws ecs list-tasks --cluster $CLUSTER --region $REGION --query "taskArns" --output text)
    if [ -n "$TASKS" ] && [ "$TASKS" != "None" ]; then
        echo "  Task running: ${TASKS##*/}"
        echo ""
        echo "=== 5. Re-enabling idle monitor ==="
        aws events enable-rule --name $IDLE_RULE --region $REGION
        echo "  Done"
        echo ""
        echo "Monitor with:"
        echo "  aws logs tail /ecs/dev-wwii-pipeline --follow --region $REGION --since 2m"
        exit 0
    fi
    echo "  Waiting... ($i/30)"
    sleep 10
done

echo "  WARNING: No task started after 5 minutes"
echo "  Re-enabling idle monitor"
aws events enable-rule --name $IDLE_RULE --region $REGION
echo "  Check logs:"
echo "    aws logs tail /aws/lambda/dev-wwii-trigger --region $REGION --since 10m"
echo "    aws logs tail /aws/lambda/dev-wwii-nat-manager --region $REGION --since 10m"

#!/bin/bash
set -e
REGION="us-east-1"
CLUSTER="dev-wwii-pipeline"
BUCKET="dev-wwii-data-pipeline"
ACCOUNT="340339225515"

echo "=== 1. Stopping all tasks ==="
TASKS=$(aws ecs list-tasks --cluster $CLUSTER --region $REGION --query "taskArns[]" --output text 2>/dev/null)
if [ -n "$TASKS" ] && [ "$TASKS" != "None" ]; then
    for task in $TASKS; do
        aws ecs stop-task --cluster $CLUSTER --task $task --region $REGION --no-cli-pager > /dev/null
        echo "  Stopped: ${task##*/}"
    done
else
    echo "  No tasks running"
fi

echo "=== 2. Clearing locks ==="
for key in "lock#dev-wwii-phase1-parse" "lock#dev-wwii-phase2-extract" "lock#dev-wwii-phase3-enrich"; do
    aws dynamodb delete-item --table-name dev-wwii-api-cache --key "{\"cache_key\":{\"S\":\"$key\"}}" --region $REGION 2>/dev/null || true
done
echo "  Done"

echo "=== 3. Fixing S3 notification ==="
aws s3api put-bucket-notification-configuration --bucket $BUCKET --region $REGION --notification-configuration "{
  \"TopicConfigurations\": [
    {
      \"TopicArn\": \"arn:aws:sns:$REGION:$ACCOUNT:dev-wwii-content-uploaded\",
      \"Events\": [\"s3:ObjectCreated:*\"],
      \"Filter\": {\"Key\": {\"FilterRules\": [{\"Name\": \"Prefix\", \"Value\": \"contentrepository/\"}, {\"Name\": \"Suffix\", \"Value\": \".md\"}]}}
    },
    {
      \"TopicArn\": \"arn:aws:sns:$REGION:$ACCOUNT:dev-wwii-chapter-parsed\",
      \"Events\": [\"s3:ObjectCreated:*\"],
      \"Filter\": {\"Key\": {\"FilterRules\": [{\"Name\": \"Prefix\", \"Value\": \"output/\"}, {\"Name\": \"Suffix\", \"Value\": \"-parsed.json\"}]}}
    }
  ]
}"
echo "  Done"

echo "=== Done. Upload content to trigger: ==="
echo "aws s3 sync contentrepository/TheSiegfriedLineCampaign/ s3://$BUCKET/contentrepository/TheSiegfriedLineCampaign/ --region $REGION"
echo ""
echo "Then monitor with:"
echo "aws logs tail /ecs/dev-wwii-pipeline --region $REGION --since 5m --follow"

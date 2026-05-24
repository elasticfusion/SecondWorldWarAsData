#!/bin/bash
# Pipeline status: show queues, batch jobs, running tasks, and dedup state.
# Usage: bash scripts/pipeline_status.sh

REGION="us-east-1"
TABLE="dev-wwii-api-cache"
CLUSTER="dev-wwii-pipeline"
BUCKET="dev-wwii-data-pipeline"

echo "Pipeline Status ($(date -u '+%Y-%m-%d %H:%M UTC'))"
echo "═══════════════════════════════════════════════════"

echo ""
echo "Queues:"
CONTENT=$(aws dynamodb get-item --table-name $TABLE --key '{"cache_key":{"S":"pending#content"}}' --region $REGION --query 'Item.keys.L | length(@)' --output text 2>/dev/null)
PARSED=$(aws dynamodb get-item --table-name $TABLE --key '{"cache_key":{"S":"pending#parsed"}}' --region $REGION --query 'Item.keys.L | length(@)' --output text 2>/dev/null)
echo "  Content (Phase 1): ${CONTENT:-0} files"
echo "  Parsed (Phase 2):  ${PARSED:-0} files"

echo ""
echo "Batch Jobs:"
aws dynamodb scan --table-name $TABLE --filter-expression "begins_with(cache_key, :p)" \
  --expression-attribute-values '{":p":{"S":"batch_job#"}}' \
  --projection-expression "batch_id,#s,request_count,book" \
  --expression-attribute-names '{"#s":"status"}' \
  --region $REGION --query 'Items[*].[batch_id.S,status.S,request_count.N,book.S]' --output text 2>/dev/null | \
  while IFS=$'\t' read -r id status count book; do
    [ -z "$id" ] && continue
    echo "  ${id:0:12}  ${status:-?}  ${count:-?} reqs  ${book:-?}"
  done
[ $? -ne 0 ] && echo "  (none)"

echo ""
echo "Running Tasks:"
aws ecs list-tasks --cluster $CLUSTER --region $REGION --query 'taskArns[]' --output text 2>/dev/null | tr '\t' '\n' | while read -r arn; do
  [ -z "$arn" ] && continue
  TASK_ID="${arn##*/}"
  DEF=$(aws ecs describe-tasks --cluster $CLUSTER --tasks "$TASK_ID" --region $REGION --query 'tasks[0].taskDefinitionArn' --output text 2>/dev/null)
  FAMILY="${DEF##*/}"
  echo "  ${TASK_ID:0:12}  $FAMILY"
done
aws ecs list-tasks --cluster $CLUSTER --region $REGION --query 'length(taskArns)' --output text | grep -q "^0$" && echo "  (none)"

echo ""
echo "Dedup Review:"
STATUS=$(aws s3 cp s3://$BUCKET/dedup/review_status.json - --region $REGION 2>/dev/null)
if [ -n "$STATUS" ]; then
  COMPLETE=$(echo "$STATUS" | python3 -c "import sys,json; print(json.load(sys.stdin).get('complete','?'))" 2>/dev/null)
  echo "  Complete: $COMPLETE"
else
  echo "  (not set)"
fi

echo ""
echo "NAT Gateway:"
NAT=$(aws ec2 describe-nat-gateways --filter Name=state,Values=available --region $REGION --query 'NatGateways[0].NatGatewayId' --output text 2>/dev/null)
echo "  ${NAT:-DOWN}"

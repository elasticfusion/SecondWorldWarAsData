#!/bin/bash
set -e

REGION="us-east-1"
ACCOUNT="REDACTED"
CLUSTER="dev-wwii-pipeline"
ECR_REPO="$ACCOUNT.dkr.ecr.$REGION.amazonaws.com"
PIPELINE_IMAGE="$ECR_REPO/wwii-pipeline:latest"
OPENSERP_IMAGE="$ECR_REPO/wwii-openserp:latest"
TEMPLATE_BUCKET="wwii-pipeline-deploy"
ENV="dev"
EMAIL="dchristian@cirrusnine.com"

echo "=== 1. Stopping running tasks ==="
for task in $(aws ecs list-tasks --cluster $CLUSTER --region $REGION --query "taskArns[]" --output text 2>/dev/null); do
    aws ecs stop-task --cluster $CLUSTER --task $task --region $REGION --no-cli-pager > /dev/null
    echo "  Stopped: ${task##*/}"
done
echo "  Done"

echo ""
echo "=== 2. Clearing locks ==="
aws s3 rm s3://dev-wwii-data-pipeline/locks/ --recursive --region $REGION 2>/dev/null || true
# Clear DynamoDB locks
for key in "lock#dev-wwii-phase1-parse" "lock#dev-wwii-phase2-extract" "lock#dev-wwii-phase3-enrich"; do
    aws dynamodb delete-item --table-name dev-wwii-api-cache --key "{\"cache_key\":{\"S\":\"$key\"}}" --region $REGION 2>/dev/null || true
done
echo "  Done"

echo ""
echo "=== 3. Running QA checks ==="
cd "$(dirname "$0")/.."
source .venv/bin/activate
python3 -c "import ast; ast.parse(open('ecs_entrypoint.py').read()); ast.parse(open('src/utils/batch_api.py').read()); print('  Syntax OK')"
python3 -m pytest tests/unit/ -q 2>&1 | tail -1

echo ""
echo "=== 4. Building and pushing container ==="
aws ecr get-login-password --region $REGION | docker login --username AWS --password-stdin $ECR_REPO
docker build -t wwii-pipeline .
docker tag wwii-pipeline:latest $PIPELINE_IMAGE
docker push $PIPELINE_IMAGE
echo "  Pushed: $(aws ecr describe-images --repository-name wwii-pipeline --region $REGION --image-ids imageTag=latest --query 'imageDetails[0].imagePushedAt' --output text)"

echo ""
echo "=== 5. Deploying CloudFormation ==="
aws s3 sync cloudformation/ s3://$TEMPLATE_BUCKET/cloudformation/ --region $REGION
python3 scripts/deploy_aws.py deploy --env $ENV --region $REGION --template-bucket $TEMPLATE_BUCKET --pipeline-image $PIPELINE_IMAGE --openserp-image $OPENSERP_IMAGE --notification-email $EMAIL

echo ""
echo "=== 6. Updating Lambda code ==="
bash scripts/update_lambdas.sh

echo ""
echo "=== 7. Fixing auth ==="
aws lambda update-function-configuration --function-name dev-wwii-dedup-auth --environment "Variables={AUTH_TOKEN=admin:REDACTED}" --region $REGION --no-cli-pager > /dev/null
echo "  Auth updated"

echo ""
echo "=== 8. Verification ==="
echo "  Image: $(aws ecr describe-images --repository-name wwii-pipeline --region $REGION --image-ids imageTag=latest --query 'imageDetails[0].imagePushedAt' --output text)"
echo "  Tasks: $(aws ecs list-tasks --cluster $CLUSTER --region $REGION --query 'taskArns' --output text)"
echo "  Dedup: $(aws s3 cp s3://dev-wwii-data-pipeline/dedup/review_status.json - --region $REGION 2>/dev/null || echo 'not set')"
echo ""
echo "=== Deploy complete ==="
echo ""
echo "Monitor with:"
echo "  aws logs tail /ecs/dev-wwii-pipeline --follow --region $REGION --since 2m"
echo ""
echo "Analyze logs with:"
echo "  bash scripts/analyze_logs.sh"

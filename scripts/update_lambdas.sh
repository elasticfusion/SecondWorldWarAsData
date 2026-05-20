#!/bin/bash
# Update all Lambda functions with the latest code package.
# Usage: bash scripts/update_lambdas.sh [--env dev] [--region us-east-1] [--bucket wwii-pipeline-deploy]

set -e

ENV="${1:-dev}"
REGION="${2:-us-east-1}"
BUCKET="${3:-wwii-pipeline-deploy}"
PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"

cd "$PROJECT_DIR"

echo "=== Building Lambda package ==="
rm -rf lambda_package lambda-code.zip
pip install -r requirements-lambda.txt -t lambda_package/ -q
cp -r src/ lambda_handlers/ scripts/ config.yaml lambda_package/
cd lambda_package
zip -r ../lambda-code.zip . -x "*.pyc" "*__pycache__*" > /dev/null
cd "$PROJECT_DIR"
rm -rf lambda_package

echo "=== Uploading to S3 ==="
aws s3 cp lambda-code.zip "s3://${BUCKET}/lambda/code.zip" --region "$REGION"

echo "=== Updating Lambda functions ==="
# Only update Lambdas that use the S3 code package.
# Skip: dedup-auth (inline ZipFile), trigger (inline ZipFile), pipeline phases (now ECS).
for fn in dedup-gate dedup-ui openserp-manager nat-manager metrics batch-poller; do
  echo "  Updating ${ENV}-wwii-${fn}..."
  aws lambda update-function-code \
    --function-name "${ENV}-wwii-${fn}" \
    --s3-bucket "$BUCKET" \
    --s3-key lambda/code.zip \
    --region "$REGION" \
    --no-cli-pager > /dev/null
done

rm -f lambda-code.zip
echo "=== Done ==="

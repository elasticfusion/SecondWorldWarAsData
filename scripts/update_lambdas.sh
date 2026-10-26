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
# Pass 1: install everything (pure-Python deps resolve normally).
pip install -r requirements-lambda.txt -t lambda_package/ -q
# Pass 2: OVERLAY Lambda-runtime-correct binary wheels for packages with NATIVE extensions.
# The Lambda runtime is Linux x86_64 / CPython 3.12; a wheel built for the BUILD host (e.g. a
# different arch or glibc) fails at import time — this is what broke the validation backstop:
# jsonschema -> referencing -> rpds, where rpds ships a compiled .so and the wrong-platform wheel
# raised "ModuleNotFoundError: No module named 'rpds.rpds'" on every invocation. Force the
# manylinux2014 x86_64 / cp312 wheels so native extensions match the runtime.
pip install -r requirements-lambda.txt -t lambda_package/ -q \
  --platform manylinux2014_x86_64 --implementation cp --python-version 3.12 \
  --only-binary=:all: --upgrade
cp -r src/ lambda_handlers/ config.yaml lambda_package/
# Only include scripts that Lambda handlers import
mkdir -p lambda_package/scripts
for s in find_duplicate_people.py find_duplicate_places_v2.py find_duplicate_groups.py \
         find_duplicate_equipment.py find_related_groups.py reclassify_military_units.py \
         cleanup_indexes.py; do
  [ -f "scripts/$s" ] && cp "scripts/$s" lambda_package/scripts/
done
# Scan Lambda dependencies for vulnerabilities
if command -v trivy &>/dev/null; then
  trivy fs lambda_package/ --severity HIGH,CRITICAL --exit-code 0 2>&1 | tail -5
fi
cd lambda_package
zip -r ../lambda-code.zip . -x "*.pyc" "*__pycache__*" > /dev/null
cd "$PROJECT_DIR"
rm -rf lambda_package

echo "=== Uploading to S3 ==="
aws s3 cp lambda-code.zip "s3://${BUCKET}/lambda/code.zip" --region "$REGION"

echo "=== Updating Lambda functions ==="
# Compute the validation fingerprint (schemas + guard + stats catcher) so the s3-validation-backstop
# Lambda can advertise which validation code it is running — the deploy preflight compares this to
# repo-HEAD to catch a stale backstop.
VALIDATION_FP="$(bash scripts/validation_fingerprint.sh 2>/dev/null || echo unknown)"
# Only update Lambdas that use the S3 code package.
# Skip: dedup-auth (inline ZipFile), trigger (inline ZipFile), pipeline phases (now ECS).
for fn in dedup-gate dedup-ui openserp-manager nat-manager openserp-worker-autoscaler metrics batch-poller trigger ocr-merge slack-formatter mdreview-ui urlreview-ui s3-validation-backstop idle-backstop-reaper nat-teardown; do
  echo "  Updating ${ENV}-wwii-${fn}..."
  aws lambda update-function-code \
    --function-name "${ENV}-wwii-${fn}" \
    --s3-bucket "$BUCKET" \
    --s3-key lambda/code.zip \
    --region "$REGION" \
    --no-cli-pager > /dev/null
done

# Stamp the validation fingerprint onto the backstop Lambda's env (best-effort; the function may
# not exist yet on a first deploy — the CFN sets a default).
aws lambda get-function --function-name "${ENV}-wwii-s3-validation-backstop" --region "$REGION" \
  --no-cli-pager >/dev/null 2>&1 && \
aws lambda update-function-configuration \
  --function-name "${ENV}-wwii-s3-validation-backstop" \
  --environment "Variables={VALIDATION_FINGERPRINT=${VALIDATION_FP},ENV_NAME=${ENV},S3_BUCKET=${ENV}-wwii-data-pipeline,NOTIFICATION_TOPIC_ARN=arn:aws:sns:${REGION}:$(aws sts get-caller-identity --query Account --output text 2>/dev/null):${ENV}-wwii-phase2-complete}" \
  --region "$REGION" --no-cli-pager >/dev/null 2>&1 || true

rm -f lambda-code.zip
echo "=== Done ==="

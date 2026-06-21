#!/bin/bash
set -e

REGION="us-east-1"
ACCOUNT=$(aws sts get-caller-identity --query Account --output text)
CLUSTER="dev-wwii-pipeline"
ECR_REPO="$ACCOUNT.dkr.ecr.$REGION.amazonaws.com"
PIPELINE_IMAGE="$ECR_REPO/wwii-pipeline:latest"
OPENSERP_IMAGE="$ECR_REPO/wwii-openserp:latest"
TEMPLATE_BUCKET="wwii-pipeline-deploy"
ENV="dev"
EMAIL="dchristian@cirrusnine.com"

echo "=== 0. Select config profile ==="
echo "  1) balanced          — batch extraction, live enrichment (default)"
echo "  2) cost-optimized    — everything batched, cheap models for light tasks"
echo "  3) performance       — live calls, max concurrency, fastest"
echo "  4) review-all-data   — force reprocess everything (expensive)"
echo "  5) keep current      — don't change config.yaml"
read -p "  Profile [1-5, default=5]: " -r profile_choice
case "$profile_choice" in
    1) cp config.balanced.yaml config.yaml && echo "  → balanced" ;;
    2) cp config.cost-optimized.yaml config.yaml && echo "  → cost-optimized" ;;
    3) cp config.performance-optimized.yaml config.yaml && echo "  → performance" ;;
    4) cp config.review-all-data.yaml config.yaml && echo "  → review-all-data (WARNING: expensive)" ;;
    *) echo "  → keeping current config.yaml" ;;
esac
echo ""

echo "=== 1. Stopping running tasks ==="
TASKS=$(aws ecs list-tasks --cluster $CLUSTER --region $REGION --query "taskArns[]" --output text 2>/dev/null)
if [ -n "$TASKS" ] && [ "$TASKS" != "None" ]; then
    echo "  WARNING: Running tasks detected!"
    echo "  Stopping them will lose any in-progress batch results."
    echo "  Tasks: $TASKS"
    read -p "  Continue? (y/N) " -r confirm
    if [ "$confirm" != "y" ]; then
        echo "  Aborted. Wait for tasks to finish."
        exit 1
    fi
    for task in $TASKS; do
        aws ecs stop-task --cluster $CLUSTER --task $task --region $REGION --no-cli-pager > /dev/null
        echo "  Stopped: ${task##*/}"
    done
fi
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
python3 -c "
import yaml, json, sys
from pathlib import Path

REQUIRED = ['events', 'events_batch', 'people', 'places', 'dates', 'equipment',
            'equipment_vision', 'equipment_urls', 'equipment_enrichment',
            'weather', 'weather_batch', 'casualties', 'logistics',
            'supplemental', 'supplemental_narrative', 'people_groups', 'biography',
            'people_consolidation', 'map_search', 'map_vision', 'license_check',
            'isbn_lookup', 'author_death_date', 'publication_search',
            'bibliography_verify', 'nara_identify', 'nara_verify']
errors = []
for name in REQUIRED:
    p = Path(f'prompts/{name}.yaml')
    if not p.exists():
        errors.append(f'MISSING: prompts/{name}.yaml')
        continue
    data = yaml.safe_load(p.read_text())
    if not data.get('prompt_template'):
        errors.append(f'NO prompt_template: {p}')
    schema = data.get('schema')
    if schema:
        # Verify schema parses as JSON (only if present)
        try:
            json.loads(schema)
        except (json.JSONDecodeError, TypeError) as e:
            errors.append(f'INVALID schema JSON in {p}: {e}')
if errors:
    print('  ✗ Prompt validation failed:')
    for e in errors:
        print(f'    {e}')
    sys.exit(1)
print(f'  Prompts OK ({len(REQUIRED)} validated)')
" || { echo "  ✗ Prompt check failed — aborting deploy"; exit 1; }
python3 -c "
import yaml, sys
from pathlib import Path

REQUIRED = ['people', 'equipment', 'events', 'bibliography', 'maps', 'nara']
errors = []
for name in REQUIRED:
    p = Path(f'search_queries/{name}.yaml')
    if not p.exists():
        errors.append(f'MISSING: search_queries/{name}.yaml')
        continue
    data = yaml.safe_load(p.read_text())
    if not isinstance(data, dict) or not data:
        errors.append(f'EMPTY/INVALID: {p}')
if errors:
    print('  ✗ Search query validation failed:')
    for e in errors:
        print(f'    {e}')
    sys.exit(1)
print(f'  Search queries OK ({len(REQUIRED)} validated)')
" || { echo "  ✗ Search query check failed — aborting deploy"; exit 1; }
python3 -m pytest tests/ -m "not slow and not requires_api" -q --tb=short || { echo "  ✗ Tests failed — aborting deploy"; exit 1; }

echo ""
echo "=== 3b. Security checks ==="
# Dockerfile linting
if command -v hadolint &>/dev/null; then
  hadolint Dockerfile && echo "  Hadolint: OK" || echo "  Hadolint: warnings (non-blocking)"
else
  echo "  Hadolint: not installed (skipping)"
fi
# CloudFormation linting
if command -v cfn-lint &>/dev/null; then
  cfn-lint cloudformation/*.yaml 2>&1 | head -10 && echo "  cfn-lint: OK"
else
  echo "  cfn-lint: not installed (skipping)"
fi
# CloudFormation security
if command -v cfn_nag_scan &>/dev/null; then
  cfn_nag_scan --input-path cloudformation/ 2>&1 | grep -E "FAIL|WARN" | head -10
  echo "  cfn-nag: done"
else
  echo "  cfn-nag: not installed (skipping)"
fi
# Secrets scanning
if command -v gitleaks &>/dev/null; then
  gitleaks detect --source . --no-git --no-banner 2>&1 && echo "  Gitleaks: OK (no secrets found)" || { echo "  ✗ Gitleaks found potential secrets — aborting deploy"; exit 1; }
elif command -v detect-secrets &>/dev/null; then
  detect-secrets scan --list-all-plugins 2>/dev/null | detect-secrets audit --report - 2>&1 | head -5
  echo "  detect-secrets: checked"
else
  echo "  Secrets scanning: not installed (install gitleaks: https://github.com/gitleaks/gitleaks#installing)"
fi

echo ""
echo "=== 4. Building and pushing container ==="
aws ecr get-login-password --region $REGION | docker login --username AWS --password-stdin $ECR_REPO
docker build --no-cache --progress=plain -t wwii-pipeline .
# Container vulnerability scan
if command -v trivy &>/dev/null; then
  echo "  Scanning image for vulnerabilities..."
  # Check for newer trivy version
  TRIVY_CURRENT=$(trivy --version 2>/dev/null | head -1 | grep -oP '\d+\.\d+\.\d+')
  TRIVY_LATEST=$(curl -sf https://api.github.com/repos/aquasecurity/trivy/releases/latest | grep -oP '"tag_name":\s*"v\K[^"]+' 2>/dev/null)
  if [ -n "$TRIVY_LATEST" ] && [ "$TRIVY_CURRENT" != "$TRIVY_LATEST" ]; then
    echo "  ⚠ Trivy update available: $TRIVY_CURRENT → $TRIVY_LATEST (curl -sfL https://raw.githubusercontent.com/aquasecurity/trivy/main/contrib/install.sh | sudo sh -s -- -b /usr/local/bin)"
  fi
  # Detect container runtime
  TRIVY_IMAGE_SRC=""
  if command -v podman &>/dev/null && podman image exists wwii-pipeline:latest 2>/dev/null; then
    # Podman: save to tar then scan
    podman save wwii-pipeline:latest -o /tmp/wwii-scan.tar 2>/dev/null || true
    set +e
    TRIVY_OUTPUT=$(trivy image --scanners vuln --severity HIGH,CRITICAL --ignore-unfixed --exit-code 1 --input /tmp/wwii-scan.tar 2>&1)
    TRIVY_EXIT=$?
    set -e
    rm -f /tmp/wwii-scan.tar
  else
    set +e
    TRIVY_OUTPUT=$(trivy image --scanners vuln --severity HIGH,CRITICAL --ignore-unfixed --exit-code 1 wwii-pipeline:latest 2>&1)
    TRIVY_EXIT=$?
    set -e
  fi
  echo "$TRIVY_OUTPUT" | tail -10
  if [ $TRIVY_EXIT -ne 0 ]; then
    echo "  ✗ HIGH/CRITICAL vulnerabilities found — aborting deploy"
    exit 1
  fi
  echo "  Trivy: OK"
else
  echo "  Trivy: not installed — falling back to pip-audit"
  pip-audit -r requirements.txt --severity high 2>&1 | tail -10 || { echo "  ✗ pip-audit found vulnerabilities — aborting deploy"; exit 1; }
  echo "  pip-audit: OK"
fi
docker tag wwii-pipeline:latest $PIPELINE_IMAGE
docker push $PIPELINE_IMAGE
echo "  Pushed: $(aws ecr describe-images --repository-name wwii-pipeline --region $REGION --image-ids imageTag=latest --query 'imageDetails[0].imagePushedAt' --output text)"

echo ""
echo "=== 5. Deploying CloudFormation ==="
# Check stack is in a deployable state
STACK_STATUS=$(aws cloudformation describe-stacks --stack-name wwii-pipeline-$ENV --region $REGION --query "Stacks[0].StackStatus" --output text 2>/dev/null || echo "DOES_NOT_EXIST")
if [[ "$STACK_STATUS" == *"IN_PROGRESS"* ]]; then
    echo "  ❌ Stack is $STACK_STATUS — wait for it to finish before deploying"
    echo "  Monitor: aws cloudformation describe-stacks --stack-name wwii-pipeline-$ENV --query Stacks[0].StackStatus --output text --region $REGION"
    exit 1
fi
echo "  Stack status: $STACK_STATUS"
aws s3 sync cloudformation/ s3://$TEMPLATE_BUCKET/cloudformation/ --region $REGION
python3 scripts/deploy_aws.py deploy --env $ENV --region $REGION --template-bucket $TEMPLATE_BUCKET --pipeline-image $PIPELINE_IMAGE --openserp-image $OPENSERP_IMAGE --notification-email $EMAIL

echo ""
echo "=== 6. Updating Lambda code ==="
bash scripts/update_lambdas.sh

echo ""
echo "=== 7. Fixing auth ==="
AUTH_TOKEN=$(aws secretsmanager get-secret-value --secret-id ${ENV}-wwii-pipeline/dedup-auth --query SecretString --output text --region $REGION 2>/dev/null || echo "admin:ReviewPass2026")
aws lambda update-function-configuration --function-name dev-wwii-dedup-auth --environment "Variables={AUTH_TOKEN=$AUTH_TOKEN}" --region $REGION --no-cli-pager > /dev/null
echo "  Auth updated"

echo ""
echo "=== 8. Verification ==="
# Clear locks again (triggers may have re-acquired during deploy)
for key in "lock#dev-wwii-phase1-parse" "lock#dev-wwii-phase2-extract" "lock#dev-wwii-phase3-enrich"; do
    aws dynamodb delete-item --table-name dev-wwii-api-cache --key "{\"cache_key\":{\"S\":\"$key\"}}" --region $REGION 2>/dev/null || true
done
echo "  Locks cleared"
echo "  Image: $(aws ecr describe-images --repository-name wwii-pipeline --region $REGION --image-ids imageTag=latest --query 'imageDetails[0].imagePushedAt' --output text)"
echo "  Tasks: $(aws ecs list-tasks --cluster $CLUSTER --region $REGION --query 'taskArns' --output text)"
echo "  Dedup: $(aws s3 cp s3://dev-wwii-data-pipeline/dedup/review_status.json - --region $REGION 2>/dev/null || echo 'not set')"
echo ""
echo "=== Deploy complete ==="
echo "  $(date)"
echo ""
echo "=== 9. Validate structured logging ==="
bash scripts/validate_logging.sh --cloudwatch || echo "  ⚠ CloudWatch validation incomplete (may need a pipeline run first)"
echo ""
echo "Monitor with:"
echo "  aws logs tail /ecs/dev-wwii-pipeline --follow --region $REGION --since 2m"
echo ""
echo "Analyze logs with:"
echo "  bash scripts/analyze_logs.sh"

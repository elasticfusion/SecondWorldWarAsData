# Enabling AWS for GitHub Actions CI/CD

This guide sets up the AWS resources needed for the `deploy.yml` GitHub Actions workflow to build containers, deploy Lambdas, and update CloudFormation stacks.

---

## Prerequisites

- AWS CLI configured with admin access
- GitHub repository: `<your-org>/SecondWorldWarAsData`
- AWS Account ID: `340339225515`
- Region: `us-east-1`

---

## Option A: Deploy via CloudFormation (Recommended)

The OIDC provider and deploy role are built into `cloudformation/iam.yaml`. Just pass your GitHub org when deploying the stack:

```bash
aws cloudformation deploy \
  --stack-name wwii-pipeline-dev \
  --template-file cloudformation/main.yaml \
  --parameter-overrides \
    EnvironmentName=dev \
    TemplateBucket=wwii-pipeline-deploy \
    LambdaCodeBucket=wwii-pipeline-deploy \
    LambdaCodeKey=lambda/code.zip \
    GitHubOrg=YOUR_GITHUB_ORG \
    PipelineImageUri=340339225515.dkr.ecr.us-east-1.amazonaws.com/wwii-pipeline:latest \
  --capabilities CAPABILITY_NAMED_IAM
```

This creates:
- `AWS::IAM::OIDCProvider` for `token.actions.githubusercontent.com`
- `dev-wwii-github-deploy` IAM role with least-privilege deploy permissions
- Trust policy locked to `repo:<your-org>/SecondWorldWarAsData:ref:refs/heads/main`

After deploy, get the role ARN:

```bash
aws cloudformation describe-stacks \
  --stack-name wwii-pipeline-dev \
  --query "Stacks[0].Outputs[?OutputKey=='GitHubActionsDeployRoleArn'].OutputValue" \
  --output text
```

Then skip to **Step 5** below to add the secret to GitHub.

---

## Option B: Manual Setup (Without CloudFormation)

Use this if you want to create the OIDC resources independently of the pipeline stack.

### Step 1: Create the GitHub OIDC Identity Provider

This allows GitHub Actions to assume AWS roles without long-lived access keys.

```bash
aws iam create-open-id-connect-provider \
  --url https://token.actions.githubusercontent.com \
  --client-id-list sts.amazonaws.com \
  --thumbprint-list 6938fd4d98bab03faadb97b34396831e3780aea1 \
  --region us-east-1
```

> The thumbprint is GitHub's OIDC provider certificate. AWS validates it automatically — this value is a placeholder that AWS accepts.

---

### Step 2: Create the Deploy IAM Role

Create `github-actions-deploy-role.json`:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": {
        "Federated": "arn:aws:iam::340339225515:oidc-provider/token.actions.githubusercontent.com"
      },
      "Action": "sts:AssumeRoleWithWebIdentity",
      "Condition": {
        "StringEquals": {
          "token.actions.githubusercontent.com:aud": "sts.amazonaws.com"
        },
        "StringLike": {
          "token.actions.githubusercontent.com:sub": "repo:<your-org>/SecondWorldWarAsData:ref:refs/heads/main"
        }
      }
    }
  ]
}
```

Create the role:

```bash
aws iam create-role \
  --role-name github-actions-wwii-deploy \
  --assume-role-policy-document file://github-actions-deploy-role.json
```

---

### Step 3: Attach Permissions to the Role

Create `deploy-policy.json`:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "ECRAccess",
      "Effect": "Allow",
      "Action": [
        "ecr:GetAuthorizationToken",
        "ecr:BatchCheckLayerAvailability",
        "ecr:GetDownloadUrlForLayer",
        "ecr:BatchGetImage",
        "ecr:PutImage",
        "ecr:InitiateLayerUpload",
        "ecr:UploadLayerPart",
        "ecr:CompleteLayerUpload"
      ],
      "Resource": "*"
    },
    {
      "Sid": "S3DeployBucket",
      "Effect": "Allow",
      "Action": [
        "s3:PutObject",
        "s3:GetObject",
        "s3:ListBucket"
      ],
      "Resource": [
        "arn:aws:s3:::wwii-pipeline-deploy",
        "arn:aws:s3:::wwii-pipeline-deploy/*"
      ]
    },
    {
      "Sid": "LambdaUpdate",
      "Effect": "Allow",
      "Action": [
        "lambda:UpdateFunctionCode",
        "lambda:GetFunction"
      ],
      "Resource": "arn:aws:lambda:us-east-1:340339225515:function:dev-wwii-*"
    },
    {
      "Sid": "CloudFormation",
      "Effect": "Allow",
      "Action": [
        "cloudformation:CreateStack",
        "cloudformation:UpdateStack",
        "cloudformation:DescribeStacks",
        "cloudformation:DescribeStackEvents",
        "cloudformation:GetTemplate",
        "cloudformation:CreateChangeSet",
        "cloudformation:DescribeChangeSet",
        "cloudformation:ExecuteChangeSet",
        "cloudformation:DeleteChangeSet"
      ],
      "Resource": "arn:aws:cloudformation:us-east-1:340339225515:stack/wwii-pipeline-*/*"
    },
    {
      "Sid": "CloudFormationNestedStacks",
      "Effect": "Allow",
      "Action": [
        "iam:CreateRole",
        "iam:DeleteRole",
        "iam:AttachRolePolicy",
        "iam:DetachRolePolicy",
        "iam:PutRolePolicy",
        "iam:DeleteRolePolicy",
        "iam:GetRole",
        "iam:PassRole",
        "iam:TagRole"
      ],
      "Resource": "arn:aws:iam::340339225515:role/dev-wwii-*"
    },
    {
      "Sid": "NestedStackResources",
      "Effect": "Allow",
      "Action": [
        "ec2:*",
        "ecs:*",
        "logs:*",
        "dynamodb:*",
        "s3:CreateBucket",
        "s3:DeleteBucket",
        "s3:PutBucketPolicy",
        "s3:GetBucketPolicy",
        "events:*",
        "sns:*",
        "secretsmanager:GetSecretValue"
      ],
      "Resource": "*",
      "Condition": {
        "StringEquals": {
          "aws:RequestedRegion": "us-east-1"
        }
      }
    }
  ]
}
```

Attach it:

```bash
aws iam put-role-policy \
  --role-name github-actions-wwii-deploy \
  --policy-name DeployAccess \
  --policy-document file://deploy-policy.json
```

---

### Step 4: Create the ECR Repository (if not exists)

```bash
aws ecr create-repository \
  --repository-name wwii-pipeline \
  --region us-east-1 \
  --image-scanning-configuration scanOnPush=true \
  --encryption-configuration encryptionType=AES256
```

---

## Step 5: Add the GitHub Repository Secret

1. Go to your repo → **Settings** → **Secrets and variables** → **Actions**
2. Click **New repository secret**
3. Name: `AWS_DEPLOY_ROLE_ARN`
4. Value: `arn:aws:iam::340339225515:role/github-actions-wwii-deploy`

---

## Step 6: Verify

Push a change to `main` or trigger the workflow manually:

```bash
gh workflow run deploy.yml --field deploy_target=all
```

Monitor:

```bash
gh run watch
```

---

## Security Notes

- **No long-lived credentials** — uses OIDC federation, tokens are short-lived (1 hour)
- **Branch-locked** — only `main` branch can assume the role (enforced by the `sub` condition)
- **Least privilege** — role can only modify `dev-wwii-*` resources
- **Image scanning** — ECR scans on push, Trivy scans in the workflow before push
- **To add staging/prod**: duplicate the `StringLike` condition for additional branches, or create separate roles per environment

---

## Troubleshooting

| Issue | Fix |
|-------|-----|
| `Not authorized to perform: sts:AssumeRoleWithWebIdentity` | Check the `sub` condition matches your repo/branch exactly |
| `Error: No identity token` | Ensure `permissions: id-token: write` is in the workflow |
| `AccessDenied` on ECR push | Verify ECR permissions include `ecr:GetAuthorizationToken` with `Resource: "*"` |
| `Stack update failed` | Check CloudFormation events: `aws cloudformation describe-stack-events --stack-name wwii-pipeline-dev` |
| `Lambda update failed` | Verify function names match: `aws lambda list-functions --query "Functions[?starts_with(FunctionName, 'dev-wwii')]"` |

---

## File Reference

| File | Purpose |
|------|---------|
| `.github/workflows/deploy.yml` | CI/CD pipeline (build, scan, push, deploy) |
| `.github/workflows/tests.yml` | Unit tests (gate for deploy) |
| `cloudformation/main.yaml` | Root stack (orchestrates nested stacks) |
| `scripts/deploy_all.sh` | Manual deploy script (local fallback) |
| `scripts/update_lambdas.sh` | Manual Lambda update (local fallback) |

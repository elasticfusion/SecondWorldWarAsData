# S3 Storage Configuration

This guide explains how to configure S3 storage for maps extraction.

## Overview

Maps can be stored in two backends:
- **Filesystem** (default): Local storage in `output/maps/` and `output/maps_images/`
- **S3**: AWS S3 bucket storage

## Prerequisites

- AWS account with S3 access
- AWS credentials configured
- S3 bucket created

## Setup

### 1. Create S3 Bucket

```bash
aws s3 mb s3://your-wwii-data-bucket --region us-east-1
```

Or use AWS Console to create a bucket.

### 2. Configure AWS Credentials

**Option A: AWS CLI**
```bash
aws configure
# Enter your AWS Access Key ID
# Enter your AWS Secret Access Key
# Enter default region (e.g., us-east-1)
```

**Option B: Environment Variables**
```bash
export AWS_ACCESS_KEY_ID="your-access-key"
export AWS_SECRET_ACCESS_KEY="your-secret-key"
export AWS_DEFAULT_REGION="us-east-1"
```

**Option C: Credentials File**

Create `~/.aws/credentials`:
```ini
[default]
aws_access_key_id = your-access-key
aws_secret_access_key = your-secret-key
```

Create `~/.aws/config`:
```ini
[default]
region = us-east-1
```

### 3. Update config.yaml

```yaml
maps:
  enabled: true
  download_images: true
  storage_backend: "s3"
  s3_bucket: "your-wwii-data-bucket"
  s3_prefix: "maps/"
  s3_region: "us-east-1"
```

### 4. Run Extraction

```bash
python3 phase2_extract.py
```

## S3 Structure

Maps are stored with the following structure:

```
s3://your-bucket/maps/
├── metadata/
│   ├── {MapID}.json
│   ├── {MapID}.json
│   └── ...
└── images/
    ├── {MapID}.jpg
    ├── {MapID}.png
    └── ...
```

## Verification

Check uploaded files:

```bash
# List metadata files
aws s3 ls s3://your-bucket/maps/metadata/

# List image files
aws s3 ls s3://your-bucket/maps/images/

# Download a specific file
aws s3 cp s3://your-bucket/maps/metadata/{MapID}.json ./
```

## IAM Permissions

Minimum required IAM permissions:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "s3:PutObject",
        "s3:GetObject",
        "s3:ListBucket"
      ],
      "Resource": [
        "arn:aws:s3:::your-bucket/*",
        "arn:aws:s3:::your-bucket"
      ]
    }
  ]
}
```

## Cost Considerations

**Storage Costs:**
- S3 Standard: ~$0.023/GB/month
- Typical map: 100KB-2MB
- 1000 maps ≈ 500MB ≈ $0.01/month

**Request Costs:**
- PUT requests: $0.005 per 1000 requests
- GET requests: $0.0004 per 1000 requests

**Data Transfer:**
- Upload to S3: Free
- Download from S3: $0.09/GB (after 100GB free tier)

## Switching Between Backends

### Filesystem → S3

1. Update `config.yaml` to use S3
2. Re-run `phase2_extract.py`
3. Files will be uploaded to S3
4. Local files remain unchanged

### S3 → Filesystem

1. Update `config.yaml` to use filesystem
2. Re-run `phase2_extract.py`
3. Files will be saved locally
4. S3 files remain unchanged

## Troubleshooting

### "NoCredentialsError"
- AWS credentials not configured
- Run `aws configure` or set environment variables

### "AccessDenied"
- IAM permissions insufficient
- Check bucket policy and IAM user permissions

### "NoSuchBucket"
- Bucket doesn't exist or wrong region
- Verify bucket name and region in config

### "InvalidAccessKeyId"
- AWS credentials expired or invalid
- Regenerate access keys in AWS Console

## Best Practices

1. **Use IAM roles** for EC2/Lambda instead of access keys
2. **Enable versioning** on S3 bucket for data protection
3. **Set lifecycle policies** to archive old data to Glacier
4. **Enable server-side encryption** (SSE-S3 or SSE-KMS)
5. **Use separate buckets** for dev/staging/production

## Related Documentation

- [AWS S3 Documentation](https://docs.aws.amazon.com/s3/)
- [Boto3 S3 Documentation](https://boto3.amazonaws.com/v1/documentation/api/latest/reference/services/s3.html)
- `contextmanagement/Specs/maps.md` - Maps extraction specification

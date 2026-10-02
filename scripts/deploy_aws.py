#!/usr/bin/env python3
"""Deploy, validate, and manage the WWII Pipeline AWS infrastructure.

Usage:
    python3 scripts/deploy_aws.py validate
    python3 scripts/deploy_aws.py deploy --env dev --region us-east-1
    python3 scripts/deploy_aws.py status --env dev
    python3 scripts/deploy_aws.py destroy --env dev
"""

import argparse
import subprocess
import time
from pathlib import Path
from typing import Optional

TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "cloudformation"


def _probe_gpu_azs(region: str, profile: Optional[str]) -> list:
    """Probe for GPU-capable AZs (portable — no hardcoded AZ). Returns the first 2
    AZs offering the forgiving GPU family set, so the network builds exactly 2
    GPU+CPU AZ-subnets (operator: 2 public + 2 private, compute in private).
    Fail-fast if 0 GPU AZs; warn if only 1 (single-AZ, loses capacity resilience)."""
    import boto3

    sess = boto3.Session(profile_name=profile) if profile else boto3.Session()
    ec2 = sess.client("ec2", region_name=region)
    families = ["g4dn.xlarge", "g5.xlarge", "g6.xlarge"]
    az_sets = []
    for fam in families:
        resp = ec2.describe_instance_type_offerings(
            LocationType="availability-zone",
            Filters=[{"Name": "instance-type", "Values": [fam]}],
        )
        az_sets.append({o["Location"] for o in resp.get("InstanceTypeOfferings", [])})
    common = set.intersection(*az_sets) if az_sets else set()
    gpu_azs = (
        sorted(common) if common else sorted(set.union(*az_sets)) if az_sets else []
    )
    if not gpu_azs:
        raise SystemExit(
            f"FATAL: no GPU-capable AZ in {region} — cannot deploy GPU OCR."
        )
    if len(gpu_azs) == 1:
        print(
            f"  WARNING: only 1 GPU-capable AZ in {region} ({gpu_azs[0]}) — "
            f"single-AZ GPU (no capacity resilience)."
        )
    chosen = gpu_azs[:2]
    print(f"  GPU AZ probe ({region}): available={gpu_azs} -> using {chosen}")
    return chosen


TEMPLATES = [
    "network.yaml",
    "storage.yaml",
    "iam.yaml",
    "compute.yaml",
    "events.yaml",
    "main.yaml",
]


def _run(cmd: list[str], check: bool = True) -> subprocess.CompletedProcess:
    """Run a command and return result."""
    return subprocess.run(cmd, capture_output=True, text=True, check=check)


def _get_cf_client(region: str, profile: str | None):
    """Get boto3 CloudFormation client."""
    import boto3

    kwargs = {"region_name": region}
    if profile:
        session = boto3.Session(profile_name=profile)
        return session.client("cloudformation", **kwargs)
    return boto3.client("cloudformation", **kwargs)


def cmd_validate(_args):
    """Validate all CloudFormation templates with cfn-lint."""
    print("Validating CloudFormation templates...\n")
    errors = 0
    for name in TEMPLATES:
        path = TEMPLATE_DIR / name
        if not path.exists():
            print(f"  MISSING: {name}")
            errors += 1
            continue
        result = _run(["cfn-lint", str(path)], check=False)
        if result.returncode != 0:
            print(f"  FAIL: {name}")
            print(result.stdout)
            errors += 1
        else:
            print(f"  OK: {name}")

    print(f"\n{'PASSED' if errors == 0 else f'FAILED ({errors} errors)'}")
    return errors == 0


def cmd_deploy(args):
    """Deploy or update the CloudFormation stack."""
    if not cmd_validate(args):
        print("\nFix validation errors before deploying.")
        return

    # Read notification_email from config.yaml if not passed via CLI
    if not args.notification_email:
        import yaml

        config_path = Path(__file__).resolve().parent.parent / "config.yaml"
        if config_path.exists():
            with open(config_path, "r", encoding="utf-8") as f:
                config = yaml.safe_load(f)
            args.notification_email = config.get("aws", {}).get(
                "notification_email", ""
            )

    if args.dry_run:
        print("\n--dry-run: would deploy stack, exiting.")
        return

    cf = _get_cf_client(args.region, args.profile)
    stack_name = f"wwii-pipeline-{args.env}"

    # Probe GPU-capable AZs so the network builds exactly 2 aligned GPU+CPU subnets.
    gpu_azs = _probe_gpu_azs(args.region, args.profile)
    gpu_az1 = gpu_azs[0]
    gpu_az2 = gpu_azs[1] if len(gpu_azs) > 1 else gpu_azs[0]

    # Check if stack exists
    try:
        cf.describe_stacks(StackName=stack_name)
        action = "update"
    except cf.exceptions.ClientError:
        action = "create"

    print(f"\n{action.title()}ing stack: {stack_name}")

    method = cf.create_stack if action == "create" else cf.update_stack

    def _image_param(
        key: str, value: Optional[str], allow_previous: bool = True
    ) -> dict:
        # If no image was passed on an UPDATE, keep the currently-deployed image
        # (UsePreviousValue) rather than blanking it to "" — an empty image makes
        # every ECS TaskDef fail with "Container.image should not be null or
        # empty" (root cause of the 2026-07 ComputeStack rollback).
        # allow_previous=False for OPTIONAL images (video/av) that may not exist on
        # the stack yet: UsePreviousValue errors for a never-before-set parameter,
        # so fall back to "" (their templates treat "" as "resource not created").
        if value:
            return {"ParameterKey": key, "ParameterValue": value}
        if action == "update" and allow_previous:
            return {"ParameterKey": key, "UsePreviousValue": True}
        return {"ParameterKey": key, "ParameterValue": ""}

    try:
        method(
            StackName=stack_name,
            TemplateURL=f"https://{args.template_bucket}.s3.amazonaws.com/cloudformation/main.yaml",
            Parameters=[
                {"ParameterKey": "EnvironmentName", "ParameterValue": args.env},
                {
                    "ParameterKey": "TemplateBucket",
                    "ParameterValue": args.template_bucket,
                },
                {
                    "ParameterKey": "LambdaCodeBucket",
                    "ParameterValue": args.template_bucket,
                },
                {"ParameterKey": "LambdaCodeKey", "ParameterValue": "lambda/code.zip"},
                _image_param("OpenSerpImageUri", args.openserp_image),
                _image_param("PipelineImageUri", args.pipeline_image),
                _image_param("VideoImageUri", args.video_image),
                # AvImageUri is a NEW parameter (not on the deployed stack yet) —
                # UsePreviousValue would error, so force "" when no image is given.
                _image_param("AvImageUri", args.av_image, allow_previous=False),
                {
                    "ParameterKey": "NotificationEmail",
                    "ParameterValue": args.notification_email or "",
                },
                # M4 concurrency kill-switch — default OFF. Deploy the dispatcher
                # dormant; enable deliberately later.
                {
                    "ParameterKey": "MultiDocEnabled",
                    "ParameterValue": getattr(args, "multi_doc", "false") or "false",
                },
                {"ParameterKey": "GpuAz1", "ParameterValue": gpu_az1},
                {"ParameterKey": "GpuAz2", "ParameterValue": gpu_az2},
            ],
            Capabilities=["CAPABILITY_NAMED_IAM", "CAPABILITY_AUTO_EXPAND"],
        )
    except cf.exceptions.ClientError as e:
        if "No updates" in str(e):
            print("No changes to deploy.")
            return
        raise

    _wait_for_stack(cf, stack_name, action)


def cmd_status(args):
    """Show stack status."""
    cf = _get_cf_client(args.region, args.profile)
    stack_name = f"wwii-pipeline-{args.env}"

    try:
        resp = cf.describe_stacks(StackName=stack_name)
        stack = resp["Stacks"][0]
        print(f"Stack: {stack_name}")
        print(f"Status: {stack['StackStatus']}")
        print(f"Created: {stack.get('CreationTime', 'N/A')}")
        print(f"Updated: {stack.get('LastUpdatedTime', 'N/A')}")
        print("\nOutputs:")
        for output in stack.get("Outputs", []):
            print(f"  {output['OutputKey']}: {output['OutputValue']}")
    except cf.exceptions.ClientError:
        print(f"Stack {stack_name} not found.")


def cmd_destroy(args):
    """Delete the CloudFormation stack."""
    cf = _get_cf_client(args.region, args.profile)
    stack_name = f"wwii-pipeline-{args.env}"

    if args.dry_run:
        print(f"--dry-run: would delete stack {stack_name}")
        return

    confirm = input(f"Delete stack {stack_name}? This is irreversible. [y/N]: ")
    if confirm.lower() != "y":
        print("Aborted.")
        return

    print(f"Deleting stack: {stack_name}")
    cf.delete_stack(StackName=stack_name)
    _wait_for_stack(cf, stack_name, "delete")


def _wait_for_stack(cf, stack_name: str, action: str):
    """Wait for stack operation to complete, streaming events."""
    seen_events = set()
    while True:
        try:
            resp = cf.describe_stacks(StackName=stack_name)
            status = resp["Stacks"][0]["StackStatus"]
        except cf.exceptions.ClientError:
            if action == "delete":
                print("Stack deleted.")
                return
            raise

        # Print new events
        events = cf.describe_stack_events(StackName=stack_name)["StackEvents"]
        for event in reversed(events[:10]):
            eid = event["EventId"]
            if eid not in seen_events:
                seen_events.add(eid)
                reason = event.get("ResourceStatusReason", "")
                print(
                    f"  {event['ResourceType']} {event['LogicalResourceId']} "
                    f"{event['ResourceStatus']} {reason}"
                )

        if "COMPLETE" in status or "FAILED" in status:
            print(f"\nFinal status: {status}")
            return

        time.sleep(10)


def main():
    parser = argparse.ArgumentParser(description="WWII Pipeline AWS Deployment")
    sub = parser.add_subparsers(dest="command", required=True)

    # Common args
    for name, help_text in [
        ("validate", "Validate CloudFormation templates"),
        ("deploy", "Deploy or update stack"),
        ("status", "Show stack status"),
        ("destroy", "Delete stack"),
    ]:
        p = sub.add_parser(name, help=help_text)
        p.add_argument("--env", default="dev", help="Environment name")
        p.add_argument("--region", default="us-east-1", help="AWS region")
        p.add_argument("--profile", default=None, help="AWS CLI profile")
        p.add_argument(
            "--dry-run", action="store_true", help="Preview without executing"
        )

    # Deploy-specific args
    deploy_parser = sub.choices["deploy"]
    deploy_parser.add_argument(
        "--template-bucket", required=True, help="S3 bucket for templates"
    )
    deploy_parser.add_argument(
        "--openserp-image", default=None, help="ECR image URI for OpenSERP"
    )
    deploy_parser.add_argument(
        "--pipeline-image", default=None, help="ECR image URI for pipeline container"
    )
    deploy_parser.add_argument(
        "--video-image",
        default=None,
        help="ECR image URI for the separate video-processing container (optional)",
    )
    deploy_parser.add_argument(
        "--av-image",
        default=None,
        help="ECR image URI for the separate ClamAV scanning container (optional)",
    )
    deploy_parser.add_argument(
        "--notification-email",
        default=None,
        help="Email for Phase 2 completion notifications",
    )
    deploy_parser.add_argument(
        "--multi-doc",
        default="false",
        choices=["true", "false"],
        help="M4 concurrency kill-switch (default false = dispatcher dormant)",
    )

    args = parser.parse_args()
    {
        "validate": cmd_validate,
        "deploy": cmd_deploy,
        "status": cmd_status,
        "destroy": cmd_destroy,
    }[args.command](args)


if __name__ == "__main__":
    main()

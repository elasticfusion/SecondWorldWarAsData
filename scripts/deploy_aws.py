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

TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "cloudformation"
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

    # Check if stack exists
    try:
        cf.describe_stacks(StackName=stack_name)
        action = "update"
    except cf.exceptions.ClientError:
        action = "create"

    print(f"\n{action.title()}ing stack: {stack_name}")

    method = cf.create_stack if action == "create" else cf.update_stack
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
                {
                    "ParameterKey": "OpenSerpImageUri",
                    "ParameterValue": args.openserp_image or "",
                },
                {
                    "ParameterKey": "PipelineImageUri",
                    "ParameterValue": args.pipeline_image or "",
                },
                {
                    "ParameterKey": "NotificationEmail",
                    "ParameterValue": args.notification_email or "",
                },
            ],
            Capabilities=["CAPABILITY_NAMED_IAM"],
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
        "--notification-email",
        default=None,
        help="Email for Phase 2 completion notifications",
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

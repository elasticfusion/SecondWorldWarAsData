"""Regression (§1.0d radon-reduction wave 2b review): _launch_phase1_if_idle must NOT launch a
Phase-1 task when the pending title-uniqueness gate drops EVERY key (all duplicates/collisions).
A helper extraction briefly collapsed that "do not launch" outcome onto the same "" sentinel used
for "launch all books", causing a spurious Phase-1 launch over "all". _resolve_pending_launch_book
now returns None = do-not-launch, "" = launch-all.

NOTE: trigger_handler binds module-global boto3 clients at import; importing it at module top
level (outside moto) poisons those globals for the moto-based unit tests. So import it lazily
INSIDE each test (matching tests/unit/test_trigger_handler.py) and patch the globals.
"""

import os
from unittest.mock import MagicMock, patch

os.environ.setdefault("ECS_CLUSTER", "test-wwii-pipeline")
os.environ.setdefault("PRIVATE_SUBNET_IDS", "subnet-abc123")
os.environ.setdefault("SECURITY_GROUP_ID", "sg-abc123")
os.environ.setdefault("S3_BUCKET", "test-bucket")
os.environ.setdefault("CACHE_TABLE", "test-wwii-api-cache")
os.environ.setdefault("PHASE1_TASK_DEF", "test-wwii-phase1-parse")
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")


def _run(resolver_return):
    """Drive _launch_phase1_if_idle with an idle pipeline and a stubbed resolver; return the
    _run_task mock so the caller can assert whether a launch happened."""
    from lambda_handlers import trigger_handler as th

    idle_ecs = MagicMock()
    idle_ecs.list_tasks.return_value = {"taskArns": []}  # pipeline idle
    with (
        patch.object(th, "ecs", idle_ecs),
        patch.object(th, "_resolve_pending_launch_book", return_value=resolver_return),
        patch.object(th, "_run_task") as run_task,
    ):
        th._launch_phase1_if_idle()
    return run_task


def test_no_launch_when_resolver_returns_none():
    """None => do NOT launch any Phase-1 task (all pending were duplicates/collisions)."""
    run_task = _run(None)
    run_task.assert_not_called()


def test_launch_all_when_resolver_returns_empty_string():
    """'' => launch Phase 1 across all books (the legitimate launch-all sentinel)."""
    run_task = _run("")
    run_task.assert_called_once()
    assert run_task.call_args.kwargs.get("book_name") == ""


def test_launch_single_book_when_resolver_returns_name():
    run_task = _run("SomeBook")
    run_task.assert_called_once()
    assert run_task.call_args.kwargs.get("book_name") == "SomeBook"

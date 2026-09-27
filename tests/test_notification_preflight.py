"""Tests for the non-blocking SNS notification-subscription preflight.

Guards the silent-gap class where a notification/alarm topic has no CONFIRMED
subscription (e.g. an email sub that was never confirmed and SNS purged), so
alarms go nowhere. The check must WARN but never abort the pipeline.
"""

import logging
import os
from unittest.mock import MagicMock, patch

# Required env for importing ecs_entrypoint at module load (matches
# tests/unit/test_entrypoint_modes.py convention).
os.environ.setdefault("S3_BUCKET", "test-bucket")
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")

import ecs_entrypoint


def _call_with_subs(subs, monkeyenv=None):
    """Call the preflight with a fake SNS client returning `subs`, WITH the patch
    active during the call (the bug we guard against needs the mock live)."""
    fake_sns = MagicMock()
    fake_sns.list_subscriptions_by_topic.return_value = {"Subscriptions": subs}
    env = {
        "NOTIFICATION_TOPIC_ARN": "arn:aws:sns:us-east-1:111:dev-wwii-phase2-complete",
        "AWS_ACCOUNT_ID": "111",
        "ENV_NAME": "dev",
        "AWS_DEFAULT_REGION": "us-east-1",
    }
    env.update(monkeyenv or {})
    with (
        patch.dict(ecs_entrypoint.os.environ, env, clear=False),
        patch.object(ecs_entrypoint.boto3, "client", return_value=fake_sns),
    ):
        ecs_entrypoint._preflight_notification_subscriptions()


def test_no_subscriptions_warns(caplog):
    with caplog.at_level(logging.WARNING):
        _call_with_subs([])
    assert any("NO confirmed subscriptions" in r.message for r in caplog.records)


def test_pending_only_warns(caplog):
    subs = [{"Protocol": "email", "SubscriptionArn": "PendingConfirmation"}]
    with caplog.at_level(logging.WARNING):
        _call_with_subs(subs)
    msgs = [r.message for r in caplog.records]
    assert any("NO confirmed subscriptions" in m and "pending" in m for m in msgs)


def test_confirmed_subscription_no_warn(caplog):
    subs = [
        {
            "Protocol": "email",
            "SubscriptionArn": "arn:aws:sns:us-east-1:111:dev-wwii-phase2-complete:abc",
        }
    ]
    with caplog.at_level(logging.WARNING):
        _call_with_subs(subs)
    assert not any("NO confirmed subscriptions" in r.message for r in caplog.records)


def test_list_error_is_non_blocking(caplog):
    fake_sns = MagicMock()
    fake_sns.list_subscriptions_by_topic.side_effect = RuntimeError("no perms")
    env = {
        "NOTIFICATION_TOPIC_ARN": "arn:aws:sns:us-east-1:111:dev-wwii-phase2-complete",
        "AWS_ACCOUNT_ID": "111",
        "ENV_NAME": "dev",
    }
    with (
        patch.dict(ecs_entrypoint.os.environ, env, clear=False),
        patch.object(ecs_entrypoint.boto3, "client", return_value=fake_sns),
    ):
        # Must not raise.
        ecs_entrypoint._preflight_notification_subscriptions()

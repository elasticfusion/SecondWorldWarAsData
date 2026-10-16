"""nat_teardown cron decider — the SOLE NAT teardown owner.

Verifies the owner's model: do NOTHING while the queue has work OR a container is
processing (recheck in 10 min); tear down ONLY when queue empty AND no container; and
never tear down on uncertainty.
"""

import os
import sys
from unittest.mock import MagicMock, patch

os.environ.setdefault("ENV_NAME", "dev")
os.environ.setdefault("AWS_REGION", "us-east-1")

import lambda_handlers.nat_teardown as nt  # noqa: E402


def _ec2_with_nat(present: bool):
    ec2 = MagicMock()
    ec2.describe_nat_gateways.return_value = {
        "NatGateways": [{"NatGatewayId": "nat-1"}] if present else []
    }
    return ec2


def test_no_nat_does_nothing():
    with patch("boto3.client", return_value=_ec2_with_nat(False)):
        out = nt.handler({})
    assert out["action"] == "none"
    assert "already down" in out["reason"]


def test_demand_present_reschedules_recheck_not_teardown():
    """Queue work / running container present -> recheck in 10 min, NEVER delete."""
    with (
        patch("boto3.client", return_value=_ec2_with_nat(True)),
        patch("lambda_handlers.nat_manager._nat_demand_present", return_value=True),
        patch.object(nt, "_schedule_recheck") as sched,
        patch.object(nt, "_teardown") as teardown,
    ):
        out = nt.handler({})
    assert out["action"] == "recheck"
    sched.assert_called_once()
    teardown.assert_not_called()  # phases/queue-busy must NEVER tear down


def test_idle_tears_down():
    """Queue empty AND no container -> tear down (invoke nat_manager delete)."""
    with (
        patch("boto3.client", return_value=_ec2_with_nat(True)),
        patch("lambda_handlers.nat_manager._nat_demand_present", return_value=False),
        patch.object(nt, "_teardown", return_value={"action": "delete"}) as teardown,
        patch.object(nt, "_schedule_recheck") as sched,
    ):
        out = nt.handler({})
    assert out["action"] == "delete"
    teardown.assert_called_once()
    sched.assert_not_called()


def test_error_is_failsafe_no_teardown():
    """Any error -> no teardown (never destroy on uncertainty)."""
    with patch("boto3.client", side_effect=RuntimeError("boom")):
        out = nt.handler({})
    assert out["action"] == "none"
    assert "error" in out["reason"]


def test_schedule_recheck_rearms_on_conflict():
    """The 10-min re-arm must UPDATE (not leave) a conflicting one-shot so the recheck
    chain is deterministic — exercises _schedule_recheck itself (not mocked)."""
    import datetime as _dt

    sched = MagicMock()
    sched.exceptions.ConflictException = type("Conflict", (Exception,), {})
    sched.exceptions.ResourceNotFoundException = type("NotFound", (Exception,), {})
    sched.create_schedule.side_effect = sched.exceptions.ConflictException()
    sts = MagicMock()
    sts.get_caller_identity.return_value = {"Account": "123456789012"}

    def _client(name, **kw):
        return {"scheduler": sched, "sts": sts}[name]

    with patch("boto3.client", side_effect=_client):
        nt._schedule_recheck("123456789012")
    # Conflict -> must re-arm via update_schedule (deterministic chain), not silently leave it.
    sched.update_schedule.assert_called_once()
    assert "nat-teardown-recheck" in sched.update_schedule.call_args.kwargs["Name"]

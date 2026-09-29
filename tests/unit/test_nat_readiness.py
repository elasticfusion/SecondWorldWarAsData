"""Tests for the nat_manager readiness contract (_verify_ready).

Operator ask (Issue 1): verify networking readiness (NAT + all interface endpoints
AVAILABLE) BEFORE compute is requested — so a GPU OCR instance never boots without
a path to ECS/ECR (the RUNNABLE-stall root cause). These assert the gate returns
not-ready (with the missing components) until everything is truly up.
"""

import os
from unittest.mock import MagicMock, patch

os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
os.environ.setdefault("ENV_NAME", "dev")

from lambda_handlers import nat_manager as nm


def _ec2(nat_state=None, available_endpoints=None):
    """Mock ec2: nat_state (None=no NAT) + set of endpoint svc short-names available."""
    available_endpoints = available_endpoints or set()
    ec2 = MagicMock()

    ec2.describe_nat_gateways.return_value = (
        {"NatGateways": [{"NatGatewayId": "nat-1", "State": nat_state}]}
        if nat_state
        else {"NatGateways": []}
    )

    def _find_eps(Filters=None):
        # _find_endpoints (tag-based) returns available ones
        return {
            "VpcEndpoints": [
                {"ServiceName": f"com.amazonaws.us-east-1.{svc}", "State": "available"}
                for svc in available_endpoints
            ]
        }

    ec2.describe_vpc_endpoints.side_effect = lambda **kw: _find_eps(**kw)
    return ec2


def test_not_ready_when_no_nat():
    ec2 = _ec2(nat_state=None, available_endpoints=set(nm.INTERFACE_ENDPOINTS))
    with patch.object(nm, "_find_nat", return_value=None):
        ready, missing = nm._verify_ready(ec2, "us-east-1")
    assert ready is False
    assert "nat" in missing


def test_not_ready_when_nat_pending():
    ec2 = _ec2(nat_state="pending", available_endpoints=set(nm.INTERFACE_ENDPOINTS))
    with patch.object(nm, "_find_nat", return_value="nat-1"):
        ready, missing = nm._verify_ready(ec2, "us-east-1")
    assert ready is False and "nat" in missing


def test_not_ready_when_ecs_endpoint_missing():
    """The exact stall cause: NAT up but the ECS endpoint isn't available."""
    eps = set(nm.INTERFACE_ENDPOINTS) - {"ecs"}
    ec2 = _ec2(nat_state="available", available_endpoints=eps)
    with (
        patch.object(nm, "_find_nat", return_value="nat-1"),
        patch.object(nm, "_endpoint_available_untagged", return_value=False),
    ):
        ready, missing = nm._verify_ready(ec2, "us-east-1")
    assert ready is False and "ecs" in missing


def test_ready_when_nat_and_all_endpoints_available():
    ec2 = _ec2(nat_state="available", available_endpoints=set(nm.INTERFACE_ENDPOINTS))
    with patch.object(nm, "_find_nat", return_value="nat-1"):
        ready, missing = nm._verify_ready(ec2, "us-east-1")
    assert ready is True and missing == []


def test_verify_action_returns_readiness(monkeypatch):
    """handler action=verify surfaces the gate (for OCR preflight)."""
    import boto3

    fake_ec2 = _ec2(
        nat_state="available", available_endpoints=set(nm.INTERFACE_ENDPOINTS)
    )
    with (
        patch.object(boto3, "client", return_value=fake_ec2),
        patch.object(nm, "_find_nat", return_value="nat-1"),
    ):
        out = nm.handler({"action": "verify"}, None)
    assert out["ready"] is True and out["missing"] == []


def test_ensure_endpoints_ignores_deleting_as_present():
    """The create-after-delete race: a 'deleting' endpoint must NOT count as present
    (else recreation is skipped). _endpoint_present_untagged excludes 'deleting'."""
    ec2 = MagicMock()
    # a deleting endpoint for ecs -> describe with available/pending filter returns none
    ec2.describe_vpc_endpoints.return_value = {"VpcEndpoints": []}
    assert nm._endpoint_present_untagged(ec2, "us-east-1", "ecs") is False

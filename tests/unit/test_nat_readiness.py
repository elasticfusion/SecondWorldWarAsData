"""Tests for the nat_manager readiness contract (_verify_ready).

Operator ask (Issue 1): verify networking readiness (NAT + all interface endpoints
AVAILABLE) BEFORE compute is requested — so a GPU OCR instance never boots without
a path to ECS/ECR (the RUNNABLE-stall root cause). These assert the gate returns
not-ready (with the missing components) until everything is truly up.
"""

import os
import pytest
from unittest.mock import MagicMock, patch

os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
os.environ.setdefault("ENV_NAME", "dev")

from lambda_handlers import nat_manager as nm


def _ec2(nat_state=None, available_endpoints=None, route_active=True):
    """Mock ec2: nat_state (None=no NAT) + set of endpoint svc short-names available
    + whether the private route table has an ACTIVE 0.0.0.0/0 -> nat route."""
    available_endpoints = available_endpoints or set()
    ec2 = MagicMock()

    ec2.describe_nat_gateways.return_value = (
        {"NatGateways": [{"NatGatewayId": "nat-1", "State": nat_state}]}
        if nat_state
        else {"NatGateways": []}
    )

    def _dispatch(**kw):
        # describe_vpc_endpoints vs describe_route_tables share no args here; the
        # route-table call is made by _route_active via describe_route_tables.
        return _find_eps(**kw)

    def _find_eps(Filters=None):
        return {
            "VpcEndpoints": [
                {"ServiceName": f"com.amazonaws.us-east-1.{svc}", "State": "available"}
                for svc in available_endpoints
            ]
        }

    ec2.describe_vpc_endpoints.side_effect = lambda **kw: _find_eps(**kw)
    ec2.describe_route_tables.return_value = {
        "RouteTables": [
            {
                "Routes": [
                    {
                        "DestinationCidrBlock": "0.0.0.0/0",
                        "NatGatewayId": "nat-1",
                        "State": "active" if route_active else "blackhole",
                    }
                ]
            }
        ]
    }
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


def test_delete_all_refuses_when_ocr_demand_present():
    """Regression: a direct action=delete tore down NAT+endpoints under a RUNNING
    OCR job (ECR-pull timeout). _delete_all must refuse teardown while demand exists
    regardless of trigger path — the guard lives in _delete_all now."""
    ec2 = MagicMock()
    with patch.object(nm, "_nat_demand_present", return_value=True):
        out = nm._delete_all(ec2, "us-east-1", force=False)
    assert out.get("action") == "none" and out.get("reason") == "nat demand present"
    ec2.delete_nat_gateway.assert_not_called()


def test_delete_all_force_overrides_demand():
    """Operator teardown (force=True) bypasses the demand guard."""
    ec2 = MagicMock()
    ec2.describe_nat_gateways.return_value = {"NatGateways": []}
    ec2.describe_vpc_endpoints.return_value = {"VpcEndpoints": []}
    with patch.object(nm, "_nat_demand_present", return_value=True):
        out = nm._delete_all(ec2, "us-east-1", force=True)
    # proceeds (no 'nat demand present' short-circuit)
    assert out.get("reason") != "nat demand present"


def test_delete_all_proceeds_when_no_demand():
    ec2 = MagicMock()
    ec2.describe_nat_gateways.return_value = {"NatGateways": []}
    ec2.describe_vpc_endpoints.return_value = {"VpcEndpoints": []}
    with patch.object(nm, "_nat_demand_present", return_value=False):
        out = nm._delete_all(ec2, "us-east-1", force=False)
    assert out.get("reason") != "nat demand present"


def test_create_endpoint_tolerates_dns_conflict_then_succeeds():
    """A private-DNS conflict from a still-deleting endpoint is waited out +
    retried; the second create succeeds. (Regression: dispatcher CreateNat kept
    failing with 'conflicting DNS domain for api.ecr...'.)"""
    from botocore.exceptions import ClientError

    ec2 = MagicMock()
    conflict = ClientError(
        {
            "Error": {
                "Code": "InvalidParameter",
                "Message": "private-dns-enabled cannot be set because there is "
                "already a conflicting DNS domain for api.ecr.us-east-1.amazonaws.com",
            }
        },
        "CreateVpcEndpoint",
    )
    ec2.create_vpc_endpoint.side_effect = [conflict, None]  # fail once, then ok
    # not present during conflict (forces the retry path), no deleting endpoints
    ec2.describe_vpc_endpoints.return_value = {"VpcEndpoints": []}
    with patch.object(nm, "_wait_out_deleting"), patch("time.sleep"):
        nm._create_endpoint(ec2, "us-east-1", "ecr.api")
    assert ec2.create_vpc_endpoint.call_count == 2


def test_create_endpoint_conflict_but_already_present_is_success():
    """If the endpoint is already present when the DNS conflict fires, treat as
    success (no error, no infinite retry)."""
    from botocore.exceptions import ClientError

    ec2 = MagicMock()
    conflict = ClientError(
        {"Error": {"Code": "InvalidParameter", "Message": "conflicting DNS domain"}},
        "CreateVpcEndpoint",
    )
    ec2.create_vpc_endpoint.side_effect = conflict
    ec2.describe_vpc_endpoints.return_value = {"VpcEndpoints": [{"State": "available"}]}
    with patch("time.sleep"):
        nm._create_endpoint(ec2, "us-east-1", "ecr.api")  # must not raise
    assert ec2.create_vpc_endpoint.call_count == 1


def test_create_endpoint_reraises_non_dns_error():
    from botocore.exceptions import ClientError

    ec2 = MagicMock()
    other = ClientError(
        {"Error": {"Code": "UnauthorizedOperation", "Message": "nope"}},
        "CreateVpcEndpoint",
    )
    ec2.create_vpc_endpoint.side_effect = other
    with pytest.raises(ClientError):
        nm._create_endpoint(ec2, "us-east-1", "ecr.api")


def test_ocr_jobs_in_flight_fails_safe_on_access_denied():
    """Regression: missing batch:ListJobs must NOT read as 'no demand'. An
    AccessDenied (or any non-'queue-absent' error) => assume demand (True), so the
    teardown guard is never silently disabled by an IAM gap."""
    from botocore.exceptions import ClientError

    err = ClientError(
        {"Error": {"Code": "AccessDeniedException", "Message": "denied"}}, "ListJobs"
    )
    fake_batch = MagicMock()
    fake_batch.list_jobs.side_effect = err
    import boto3

    with patch.object(boto3, "client", return_value=fake_batch):
        assert nm._ocr_jobs_in_flight() is True


def test_ocr_jobs_in_flight_skips_absent_queue():
    """A genuinely absent queue is skipped (not treated as demand)."""
    from botocore.exceptions import ClientError

    err = ClientError(
        {"Error": {"Code": "JobQueueNotFoundException", "Message": "no queue"}},
        "ListJobs",
    )
    fake_batch = MagicMock()
    fake_batch.list_jobs.side_effect = err
    import boto3

    with patch.object(boto3, "client", return_value=fake_batch):
        assert nm._ocr_jobs_in_flight() is False


def test_ocr_jobs_in_flight_true_when_job_listed():
    fake_batch = MagicMock()
    fake_batch.list_jobs.return_value = {"jobSummaryList": [{"jobId": "j1"}]}
    import boto3

    with patch.object(boto3, "client", return_value=fake_batch):
        assert nm._ocr_jobs_in_flight() is True


def test_not_ready_when_route_not_active():
    """NAT available + all endpoints available but the default route is NOT active
    (propagation lag / blackhole) -> not ready, 'route' missing."""
    ec2 = _ec2(
        nat_state="available",
        available_endpoints=set(nm.INTERFACE_ENDPOINTS),
        route_active=False,
    )
    with patch.object(nm, "_find_nat", return_value="nat-1"):
        ready, missing = nm._verify_ready(ec2, "us-east-1")
    assert ready is False and "route" in missing


def test_ready_requires_active_route():
    """Readiness requires NAT available AND endpoints AND an ACTIVE default route."""
    ec2 = _ec2(
        nat_state="available",
        available_endpoints=set(nm.INTERFACE_ENDPOINTS),
        route_active=True,
    )
    with patch.object(nm, "_find_nat", return_value="nat-1"):
        ready, missing = nm._verify_ready(ec2, "us-east-1")
    assert ready is True and missing == []


def test_pending_grok_batch_keeps_nat_up(monkeypatch):
    """Operator directive: a pending Grok batch job is NAT demand (NAT stays up)."""
    # No leases, no running tasks, no OCR jobs, but a pending batch exists.
    monkeypatch.setattr(nm, "_ocr_jobs_in_flight", lambda: False)
    monkeypatch.setattr(nm, "_grok_batch_pending", lambda: True)
    fake_table = MagicMock()
    fake_table.scan.return_value = {"Items": []}  # no leases
    monkeypatch.setattr(nm, "_lease_table", lambda: fake_table)
    fake_ecs = MagicMock()
    fake_ecs.list_tasks.return_value = {"taskArns": []}
    monkeypatch.setattr(nm, "_ecs_client", lambda: fake_ecs)
    assert nm._nat_demand_present() is True


def test_no_demand_when_nothing_pending(monkeypatch):
    """All demand sources empty -> teardown allowed (demand False)."""
    monkeypatch.setattr(nm, "_ocr_jobs_in_flight", lambda: False)
    monkeypatch.setattr(nm, "_grok_batch_pending", lambda: False)
    fake_table = MagicMock()
    fake_table.scan.return_value = {"Items": []}
    monkeypatch.setattr(nm, "_lease_table", lambda: fake_table)
    fake_ecs = MagicMock()
    fake_ecs.list_tasks.return_value = {"taskArns": []}
    monkeypatch.setattr(nm, "_ecs_client", lambda: fake_ecs)
    assert nm._nat_demand_present() is False


def test_grok_batch_ready_state_keeps_nat(monkeypatch):
    """A batch in 'ready' (completed at Grok, not yet retrieved) still needs NAT —
    the completion->retrieve window must not tear NAT down."""
    import time as _t

    now = int(_t.time())
    fake = MagicMock()
    fake.scan.return_value = {
        "Items": [{"cache_key": "batch_job#x", "status": "ready", "submitted_at": now}]
    }
    monkeypatch.setattr("src.utils.job_queue._get_table", lambda: fake)
    assert nm._grok_batch_pending() is True


def test_grok_batch_zombie_age_capped(monkeypatch):
    """A 'pending' job older than the age cap no longer pins NAT (cost-tail guard)."""
    import time as _t

    old = int(_t.time()) - 50 * 3600  # 50h old, cap default 8h
    fake = MagicMock()
    fake.scan.return_value = {
        "Items": [
            {"cache_key": "batch_job#z", "status": "pending", "submitted_at": old}
        ]
    }
    monkeypatch.setattr("src.utils.job_queue._get_table", lambda: fake)
    assert nm._grok_batch_pending() is False

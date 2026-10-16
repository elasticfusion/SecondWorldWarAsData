"""Tests for nat_manager NAT-demand awareness (M3 churn fix).

The Phase1->Phase2 NAT churn: a 'completed successfully' SNS message tore down
NAT unconditionally while Phase 2 was still starting. Fix: the SNS teardown path
now checks cluster demand (live leases OR running pipeline tasks) first.

Patches the module's _lease_table/_ecs_client helpers (NOT global boto3) so these
tests can't leak client state onto moto-based tests.
"""

import os
import time
from unittest.mock import MagicMock, patch

os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
os.environ.setdefault("ENV_NAME", "dev")

from lambda_handlers import nat_manager as nm


def _sns_completion_event():
    return {
        "Records": [
            {
                "EventSource": "aws:sns",
                "Sns": {
                    "Message": "Phase 2 (Extract) completed successfully.\nBucket: x"
                },
            }
        ]
    }


def _table_with(items):
    t = MagicMock()
    t.scan.return_value = {"Items": items}
    return t


def _ecs_with(task_arns):
    e = MagicMock()
    e.list_tasks.return_value = {"taskArns": task_arns}

    # Mirror the real describe_tasks: each task has a 'group'. Tasks whose ARN
    # mentions openserp get the openserp service group (so the demand check's
    # group-based exclusion can drop them); others are pipeline phase tasks.
    def _describe(cluster, tasks):  # noqa: ARG001
        out = []
        for arn in tasks:
            if "openserp" in arn:
                group = f"service:{nm.ENV_NAME}-wwii-openserp"
            elif "phase1" in arn:
                group = f"family:{nm.ENV_NAME}-wwii-phase1-parse"
            elif "phase3" in arn:
                group = f"family:{nm.ENV_NAME}-wwii-phase3-enrich"
            else:
                group = f"family:{nm.ENV_NAME}-wwii-phase2-extract"
            out.append({"taskArn": arn, "group": group})
        return {"tasks": out}

    e.describe_tasks.side_effect = _describe
    return e


def test_demand_present_when_live_lease():
    now = int(time.time())
    with patch.object(
        nm,
        "_lease_table",
        return_value=_table_with([{"cache_key": "nat#lease#t1", "ttl": now + 500}]),
    ):
        assert nm._nat_demand_present() is True


def test_expired_lease_does_not_count():
    now = int(time.time())
    with (
        patch.object(
            nm,
            "_lease_table",
            return_value=_table_with([{"cache_key": "nat#lease#t1", "ttl": now - 10}]),
        ),
        patch.object(nm, "_ecs_client", return_value=_ecs_with([])),
        patch.object(nm, "_ocr_jobs_in_flight", return_value=False),
        patch.object(nm, "_grok_batch_pending", return_value=False),
    ):
        assert nm._nat_demand_present() is False


def test_demand_present_when_running_pipeline_task():
    with (
        patch.object(nm, "_lease_table", return_value=_table_with([])),
        patch.object(
            nm,
            "_ecs_client",
            return_value=_ecs_with(["arn:.../dev-wwii-phase2-extract/abc"]),
        ),
    ):
        assert nm._nat_demand_present() is True


def test_openserp_only_task_is_not_demand():
    """Regression: an openserp SERVICE task must not count as pipeline demand.
    The exclusion inspects the task GROUP (service:...openserp), not the ARN."""
    # _ecs_with tags any ARN containing 'openserp' with the openserp group.
    openserp_arn = "arn:aws:ecs:us-east-1:1:task/dev-wwii-pipeline/openserp-task-1"
    with (
        patch.object(nm, "_lease_table", return_value=_table_with([])),
        patch.object(nm, "_ecs_client", return_value=_ecs_with([openserp_arn])),
        patch.object(nm, "_ocr_jobs_in_flight", return_value=False),
        patch.object(nm, "_grok_batch_pending", return_value=False),
    ):
        assert nm._nat_demand_present() is False


def test_demand_present_when_ocr_batch_job_in_flight():
    """GPU OCR jobs need NAT egress but aren't ECS tasks / leases — count them."""
    with (
        patch.object(nm, "_lease_table", return_value=_table_with([])),
        patch.object(nm, "_ecs_client", return_value=_ecs_with([])),
        patch.object(nm, "_ocr_jobs_in_flight", return_value=True),
    ):
        assert nm._nat_demand_present() is True


def test_no_demand_when_no_leases_no_tasks_no_ocr():
    with (
        patch.object(nm, "_lease_table", return_value=_table_with([])),
        patch.object(nm, "_ecs_client", return_value=_ecs_with([])),
        patch.object(nm, "_ocr_jobs_in_flight", return_value=False),
        patch.object(nm, "_grok_batch_pending", return_value=False),
    ):
        assert nm._nat_demand_present() is False


def test_no_demand_when_only_openserp_and_no_leases():
    with (
        patch.object(nm, "_lease_table", return_value=_table_with([])),
        patch.object(nm, "_ocr_jobs_in_flight", return_value=False),
        patch.object(nm, "_grok_batch_pending", return_value=False),
        patch.object(
            nm, "_ecs_client", return_value=_ecs_with(["arn:.../dev-wwii-openserp/xyz"])
        ),
    ):
        assert nm._nat_demand_present() is False


def test_openserp_worker_task_IS_demand():
    """Regression (SQS worker): a running openserp-WORKER task MUST count as NAT demand.
    Only the browser support service (group service:{env}-wwii-openserp) is excluded; the
    worker (service:{env}-wwii-openserp-worker) needs NAT for ECR/S3/Grok/search egress, so
    tearing NAT down under it caused ECR-pull i/o timeouts."""
    ecs = MagicMock()
    ecs.list_tasks.return_value = {"taskArns": ["arn:.../worker-1"]}
    ecs.describe_tasks.return_value = {
        "tasks": [
            {
                "taskArn": "arn:.../worker-1",
                "group": f"service:{nm.ENV_NAME}-wwii-openserp-worker",
            }
        ]
    }
    with (
        patch.object(nm, "_lease_table", return_value=_table_with([])),
        patch.object(nm, "_ocr_jobs_in_flight", return_value=False),
        patch.object(nm, "_grok_batch_pending", return_value=False),
        patch.object(nm, "_ecs_client", return_value=ecs),
    ):
        assert nm._nat_demand_present() is True  # worker keeps NAT up


def test_demand_check_error_assumes_present():
    with patch.object(nm, "_lease_table", side_effect=RuntimeError("boom")):
        assert nm._nat_demand_present() is True  # never tear down on uncertainty


def test_sns_completion_never_tears_down():
    """Phase-completion SNS is now a NO-OP: teardown is owned exclusively by the
    nat_teardown cron Lambda. SNS never tears down, regardless of demand."""
    for demand in (True, False):
        with patch.object(nm, "_nat_demand_present", return_value=demand):
            with patch.object(nm, "_delete_all") as del_all:
                out = nm.handler(_sns_completion_event(), None)
        assert out["action"] == "none"
        assert "nat_teardown cron" in out["reason"]
        del_all.assert_not_called()  # phase completion NEVER tears down


# NOTE: the "teardown proceeds when no demand" path is exercised by the existing
# action=delete tests; we don't re-test it here because the SNS handler
# instantiates a real boto3 ec2 client before _delete_all, which corrupts moto
# state for later tests under CI's default ordering. The demand-SKIP case above
# is the actual churn fix.


# --- Operator readiness contract: NAT must stay UP while ANY of these is true ---
# (Validate the teardown-guard answers each distinct "work in progress?" question.)


def _no_leases_no_ocr_no_batch(monkeypatch):
    """Zero out every demand source EXCEPT running ECS tasks, so a test can assert
    the task-based (phase) demand in isolation."""
    monkeypatch.setattr(nm, "_lease_table", lambda: _table_with([]))
    monkeypatch.setattr(nm, "_ocr_jobs_in_flight", lambda: False)
    monkeypatch.setattr(nm, "_grok_batch_pending", lambda: False)


def test_demand_q1_phase1_running(monkeypatch):
    """Q1: is Phase 1 running? -> NAT stays up."""
    _no_leases_no_ocr_no_batch(monkeypatch)
    monkeypatch.setattr(
        nm, "_ecs_client", lambda: _ecs_with(["arn:.../dev-wwii-phase1-parse/aaa"])
    )
    assert nm._nat_demand_present() is True


def test_demand_q2_phase2_container_working(monkeypatch):
    """Q2: is Phase 2 / its container working? -> NAT stays up."""
    _no_leases_no_ocr_no_batch(monkeypatch)
    monkeypatch.setattr(
        nm, "_ecs_client", lambda: _ecs_with(["arn:.../dev-wwii-phase2-extract/bbb"])
    )
    assert nm._nat_demand_present() is True


def test_demand_q3_phase2_waiting_on_grok_batch(monkeypatch):
    """Q3: is Phase 2 waiting on a Grok batch (no running task)? -> NAT stays up."""
    monkeypatch.setattr(nm, "_lease_table", lambda: _table_with([]))
    monkeypatch.setattr(nm, "_ocr_jobs_in_flight", lambda: False)
    monkeypatch.setattr(nm, "_ecs_client", lambda: _ecs_with([]))  # no running task
    monkeypatch.setattr(nm, "_grok_batch_pending", lambda: True)  # batch pending
    assert nm._nat_demand_present() is True


def test_demand_q4_phase3_running(monkeypatch):
    """Q4: is Phase 3 running? -> NAT stays up."""
    _no_leases_no_ocr_no_batch(monkeypatch)
    monkeypatch.setattr(
        nm, "_ecs_client", lambda: _ecs_with(["arn:.../dev-wwii-phase3-enrich/ccc"])
    )
    assert nm._nat_demand_present() is True


def test_demand_none_all_four_idle_allows_teardown(monkeypatch):
    """When ALL four are idle (no phase task, no pending batch, no lease/OCR),
    the guard reports NO demand -> teardown is allowed."""
    _no_leases_no_ocr_no_batch(monkeypatch)
    monkeypatch.setattr(nm, "_ecs_client", lambda: _ecs_with([]))
    assert nm._nat_demand_present() is False


def test_teardown_after_4h_dedup_wait_no_phase3(monkeypatch):
    """Operator contract: Phase 2/dedup parked for 4h (the gateway lease has
    EXPIRED) with no Phase 3 activity -> NAT AND endpoints are torn down.

    Models the state after the human-dedup wait: no running task, no pending Grok
    batch (Phase 2 already produced entities), no OCR, and the 4h nat#lease#gateway
    floor expired. The demand gate must report NO demand and _delete_all must remove
    both the NAT gateway and the VPC endpoints.
    """
    now = int(time.time())
    # 1) Demand gate: only an EXPIRED gateway lease remains; everything else idle.
    expired_lease = _table_with(
        [{"cache_key": "nat#lease#gateway", "ttl": now - 10}]  # 4h floor expired
    )
    with (
        patch.object(nm, "_lease_table", return_value=expired_lease),
        patch.object(nm, "_ecs_client", return_value=_ecs_with([])),  # no phase tasks
        patch.object(nm, "_ocr_jobs_in_flight", return_value=False),
        patch.object(nm, "_grok_batch_pending", return_value=False),  # dedup wait: none
    ):
        assert nm._nat_demand_present() is False  # teardown allowed

    # 2) _delete_all (unforced) honors the gate and deletes BOTH NAT + endpoints.
    ec2 = MagicMock()
    with (
        patch.object(nm, "_nat_demand_present", return_value=False),
        patch.object(nm, "_find_nat", return_value="nat-1"),
        patch.object(
            nm,
            "_find_endpoints",
            return_value=[{"VpcEndpointId": "vpce-1"}, {"VpcEndpointId": "vpce-2"}],
        ),
        patch.object(nm, "_notify"),
    ):
        ec2.describe_nat_gateways.return_value = {
            "NatGateways": [{"NatGatewayAddresses": [{"AllocationId": "eipalloc-1"}]}]
        }
        out = nm._delete_all(ec2, "us-east-1")

    assert out == {"status": "deleted"}
    ec2.delete_nat_gateway.assert_called_once_with(NatGatewayId="nat-1")
    ec2.delete_vpc_endpoints.assert_called_once()
    assert sorted(ec2.delete_vpc_endpoints.call_args.kwargs["VpcEndpointIds"]) == [
        "vpce-1",
        "vpce-2",
    ]


def test_no_teardown_before_4h_lease_expires(monkeypatch):
    """Inverse guard: within the 4h window (gateway lease still LIVE), NAT is NOT
    torn down even if nothing else is active — the lease floor holds it."""
    now = int(time.time())
    live_lease = _table_with(
        [{"cache_key": "nat#lease#gateway", "ttl": now + 3600}]  # ~1h into 4h floor
    )
    with (
        patch.object(nm, "_lease_table", return_value=live_lease),
        patch.object(nm, "_ecs_client", return_value=_ecs_with([])),
        patch.object(nm, "_ocr_jobs_in_flight", return_value=False),
        patch.object(nm, "_grok_batch_pending", return_value=False),
    ):
        assert nm._nat_demand_present() is True  # lease floor keeps NAT up

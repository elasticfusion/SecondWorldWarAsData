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


def test_no_demand_when_only_openserp_and_no_leases():
    with (
        patch.object(nm, "_lease_table", return_value=_table_with([])),
        patch.object(
            nm, "_ecs_client", return_value=_ecs_with(["arn:.../dev-wwii-openserp/xyz"])
        ),
    ):
        assert nm._nat_demand_present() is False


def test_demand_check_error_assumes_present():
    with patch.object(nm, "_lease_table", side_effect=RuntimeError("boom")):
        assert nm._nat_demand_present() is True  # never tear down on uncertainty


def test_sns_completion_skips_teardown_when_demand():
    with patch.object(nm, "_nat_demand_present", return_value=True):
        with patch.object(nm, "_delete_all") as del_all:
            out = nm.handler(_sns_completion_event(), None)
    assert out["reason"] == "nat demand present"
    del_all.assert_not_called()  # NAT NOT torn down — the churn fix


# NOTE: the "teardown proceeds when no demand" path is exercised by the existing
# action=delete tests; we don't re-test it here because the SNS handler
# instantiates a real boto3 ec2 client before _delete_all, which corrupts moto
# state for later tests under CI's default ordering. The demand-SKIP case above
# is the actual churn fix.

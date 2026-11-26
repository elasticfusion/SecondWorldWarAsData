"""Task 1.2c (owner 2026-11-18): one zonal NAT that fails over to the other AZ.

The NAT used to be created only in PublicSubnet1, which both private subnets route through: an
outage of that AZ left both AZs without internet until the AZ came back. Now nat_manager places
the NAT in the first public subnet not marked bad, marks a subnet bad when its NAT cannot be
created, fails, stays pending or never becomes available, alerts a failover, and a task that finds
no internet through a "ready" NAT asks for one in the other AZ.
"""

import os
from datetime import datetime, timedelta, timezone
from unittest import mock

import pytest

os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")

from lambda_handlers import nat_manager as nm  # noqa: E402

A, B = "subnet-az1", "subnet-az2"


def _client_error(code):
    from botocore.exceptions import ClientError

    return ClientError({"Error": {"Code": code, "Message": code}}, "CreateNatGateway")


class FakeTable:
    def __init__(self):
        self.items = {}

    def update_item(
        self, Key, UpdateExpression, ConditionExpression, ExpressionAttributeValues
    ):  # noqa: N803
        """Only _claim_failover's conditional SET last_failover."""
        item = self.items.setdefault(Key["cache_key"], {"cache_key": Key["cache_key"]})
        last = item.get("last_failover")
        if last is not None and not last < ExpressionAttributeValues[":cut"]:
            raise _client_error("ConditionalCheckFailedException")
        item["last_failover"] = ExpressionAttributeValues[":now"]

    def get_item(self, Key):  # noqa: N803
        item = self.items.get(Key["cache_key"])
        return {"Item": dict(item)} if item else {}

    def put_item(self, Item, **kw):  # noqa: N803
        self.items[Item["cache_key"]] = dict(Item)

    def delete_item(self, Key):  # noqa: N803
        self.items.pop(Key["cache_key"], None)


class FakeEc2:
    """NAT gateways by id: create / describe / delete, with per-subnet failure injection."""

    def __init__(
        self, broken=(), never_available=(), error="InsufficientFreeAddressesInSubnet"
    ):
        self.nats = {}
        self.broken = set(broken)
        self.error = error
        self.delete_fails = False
        self.never_available = set(never_available)
        self.routes = []
        self.released = []
        self.n = 0

    def describe_addresses(self, **kw):
        return {"Addresses": []}

    def allocate_address(self, **kw):
        self.n += 1
        return {"AllocationId": f"eip-{self.n}"}

    def release_address(self, AllocationId):  # noqa: N803
        self.released.append(AllocationId)

    def create_nat_gateway(self, SubnetId, AllocationId, **kw):  # noqa: N803
        if SubnetId in self.broken:
            raise _client_error(self.error)
        nat_id = f"nat-{SubnetId}-{len(self.nats)}"
        self.nats[nat_id] = {
            "NatGatewayId": nat_id,
            "State": "pending",
            "SubnetId": SubnetId,
            "CreateTime": datetime.now(timezone.utc),
            "NatGatewayAddresses": [{"AllocationId": AllocationId}],
        }
        return {"NatGateway": {"NatGatewayId": nat_id}}

    def describe_nat_gateways(self, Filter=None, NatGatewayIds=None):  # noqa: N803
        if NatGatewayIds:
            return {
                "NatGateways": [self.nats[i] for i in NatGatewayIds if i in self.nats]
            }
        states = next(f["Values"] for f in Filter if f["Name"] == "state")
        return {"NatGateways": [n for n in self.nats.values() if n["State"] in states]}

    def delete_nat_gateway(self, NatGatewayId):  # noqa: N803
        if self.delete_fails:
            raise _client_error("Throttling")
        self.nats[NatGatewayId]["State"] = "deleting"

    def get_waiter(self, _name):
        ec2 = self

        class Waiter:
            def wait(self, NatGatewayIds, WaiterConfig):  # noqa: N803
                nat = ec2.nats[NatGatewayIds[0]]
                if nat["SubnetId"] in ec2.never_available:
                    from botocore.exceptions import WaiterError

                    raise WaiterError(
                        "NatGatewayAvailable", "Max attempts exceeded", {}
                    )
                nat["State"] = "available"

        return Waiter()

    def live(self):
        return [n for n in self.nats.values() if n["State"] in ("pending", "available")]


@pytest.fixture
def env(monkeypatch):
    table = FakeTable()
    monkeypatch.setattr(nm, "PUBLIC_SUBNETS", [A, B])
    monkeypatch.setattr(nm, "_az_table", lambda: table)
    monkeypatch.setattr(nm, "_update_route", lambda ec2, nat_id: None)
    monkeypatch.setattr(nm.time, "sleep", lambda *_: None)
    monkeypatch.setattr(nm, "_failed_seen", set())
    monkeypatch.setattr(nm, "_DEADLINE", [0.0])
    alerts = []
    monkeypatch.setattr(nm, "_notify", alerts.append)
    return table, alerts


def test_the_nat_goes_in_the_primary_az_when_it_is_healthy(env):
    table, alerts = env
    ec2 = FakeEc2()
    nm._ensure_nat(ec2, table)
    assert [n["SubnetId"] for n in ec2.live()] == [A] and alerts == []


def test_a_create_failure_in_az1_fails_over_to_az2_and_alerts(env):
    table, alerts = env
    ec2 = FakeEc2(broken={A})
    nm._ensure_nat(ec2, table)
    assert [n["SubnetId"] for n in ec2.live()] == [B]
    assert "eip-1" in ec2.released  # the unused EIP is not left to cost money
    assert len(alerts) == 1 and "NAT failover" in alerts[0] and B in alerts[0]
    assert A in table.items[nm.AZ_STATE_KEY]["bad"]


def test_a_nat_that_never_becomes_available_moves_on_the_next_attempt(env):
    table, alerts = env
    ec2 = FakeEc2(never_available={A})
    with pytest.raises(nm.NatNotReady):
        nm._ensure_nat(ec2, table)
    assert ec2.live() == []  # the stuck NAT was deleted
    nm._ensure_nat(ec2, table)  # the task's readiness loop retries
    assert [n["SubnetId"] for n in ec2.live()] == [B] and len(alerts) == 1


def test_a_nat_stuck_pending_is_replaced(env):
    table, _ = env
    ec2 = FakeEc2()
    ec2.nats["old"] = {
        "NatGatewayId": "old",
        "State": "pending",
        "SubnetId": A,
        "CreateTime": datetime.now(timezone.utc) - timedelta(minutes=10),
        "NatGatewayAddresses": [],
    }
    nm._ensure_nat(ec2, table)
    assert ec2.nats["old"]["State"] == "deleting"
    assert [n["SubnetId"] for n in ec2.live()] == [B]


def test_a_failed_nat_marks_its_subnet_bad(env):
    table, _ = env
    ec2 = FakeEc2()
    ec2.nats["dead"] = {
        "NatGatewayId": "dead",
        "State": "failed",
        "SubnetId": A,
        "FailureCode": "InternalError",
        "CreateTime": datetime.now(timezone.utc),
    }
    nm._ensure_nat(ec2, table)
    assert [n["SubnetId"] for n in ec2.live()] == [B]
    assert "InternalError" in table.items[nm.AZ_STATE_KEY]["bad"][A]["reason"]


def test_after_the_bad_window_the_primary_is_used_again(env, monkeypatch, caplog):
    table, _ = env
    nm._mark_bad(table, A, "outage")
    table.items[nm.AZ_STATE_KEY]["failed_over"] = True
    table.items[nm.AZ_STATE_KEY]["bad"][A]["until"] = 0  # expired
    ec2 = FakeEc2()
    nm._ensure_nat(ec2, table)
    assert [n["SubnetId"] for n in ec2.live()] == [A]
    assert table.items[nm.AZ_STATE_KEY]["failed_over"] is False
    assert "OBSERV nat.failback" in caplog.text


def test_if_every_az_looks_bad_the_least_recent_is_still_tried(env):
    table, _ = env
    nm._mark_bad(table, A, "x")
    nm._mark_bad(table, B, "y")
    table.items[nm.AZ_STATE_KEY]["bad"][B]["until"] = 1  # marked bad earliest
    assert nm._subnet_order(table.items[nm.AZ_STATE_KEY]) == [B, A]


def test_a_task_reporting_no_egress_gets_a_nat_in_the_other_az(env):
    table, alerts = env
    ec2 = FakeEc2()
    nm._ensure_nat(ec2, table)
    first = ec2.live()[0]["NatGatewayId"]
    nm._failover_nat(ec2, table, first)
    nm._ensure_nat(ec2, table)
    assert ec2.nats[first]["State"] == "deleting"
    assert [n["SubnetId"] for n in ec2.live()] == [B] and len(alerts) == 1


def test_many_tasks_reporting_the_same_outage_fail_over_once(env):
    table, _ = env
    ec2 = FakeEc2()
    nm._ensure_nat(ec2, table)
    first = ec2.live()[0]["NatGatewayId"]
    nm._failover_nat(ec2, table, first)
    nm._ensure_nat(ec2, table)
    second = ec2.live()[0]["NatGatewayId"]
    nm._failover_nat(ec2, table, first)  # a late report about the old NAT: ignored
    nm._failover_nat(ec2, table, second)  # within 10 minutes: no ping-pong
    assert ec2.nats[second]["State"] == "available"


def test_the_az_state_is_not_nat_demand():
    assert not nm.AZ_STATE_KEY.startswith("nat#lease#")


def test_a_single_subnet_config_still_works(monkeypatch):
    monkeypatch.setenv("PUBLIC_SUBNET_ID", "subnet-only")
    monkeypatch.delenv("PUBLIC_SUBNET_IDS", raising=False)
    import importlib

    reloaded = importlib.reload(nm)
    try:
        assert reloaded.PUBLIC_SUBNETS == ["subnet-only"]
    finally:
        monkeypatch.delenv("PUBLIC_SUBNET_ID")
        importlib.reload(nm)


# --- the task side ---------------------------------------------------------------------------------


@pytest.fixture
def task(monkeypatch):
    os.environ.setdefault("S3_BUCKET", "test-bucket")
    import ecs_entrypoint as ee

    monkeypatch.setattr(ee.time, "sleep", lambda *_: None)
    return ee


def _lambda_returning(*payloads):
    import io
    import json

    sent = []
    queue = list(payloads)

    class L:
        def invoke(self, FunctionName, InvocationType, Payload):  # noqa: N803
            sent.append(json.loads(Payload))
            return {"Payload": io.BytesIO(json.dumps(queue.pop(0)).encode())}

    return L(), sent


def test_ready_but_no_internet_asks_once_for_a_failover(task, monkeypatch):
    client, sent = _lambda_returning(
        {"status": "ready", "nat": "nat-a"},
        {"status": "ready", "nat": "nat-b", "failover": "done"},
    )
    monkeypatch.setattr(task.boto3, "client", lambda *a, **k: client)
    probes = iter([False, True])
    monkeypatch.setattr(task, "_egress_ok", lambda: next(probes))
    task._ensure_nat_if_needed("phase3_enrich_data.py")
    assert sent == [{"action": "create"}, {"action": "failover", "nat_id": "nat-a"}]


def test_a_second_no_egress_does_not_fail_over_again(task, monkeypatch):
    monkeypatch.setenv("NAT_READY_MAX_ATTEMPTS", "3")
    client, sent = _lambda_returning(
        {"status": "ready", "nat": "n"},
        {"status": "ready", "nat": "n2", "failover": "done"},
        {"status": "ready", "nat": "n2"},
    )
    monkeypatch.setattr(task.boto3, "client", lambda *a, **k: client)
    monkeypatch.setattr(task, "_egress_ok", lambda: False)
    monkeypatch.setattr(task, "_alert_phase_stopped", lambda *a, **k: None)
    monkeypatch.setattr(task, "_queue_followup", lambda *a: "")
    monkeypatch.setattr(task, "_remove_lock", lambda *a: None)
    with pytest.raises(SystemExit):
        task._ensure_nat_if_needed("phase3_enrich_data.py")
    assert [r["action"] for r in sent] == ["create", "failover", "create"]


def test_the_egress_probe_needs_only_one_host(task, monkeypatch):
    def get(url, timeout):
        if "amazonaws" in url:
            raise OSError("down")
        return mock.Mock(status_code=200)

    monkeypatch.setattr("requests.get", get)
    assert task._egress_ok() is True
    monkeypatch.setattr("requests.get", mock.Mock(side_effect=OSError("no route")))
    assert task._egress_ok(rounds=2) is False


def test_a_failover_request_that_waited_on_the_lock_is_sent_again(task, monkeypatch):
    """The lock-wait path does not act on a failover (no "failover" key): the task resends it."""
    client, sent = _lambda_returning(
        {"status": "ready", "nat": "nat-a"},
        {"status": "ready", "nat": "nat-a"},  # another instance held the lock
        {"status": "ready", "nat": "nat-b", "failover": "done"},
    )
    monkeypatch.setattr(task.boto3, "client", lambda *a, **k: client)
    probes = iter([False, False, True])
    monkeypatch.setattr(task, "_egress_ok", lambda: next(probes))
    task._ensure_nat_if_needed("phase3_enrich_data.py")
    assert [r["action"] for r in sent] == ["create", "failover", "failover"]


def test_a_non_az_error_is_not_blamed_on_the_az(env):
    table, alerts = env
    ec2 = FakeEc2(broken={A, B}, error="NatGatewayLimitExceeded")
    with pytest.raises(Exception, match="NatGatewayLimitExceeded"):
        nm._ensure_nat(ec2, table)
    assert nm.AZ_STATE_KEY not in table.items and alerts == []  # no false failover


def test_no_nat_in_any_az_is_alerted(env, monkeypatch):
    table, alerts = env
    ec2 = FakeEc2(broken={A, B})
    monkeypatch.setattr(nm, "_ensure_endpoints", lambda *a: None)
    monkeypatch.setattr(nm, "_await_other_instance", lambda *a: {})
    table.items.pop("lock#nat-manager", None)
    result = nm._create_all(ec2, "us-east-1")
    assert result["status"] == "not_ready"
    assert len(alerts) == 1 and alerts[0].startswith("Networking FAILED")
    assert "InsufficientFreeAddressesInSubnet" in alerts[0]


def test_running_out_of_lambda_time_is_not_an_az_failure(env, monkeypatch):
    table, _ = env
    ec2 = FakeEc2(never_available={A})
    monkeypatch.setattr(nm, "_DEADLINE", [nm.time.time() + 60])  # little time left
    with pytest.raises(nm.NatNotReady, match="still pending"):
        nm._ensure_nat(ec2, table)
    assert nm.AZ_STATE_KEY not in table.items  # not marked bad
    assert [n["SubnetId"] for n in ec2.live()] == [
        A
    ]  # the NAT is kept for the next call


def test_a_failover_whose_delete_fails_can_be_retried(env):
    table, _ = env
    ec2 = FakeEc2()
    nm._ensure_nat(ec2, table)
    first = ec2.live()[0]["NatGatewayId"]
    ec2.delete_fails = True
    with pytest.raises(nm.NatNotReady):
        nm._failover_nat(ec2, table, first)
    ec2.delete_fails = False
    assert nm._failover_nat(ec2, table, first) == "done"  # the claim was released


def test_a_failed_nat_is_counted_once_across_cold_starts(env, monkeypatch):
    table, _ = env
    ec2 = FakeEc2()
    ec2.nats["dead"] = {"NatGatewayId": "dead", "State": "failed", "SubnetId": A,
                        "CreateTime": datetime.now(timezone.utc)}  # fmt: skip
    nm._nat_info(ec2)
    first_until = table.items[nm.AZ_STATE_KEY]["bad"][A]["until"]
    monkeypatch.setattr(nm, "_failed_seen", set())  # a cold start
    monkeypatch.setattr(nm.time, "time", lambda: first_until + 10_000)
    nm._nat_info(ec2)
    assert table.items[nm.AZ_STATE_KEY]["bad"][A]["until"] == first_until


def test_a_malformed_az_state_does_not_stop_nat_creation(env):
    table, _ = env
    table.items[nm.AZ_STATE_KEY] = {
        "cache_key": nm.AZ_STATE_KEY,
        "bad": {A: "junk", B: {"until": "x"}},
    }
    assert nm._subnet_order(table.items[nm.AZ_STATE_KEY]) == [A, B]


def test_a_nat_that_becomes_available_as_the_waiter_gives_up_is_kept(env, monkeypatch):
    table, _ = env
    ec2 = FakeEc2(never_available={A})
    monkeypatch.setattr(nm, "_nat_state", lambda ec2_, nat_id: "available")
    nm._ensure_nat(ec2, table)
    assert [n["SubnetId"] for n in ec2.live()] == [A]
    assert nm.AZ_STATE_KEY not in table.items


def test_an_unknown_state_after_the_waiter_deletes_nothing(env, monkeypatch):
    table, _ = env
    ec2 = FakeEc2(never_available={A})
    monkeypatch.setattr(nm, "_nat_state", lambda ec2_, nat_id: "")
    with pytest.raises(nm.NatNotReady, match="unknown"):
        nm._ensure_nat(ec2, table)
    assert [n["SubnetId"] for n in ec2.live()] == [A]


def test_a_stuck_nat_that_cannot_be_deleted_is_not_doubled(env):
    table, _ = env
    ec2 = FakeEc2()
    ec2.nats["old"] = {"NatGatewayId": "old", "State": "pending", "SubnetId": A,
                       "CreateTime": datetime.now(timezone.utc) - timedelta(minutes=10),
                       "NatGatewayAddresses": []}  # fmt: skip
    ec2.delete_fails = True
    with pytest.raises(nm.NatNotReady, match="stuck"):
        nm._ensure_nat(ec2, table)
    assert [n["NatGatewayId"] for n in ec2.live()] == ["old"]  # no second NAT

"""Tests for the M4.3 dispatcher Lambda handlers (spec §17)."""

from unittest.mock import MagicMock, patch

from lambda_handlers import dispatcher_handlers as dh


def _table_with(items):
    t = MagicMock()
    t.scan.return_value = {"Items": items}
    return t


def test_enumerate_skips_done_and_in_flight():
    items = [
        {
            "cache_key": "doc#a",
            "status": "held_unprocessed",
            "next_phase": "phase1",
            "book": "A",
        },
        {"cache_key": "doc#b", "status": "done"},  # skip
        {"cache_key": "doc#c", "status": "extracted"},  # in-flight, skip
        {"cache_key": "doc#d", "status": "", "next_phase": "phase2", "book": "D"},
    ]
    with patch.object(dh, "_table", return_value=_table_with(items)):
        out = dh.enumerate_pending({}, None)
    ids = [i["doc_id"] for i in out["items"]]
    assert out["count"] == 2
    assert ids == ["a", "d"]  # FIFO sorted, done/in-flight excluded


def test_enumerate_routes_task_def_by_phase():
    items = [
        {
            "cache_key": "doc#x",
            "status": "held_unprocessed",
            "next_phase": "phase2",
            "book": "X",
        }
    ]
    with patch.object(dh, "_table", return_value=_table_with(items)):
        out = dh.enumerate_pending({}, None)
    assert out["items"][0]["task_def"] == f"{dh.ENV_NAME}-wwii-phase2-extract"


def test_clamp_pool_respects_quota_cap():
    fake_sq = MagicMock()
    fake_sq.get_service_quota.return_value = {"Quota": {"Value": 4.0}}  # 4 vCPU
    with patch.object(dh.boto3, "client", return_value=fake_sq):
        with patch.object(dh, "_PER_TASK_VCPU", 1):
            out = dh.clamp_pool({"pool_min": 2, "pool_max": 8}, None)
    # quota_cap = 4 vCPU / 1 = 4; effective = min(8, 4) floored at 2 => 4
    assert out["effective"] == 4
    assert out["quota_cap"] == 4


def test_clamp_pool_floor_at_pool_min():
    fake_sq = MagicMock()
    fake_sq.get_service_quota.return_value = {"Quota": {"Value": 1.0}}  # 1 vCPU
    with patch.object(dh.boto3, "client", return_value=fake_sq):
        with patch.object(dh, "_PER_TASK_VCPU", 1):
            out = dh.clamp_pool({"pool_min": 3, "pool_max": 8}, None)
    # quota_cap = 1; min(8,1)=1, but floor pool_min=3 => 3
    assert out["effective"] == 3


def test_clamp_pool_uses_pool_max_on_quota_error():
    fake_sq = MagicMock()
    fake_sq.get_service_quota.side_effect = RuntimeError("quota api down")
    with patch.object(dh.boto3, "client", return_value=fake_sq):
        out = dh.clamp_pool({"pool_min": 2, "pool_max": 6}, None)
    assert out["effective"] == 6  # falls back to pool_max


def test_clamp_pool_warns_near_quota_ceiling():
    """§5.0: warn (notify) when desired pool_max >= 80% of the quota-derived cap."""
    fake_sq = MagicMock()
    fake_sq.get_service_quota.return_value = {"Quota": {"Value": 8.0}}  # cap = 8
    with patch.object(dh.boto3, "client", return_value=fake_sq):
        with patch.object(dh, "_PER_TASK_VCPU", 1):
            with patch.object(dh, "_notify_quota_ceiling") as notify:
                out = dh.clamp_pool({"pool_min": 2, "pool_max": 8}, None)
    notify.assert_called_once()  # pool_max 8 >= 0.8*8 → warn
    assert out["effective"] == 8


def test_clamp_pool_no_warn_when_below_ceiling():
    fake_sq = MagicMock()
    fake_sq.get_service_quota.return_value = {"Quota": {"Value": 100.0}}  # cap = 100
    with patch.object(dh.boto3, "client", return_value=fake_sq):
        with patch.object(dh, "_PER_TASK_VCPU", 1):
            with patch.object(dh, "_notify_quota_ceiling") as notify:
                dh.clamp_pool({"pool_min": 2, "pool_max": 8}, None)
    notify.assert_not_called()  # 8 < 0.8*100 → no warn


def test_human_gate_store():
    table = MagicMock()
    with patch.object(dh, "_table", return_value=table):
        out = dh.human_gate(
            {"action": "store", "doc_id": "d1", "task_token": "tok"}, None
        )
    assert out == {"parked": "d1"}
    args = table.put_item.call_args.kwargs["Item"]
    assert args["cache_key"] == "gate#d1"
    assert args["task_token"] == "tok"


def test_human_gate_resume_sends_success():
    table = MagicMock()
    table.get_item.return_value = {"Item": {"task_token": "tok"}}
    sfn = MagicMock()
    with patch.object(dh, "_table", return_value=table):
        with patch.object(dh.boto3, "client", return_value=sfn):
            out = dh.human_gate({"action": "resume", "doc_id": "d1"}, None)
    assert out == {"resumed": "d1"}
    sfn.send_task_success.assert_called_once()
    table.delete_item.assert_called_once()


def test_human_gate_resume_no_token():
    table = MagicMock()
    table.get_item.return_value = {"Item": {}}
    with patch.object(dh, "_table", return_value=table):
        out = dh.human_gate({"action": "resume", "doc_id": "d1"}, None)
    assert out["error"] == "no token"

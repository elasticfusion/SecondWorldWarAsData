"""Batch results go to their own requests, and a dead launch never blocks a batch.

2026-11-11, Cross-Channel Attack: xAI returned the 99 results of a batch in a different order
from the submission (1 of 99 in place) while echoing our custom_id as each result's
batch_request_id. Both batch paths mapped by POSITION, so with the Grok cache on every answer was
stored under another request's key (chapter 8's events slot held a units answer). And the poller
claimed the retrieve for 6 h, then timed out waiting for the NAT before launching it: the finished
batch sat unretrieved for 6 h."""

import json
import os
import time
from unittest import mock

import boto3
import pytest
from moto import mock_aws

from src.utils.batch_api import BatchResult, align_by_request_id

SUBMITTED = [("id-a", "dates"), ("id-b", "places"), ("id-c", "events")]


def _br(rid, content):
    return BatchResult(request_id=rid, content=content, finish_reason="stop")


def test_results_returned_out_of_order_go_to_their_own_requests():
    shuffled = [_br("id-c", "EVENTS"), _br("id-a", "DATES"), _br("id-b", "PLACES")]
    aligned = align_by_request_id(SUBMITTED, shuffled, batch_id="b")
    assert [(r.request_id, r.content) for r in aligned] == [
        ("id-a", "DATES"),
        ("id-b", "PLACES"),
        ("id-c", "EVENTS"),
    ]


def test_a_request_without_a_result_is_only_that_request_failing():
    aligned = align_by_request_id(
        SUBMITTED, [_br("id-c", "EVENTS"), _br("id-a", "DATES")], batch_id="b"
    )
    assert [r.status for r in aligned] == ["valid", "error", "valid"]
    assert aligned[1].request_id == "id-b"


def test_server_assigned_ids_keep_the_old_positional_order():
    legacy = [_br("srv-1", "DATES"), _br("srv-2", "PLACES"), _br("srv-3", "EVENTS")]
    assert align_by_request_id(SUBMITTED, legacy) == legacy


@pytest.mark.parametrize(
    "results",
    [
        [_br("id-a", "x"), _br("srv-2", "y"), _br("id-c", "z")],  # mixed
        [_br("id-a", "x"), _br("id-a", "y"), _br("id-c", "z")],  # repeated
    ],
)
def test_untrustworthy_ids_fail_every_request_rather_than_guess(results):
    aligned = align_by_request_id(SUBMITTED, results)
    assert [r.request_id for r in aligned] == ["id-a", "id-b", "id-c"]
    assert {r.status for r in aligned} == {"error"}


def test_the_retrieve_path_caches_each_answer_under_its_own_key():
    os.environ.setdefault("S3_BUCKET", "test-bucket")
    import ecs_entrypoint as ep

    shuffled = [
        _br("id-c", '{"Event": 1}'),
        _br("id-a", '{"dates": 1}'),
        _br("id-b", '{"places": 1}'),
    ]
    mapped = ep._map_batch_results_by_index(SUBMITTED, shuffled, "b")
    assert mapped == {
        "dates": {"id-a": '{"dates": 1}'},
        "places": {"id-b": '{"places": 1}'},
        "events": {"id-c": '{"Event": 1}'},
    }


def test_the_in_task_path_caches_each_answer_under_its_own_key(tmp_path, monkeypatch):
    """The real GrokClient.submit_batch, with the API returning results out of order."""
    import src.grok_client as gc
    from src.utils.cache_backend import DiskCacheBackend

    monkeypatch.setenv("GROK_API_KEY", "test-not-a-key")
    monkeypatch.delenv("GROK_CACHE_DISABLED", raising=False)
    monkeypatch.setenv("BATCH_STATS", "0")
    client = gc.GrokClient(DiskCacheBackend(tmp_path / "c"), batch_mode=True)
    client.cache_dir = tmp_path / "c"
    for prompt, kind in (("P-dates", "dates"), ("P-places", "places")):
        with pytest.raises(gc.BatchModeCollecting):
            client.extract_json(prompt=prompt, cache_type=kind)
    ids = [r.request_id for r in client._batch_collector.requests]
    reversed_results = [_br(ids[1], '{"places": [1]}'), _br(ids[0], '{"dates": [1]}')]
    with (
        mock.patch("src.utils.batch_api.submit_batch", return_value="batch_t"),
        mock.patch("src.utils.batch_api.poll_batch", return_value={"state": {}}),
        mock.patch(
            "src.utils.batch_api.retrieve_results_ordered",
            return_value=reversed_results,
        ),
        mock.patch("src.grok_client._save_metrics"),
    ):
        client.submit_batch()
    assert client._get_cache("dates")[ids[0]] == '{"dates": [1]}'
    assert client._get_cache("places")[ids[1]] == '{"places": [1]}'


# --- the poller ------------------------------------------------------------------------------


@pytest.fixture
def poller(monkeypatch):
    monkeypatch.setenv("CACHE_TABLE", "test-wwii-api-cache")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    monkeypatch.setenv("ENV_NAME", "test")
    with mock_aws():
        boto3.client("dynamodb", region_name="us-east-1").create_table(
            TableName="test-wwii-api-cache",
            KeySchema=[{"AttributeName": "cache_key", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "cache_key", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )
        import lambda_handlers.batch_poller as bp

        monkeypatch.setattr(bp, "CACHE_TABLE", "test-wwii-api-cache")
        yield bp


def _claim(bp, batch_id="b"):
    t = boto3.resource("dynamodb", region_name="us-east-1").Table("test-wwii-api-cache")
    return t.get_item(Key={"cache_key": f"retrieve#{batch_id}"}).get("Item")


def test_a_launch_that_died_does_not_block_the_batch_for_hours(poller, monkeypatch):
    assert poller._claim_retrieve("b") is True
    assert poller._claim_retrieve("b") is False  # a launch in progress is respected
    claimed = int(_claim(poller)["ttl"])
    assert (
        claimed <= time.time() + poller._LAUNCH_CLAIM_S
    )  # short until the task starts
    # the launching Lambda timed out: once the short claim has expired, a later poll takes over
    # (even though DynamoDB has not deleted the expired item yet)
    real = time.time
    monkeypatch.setattr(time, "time", lambda: real() + poller._LAUNCH_CLAIM_S + 1)
    assert poller._claim_retrieve("b") is True


def test_a_started_retrieve_holds_its_claim_for_its_run(poller):
    poller._claim_retrieve("b")
    poller._hold_retrieve("b")
    assert int(_claim(poller)["ttl"]) > time.time() + poller._LAUNCH_CLAIM_S


def test_no_second_nat_wait_when_the_manager_reports_ready(poller, monkeypatch):
    lam = mock.Mock()
    lam.invoke.return_value = {
        "Payload": mock.Mock(read=lambda: b'{"status": "ready"}')
    }
    ecs = mock.Mock()
    ecs.run_task.return_value = {"tasks": [{"taskArn": "arn:task/1"}], "failures": []}
    clients = {"lambda": lam, "ecs": ecs}
    monkeypatch.setattr(poller.boto3, "client", lambda name, **k: clients[name])
    monkeypatch.setattr(poller, "_get_network_config", lambda: {})
    monkeypatch.setattr(poller, "_notify", lambda m: None)
    wait = mock.Mock()
    monkeypatch.setattr(poller, "_wait_for_nat", wait)
    assert poller._trigger_retrieve({"batch_id": "b", "phase": "phase2", "book": "B"})
    wait.assert_not_called()
    assert int(_claim(poller)["ttl"]) > time.time() + poller._LAUNCH_CLAIM_S


def test_the_nat_wait_looks_for_the_nat_the_manager_creates(poller, monkeypatch):
    ec2 = mock.Mock()
    ec2.describe_nat_gateways.return_value = {"NatGateways": [{"State": "available"}]}
    monkeypatch.setattr(poller.boto3, "client", lambda name, **k: ec2)
    poller._wait_for_nat(30)
    filters = ec2.describe_nat_gateways.call_args.kwargs["Filters"]
    assert {"Name": "tag:Name", "Values": [f"{poller.ENV_NAME}-nat"]} in filters


@pytest.mark.parametrize(
    "returned",
    [
        [],  # nothing came back
        [_br("id-a", "DATES")],  # one of three
        [_br("id-a", "x"), _br("id-a", "y"), _br("id-c", "z")],  # repeated: unalignable
    ],
)
def test_results_filled_in_by_alignment_force_the_retry(returned):
    from src.utils.batch_api import alignment_degraded

    assert alignment_degraded(align_by_request_id(SUBMITTED, returned))


def test_a_complete_reordered_batch_forces_nothing():
    from src.utils.batch_api import alignment_degraded

    shuffled = [_br("id-c", "E"), _br("id-a", "D"), _br("id-b", "P")]
    assert not alignment_degraded(align_by_request_id(SUBMITTED, shuffled))


def test_the_in_task_path_retries_requests_the_api_did_not_return(
    tmp_path, monkeypatch
):
    import src.grok_client as gc
    from src.utils.cache_backend import DiskCacheBackend

    monkeypatch.setenv("GROK_API_KEY", "test-not-a-key")
    monkeypatch.delenv("GROK_CACHE_DISABLED", raising=False)
    monkeypatch.setenv("BATCH_STATS", "0")
    client = gc.GrokClient(DiskCacheBackend(tmp_path / "c"), batch_mode=True)
    client.cache_dir = tmp_path / "c"
    for prompt, kind in (("P-1", "dates"), ("P-2", "places"), ("P-3", "people")):
        with pytest.raises(gc.BatchModeCollecting):
            client.extract_json(prompt=prompt, cache_type=kind)
    ids = [r.request_id for r in client._batch_collector.requests]
    with (
        mock.patch("src.utils.batch_api.submit_batch", return_value="batch_t"),
        mock.patch("src.utils.batch_api.poll_batch", return_value={"state": {}}),
        mock.patch(
            "src.utils.batch_api.retrieve_results_ordered",
            return_value=[
                _br(ids[0], '{"dates": [1]}')
            ],  # 2 of 3 missing (67% > 20% guard)
        ),
        mock.patch("src.grok_client._save_metrics"),
        mock.patch.object(gc.GrokClient, "_retry_failed_batch") as retry,
    ):
        client.submit_batch()
    assert sorted(retry.call_args.args[0]) == sorted(ids[1:])
    assert retry.call_args.kwargs["force"] is True


def test_a_claim_that_cannot_be_held_is_reported(poller, monkeypatch):
    broken = mock.Mock()
    broken.Table.return_value.update_item.side_effect = RuntimeError("ddb")
    monkeypatch.setattr(poller.boto3, "resource", lambda *a, **k: broken)
    monkeypatch.setattr(time, "sleep", lambda s: None)
    alerts = []
    monkeypatch.setattr(poller, "observe_error", lambda *a, **k: alerts.append(a[:2]))
    assert poller._hold_retrieve("b") is False
    assert broken.Table.return_value.update_item.call_count == 3
    assert alerts == [("batch", "retrieve_claim_not_held")]

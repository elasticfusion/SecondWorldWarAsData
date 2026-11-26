"""Tests for the OCR SPOT controller (Option B: spot-retry / 48h on-demand cap)."""

import os
import time
from unittest.mock import MagicMock, patch

import boto3
import pytest
from moto import mock_aws

os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
os.environ.setdefault("CACHE_TABLE", "test-wwii-api-cache")
os.environ.setdefault("ENV_NAME", "test")
os.environ.setdefault("NOTIFICATION_TOPIC_ARN", "")

from lambda_handlers import ocr_spot_controller as ctl


@pytest.fixture
def table():
    with mock_aws():
        c = boto3.client("dynamodb", region_name="us-east-1")
        c.create_table(
            TableName="test-wwii-api-cache",
            KeySchema=[{"AttributeName": "cache_key", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "cache_key", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )
        yield boto3.resource("dynamodb", region_name="us-east-1").Table(
            "test-wwii-api-cache"
        )


def test_resubmit_to_ondemand_moves_once(table):
    batch = MagicMock()
    batch.describe_jobs.return_value = {
        "jobs": [
            {
                "container": {
                    "command": ["s3://in.pdf", "s3://out/", "--page-range", "1-50"]
                }
            }
        ]
    }
    batch.submit_job.return_value = {"jobId": "od-1"}
    job = {"jobName": "chandra-Big-p1-50", "jobId": "spot-1"}
    with patch.object(ctl, "_batch", return_value=batch):
        ctl._resubmit_to_ondemand(job)
        ctl._resubmit_to_ondemand(job)  # second call must be a no-op (already moved)
    # submitted to on-demand exactly once + terminated the spot job
    assert batch.submit_job.call_count == 1
    assert batch.submit_job.call_args.kwargs["jobQueue"].endswith("-ondemand")
    batch.terminate_job.assert_called_once()
    rec = table.get_item(Key={"cache_key": "ocrctl#chandra-Big-p1-50"})["Item"]
    assert rec["ondemand_job_id"] == "od-1"


def test_stale_claim_from_terminal_prior_job_is_reclaimed(table):
    """A leftover ocrctl# claim from a PRIOR (terminal) job of the same name must
    NOT block a freshly-submitted job — it is reclaimed and the new job routed.
    (Regression: stale claim silently no-op'd every hourly re-route.)"""
    # Pre-seed a stale claim pointing at an old job id.
    table.put_item(
        Item={
            "cache_key": "ocrctl#chandra-B400",
            "spot_job_id": "old-spot-job",
            "ondemand_started_at": 1,
        }
    )
    batch = MagicMock()

    def _describe(jobs):
        jid = jobs[0]
        status = "SUCCEEDED" if jid == "old-spot-job" else "RUNNABLE"
        return {"jobs": [{"status": status, "container": {"command": ["s3://in"]}}]}

    batch.describe_jobs.side_effect = _describe
    batch.submit_job.return_value = {"jobId": "od-new"}
    new_job = {"jobName": "chandra-B400", "jobId": "new-spot-job"}
    with patch.object(ctl, "_batch", return_value=batch):
        moved = ctl._resubmit_to_ondemand(new_job)
    assert moved is True
    assert batch.submit_job.call_count == 1
    rec = table.get_item(Key={"cache_key": "ocrctl#chandra-B400"})["Item"]
    assert rec["spot_job_id"] == "new-spot-job"
    assert rec["ondemand_job_id"] == "od-new"


def test_claim_held_by_live_prior_job_blocks(table):
    """If the prior claim's job is still LIVE (not terminal), do not re-route."""
    table.put_item(
        Item={
            "cache_key": "ocrctl#chandra-C",
            "spot_job_id": "live-old",
            "ondemand_started_at": 1,
        }
    )
    batch = MagicMock()
    batch.describe_jobs.return_value = {"jobs": [{"status": "RUNNING"}]}
    with patch.object(ctl, "_batch", return_value=batch):
        moved = ctl._resubmit_to_ondemand({"jobName": "chandra-C", "jobId": "new-C"})
    assert moved is False
    batch.submit_job.assert_not_called()


def test_route_spot_starved_counts_only_actual_moves(table):
    """`routed` must count jobs ACTUALLY moved, not attempts (the misleading
    metric that hid the stale-claim no-op)."""
    import time as _t

    old = int((_t.time() - 7200) * 1000)  # created 2h ago -> past SPOT_WAIT
    batch = MagicMock()
    paginator = MagicMock()
    paginator.paginate.return_value = [
        {"jobSummaryList": [{"jobName": "chandra-Z", "jobId": "z1", "createdAt": old}]}
    ]
    batch.get_paginator.return_value = paginator
    with (
        patch.object(ctl, "_batch", return_value=batch),
        patch.object(ctl, "_resubmit_to_ondemand", return_value=False),
    ):
        assert ctl._route_spot_starved() == 0  # attempt made but not moved
    with (
        patch.object(ctl, "_batch", return_value=batch),
        patch.object(ctl, "_resubmit_to_ondemand", return_value=True),
    ):
        assert ctl._route_spot_starved() == 1


def test_flag_oom_to_review_once(table):
    """A FAILED job with watchdog exit 76 (OOM) is alerted/flagged for review
    once (24GB is the floor, so no auto-retry); a second pass is a no-op."""
    batch = MagicMock()

    def _list_jobs(jobQueue, jobStatus):  # noqa: N803
        if jobQueue.endswith("-chandra-gpu") and jobStatus == "FAILED":
            return {"jobSummaryList": [{"jobName": "chandra-B406", "jobId": "f-1"}]}
        return {"jobSummaryList": []}

    paginator = MagicMock()
    paginator.paginate.side_effect = lambda jobQueue, jobStatus: [
        _list_jobs(jobQueue, jobStatus)
    ]
    batch.get_paginator.return_value = paginator
    batch.describe_jobs.return_value = {
        "jobs": [
            {"container": {"exitCode": 76, "command": ["s3://in.pdf", "s3://out/"]}}
        ]
    }
    with patch.object(ctl, "_batch", return_value=batch):
        n1 = ctl._flag_oom_failures()
        n2 = ctl._flag_oom_failures()
    assert n1 == 1 and n2 == 0
    # flagged for review — NOT resubmitted to any queue
    batch.submit_job.assert_not_called()
    rec = table.get_item(Key={"cache_key": "ocrctl#oom#chandra-B406"})["Item"]
    assert "flagged_at" in rec


def test_non_oom_failure_not_flagged(table):
    """A FAILED job with a non-OOM exit code is left alone."""
    batch = MagicMock()
    paginator = MagicMock()
    paginator.paginate.side_effect = lambda jobQueue, jobStatus: [
        (
            {"jobSummaryList": [{"jobName": "chandra-X", "jobId": "f-2"}]}
            if jobStatus == "FAILED" and jobQueue.endswith("-chandra-gpu")
            else {"jobSummaryList": []}
        )
    ]
    batch.get_paginator.return_value = paginator
    batch.describe_jobs.return_value = {"jobs": [{"container": {"exitCode": 1}}]}
    with patch.object(ctl, "_batch", return_value=batch):
        assert ctl._flag_oom_failures() == 0
    batch.submit_job.assert_not_called()


def test_route_spot_starved_only_old_runnable(table):
    now_ms = int(time.time() * 1000)
    batch = MagicMock()
    # one old (2h) RUNNABLE, one fresh (1min)
    with (
        patch.object(ctl, "_batch", return_value=batch),
        patch.object(
            ctl,
            "_list_jobs",
            return_value=[
                {
                    "jobName": "old",
                    "jobId": "s1",
                    "createdAt": now_ms - 2 * 3600 * 1000,
                },
                {"jobName": "fresh", "jobId": "s2", "createdAt": now_ms - 60 * 1000},
            ],
        ),
        patch.object(ctl, "_resubmit_to_ondemand") as resub,
    ):
        n = ctl._route_spot_starved()
    assert n == 1  # only the >1h one routed
    resub.assert_called_once()
    assert resub.call_args[0][0]["jobName"] == "old"


def test_enforce_ondemand_cap_terminates_past_48h(table):
    now = int(time.time())
    table.put_item(
        Item={
            "cache_key": "ocrctl#stuck",
            "ondemand_job_id": "od-9",
            "ondemand_started_at": now - 49 * 3600,  # 49h ago
        }
    )
    table.put_item(
        Item={
            "cache_key": "ocrctl#ok",
            "ondemand_job_id": "od-10",
            "ondemand_started_at": now - 2 * 3600,  # 2h ago
        }
    )
    batch = MagicMock()
    with patch.object(ctl, "_batch", return_value=batch), patch.object(ctl, "_notify"):
        capped = ctl._enforce_ondemand_cap()
    assert capped == 1
    batch.terminate_job.assert_called_once_with(
        jobId="od-9", reason="48h on-demand cap (§2)"
    )


def test_warn_near_quota_fires_at_80pct(table):
    # quota 64 vCPU; 16 running spot jobs * 4 = 64 vCPU = 100% >= 80% -> warn
    with (
        patch.object(ctl, "_spot_quota_vcpus", return_value=64),
        patch.object(
            ctl, "_list_jobs", return_value=[{"jobName": f"j{i}"} for i in range(16)]
        ),
        patch.object(ctl, "_notify") as notify,
    ):
        ctl._warn_if_near_quota()
    notify.assert_called_once()


def test_warn_near_quota_silent_below_80pct(table):
    with (
        patch.object(ctl, "_spot_quota_vcpus", return_value=64),
        patch.object(
            ctl, "_list_jobs", return_value=[{"jobName": "j"}]  # 1 job * 4 = 4 vCPU
        ),
        patch.object(ctl, "_notify") as notify,
    ):
        ctl._warn_if_near_quota()
    notify.assert_not_called()


# --- task 1.1d: the Spot price ladder -------------------------------------------------------

from src.utils import gpu_ladder  # noqa: E402

_TYPES = ["g5.xlarge", "g6.xlarge", "g5.2xlarge", "g6.2xlarge"]
_QUEUES = gpu_ladder.queue_names("test")


@pytest.fixture
def ladder(monkeypatch):
    """The 4-rung ladder with the price lookups faked: g6.xlarge $0.40, g5.2xlarge $2.00 (over the
    cap), g6.2xlarge unpriced; the original type's on-demand price is $1.00."""
    monkeypatch.setattr(ctl, "LADDER", _TYPES)
    monkeypatch.setattr(ctl, "SPOT_QUEUES", _QUEUES)
    prices = {"g6.xlarge": 0.4, "g5.2xlarge": 2.0, "g6.2xlarge": None}
    shape = gpu_ladder.Shape(4, 16384, 1, 23028)
    monkeypatch.setattr(gpu_ladder, "shape_of", lambda ec2, t: shape)
    monkeypatch.setattr(gpu_ladder, "spot_hourly", lambda ec2, t, azs=None: prices[t])
    monkeypatch.setattr(gpu_ladder, "load_on_demand", lambda *a, **k: 1.0)
    monkeypatch.setattr(ctl, "_azs", lambda: ["us-east-1a"])
    monkeypatch.setattr(ctl.boto3, "client", lambda *a, **k: MagicMock())
    return prices


def _batch_mock():
    batch = MagicMock()
    batch.describe_jobs.return_value = {
        "jobs": [
            {
                "status": "FAILED",  # every earlier job of a chain is terminal
                "container": {"command": ["s3://in.pdf", "s3://out/"]},
            }
        ]
    }
    batch.submit_job.return_value = {"jobId": "new-1"}
    return batch


def _reclaimed(name="chandra-Big", jid="j1"):
    return {
        "jobName": name,
        "jobId": jid,
        "status": "FAILED",
        "statusReason": "Host EC2 (instance i-0abc) terminated.",
    }


def test_reclaim_on_rung0_moves_to_rung1_when_priced_under_the_cap(table, ladder):
    batch = _batch_mock()
    with (
        patch.object(ctl, "_batch", return_value=batch),
        patch.object(ctl, "_notify") as notify,
    ):
        assert ctl._handle_reclaim(_reclaimed(), _QUEUES[0])
    assert batch.submit_job.call_args.kwargs["jobQueue"] == _QUEUES[1]
    batch.terminate_job.assert_not_called()  # already FAILED
    notify.assert_not_called()  # Spot to Spot costs no more: logged only
    assert "retryStrategy" not in batch.submit_job.call_args.kwargs
    rec = table.get_item(Key={"cache_key": "ocrctl#chandra-Big"})["Item"]
    assert rec["rung"] == 1


def test_reclaim_is_handled_once_event_then_sweep(table, ladder):
    batch = _batch_mock()
    job = _reclaimed()
    with patch.object(ctl, "_batch", return_value=batch), patch.object(ctl, "_notify"):
        assert ctl._handle_reclaim(job, _QUEUES[0])
        assert not ctl._handle_reclaim(job, _QUEUES[0])
    assert batch.submit_job.call_count == 1


def test_reclaim_on_rung1_skips_over_cap_and_unpriced_rungs_to_ondemand(table, ladder):
    batch = _batch_mock()
    with (
        patch.object(ctl, "_batch", return_value=batch),
        patch.object(ctl, "_notify") as notify,
    ):
        assert ctl._handle_reclaim(_reclaimed(), _QUEUES[1])
    assert batch.submit_job.call_args.kwargs["jobQueue"].endswith("-ondemand")
    msg = notify.call_args.args[1]
    assert "over $1.0000/h" in msg and "no Spot price" in msg
    assert "g5.xlarge on-demand" in msg  # the ORIGINAL type
    rec = table.get_item(Key={"cache_key": "ocrctl#chandra-Big"})["Item"]
    assert "ondemand_started_at" in rec


def test_reclaim_when_prices_unreadable_goes_ondemand_at_the_original_type(
    table, ladder, monkeypatch
):
    def boom(*a, **k):
        raise RuntimeError("pricing down")

    monkeypatch.setattr(gpu_ladder, "load_on_demand", boom)
    batch = _batch_mock()
    with patch.object(ctl, "_batch", return_value=batch), patch.object(ctl, "_notify"):
        assert ctl._handle_reclaim(_reclaimed(), _QUEUES[0])
    assert batch.submit_job.call_args.kwargs["jobQueue"].endswith("-ondemand")


def test_non_reclaim_failure_and_foreign_queue_are_left_alone(table, ladder):
    batch = _batch_mock()
    job = {
        "jobName": "chandra-X",
        "jobId": "j2",
        "statusReason": "Essential container exited",
    }
    with patch.object(ctl, "_batch", return_value=batch):
        assert not ctl._handle_reclaim(job, _QUEUES[0])
        assert not ctl._handle_reclaim(_reclaimed(), "test-other-queue")
        assert not ctl._handle_reclaim(_reclaimed(), "test-wwii-chandra-gpu-ondemand")
    batch.submit_job.assert_not_called()


def test_reclaim_found_only_in_an_attempt_reason_counts(table, ladder):
    job = {
        "jobName": "n",
        "jobId": "j",
        "attempts": [{"statusReason": "Host EC2 terminated"}],
    }
    assert ctl._is_reclaim(job)
    assert not ctl._is_reclaim({"statusReason": "OutOfMemory"})


def test_failed_event_moves_a_reclaimed_ladder_job(table, ladder):
    batch = _batch_mock()
    batch.describe_jobs.return_value = {
        "jobs": [
            {
                **_reclaimed(),
                "status": "FAILED",
                "jobQueue": f"arn:aws:batch:us-east-1:1:job-queue/{_QUEUES[0]}",
                "container": {"command": ["s3://in.pdf"]},
            }
        ]
    }
    detail = {"jobId": "j1"}
    with patch.object(ctl, "_batch", return_value=batch), patch.object(ctl, "_notify"):
        out = ctl.handler({"source": "aws.batch", "detail": detail}, None)
    assert out == {"action": "moved", "job": "chandra-Big"}
    assert batch.submit_job.call_args.kwargs["jobQueue"] == _QUEUES[1]


def test_failed_event_for_another_queue_is_ignored(table, ladder):
    batch = _batch_mock()
    batch.describe_jobs.return_value = {
        "jobs": [
            {
                **_reclaimed(),
                "status": "FAILED",
                "jobQueue": "arn:aws:batch:us-east-1:1:job-queue/other",
            }
        ]
    }
    with patch.object(ctl, "_batch", return_value=batch):
        out = ctl.handler({"source": "aws.batch", "detail": {"jobId": "j1"}}, None)
    assert out["action"] == "none"
    batch.submit_job.assert_not_called()


def test_failed_event_for_a_job_that_is_not_failed_is_ignored(table, ladder):
    batch = _batch_mock()
    batch.describe_jobs.return_value = {
        "jobs": [{**_reclaimed(), "status": "RUNNING", "jobQueue": _QUEUES[0]}]
    }
    with patch.object(ctl, "_batch", return_value=batch):
        out = ctl.handler({"source": "aws.batch", "detail": {"jobId": "j1"}}, None)
    assert out["reason"] == "not a FAILED job"
    batch.submit_job.assert_not_called()
    batch.describe_jobs.return_value = {"jobs": []}
    with patch.object(ctl, "_batch", return_value=batch):
        assert (
            ctl.handler({"source": "aws.batch", "detail": {"jobId": "j1"}}, None)[
                "action"
            ]
            == "none"
        )


def test_chain_of_moves_then_sweep_never_moves_an_old_job_again(table, ladder):
    """A reclaimed on rung 0 -> B on rung 1; B reclaimed -> C on rung 1's next; the hourly sweep
    still lists A as FAILED: A (and a duplicate event for B) must not be resubmitted."""
    batch = _batch_mock()
    batch.submit_job.side_effect = [{"jobId": "B"}, {"jobId": "C"}]
    a, b = _reclaimed(jid="A"), _reclaimed(jid="B")
    with patch.object(ctl, "_batch", return_value=batch), patch.object(ctl, "_notify"):
        assert ctl._handle_reclaim(a, _QUEUES[0])
        assert ctl._handle_reclaim(b, _QUEUES[1])
        assert not ctl._handle_reclaim(a, _QUEUES[0])  # the sweep sees A again
        assert not ctl._handle_reclaim(b, _QUEUES[1])  # a duplicate event for B
    assert batch.submit_job.call_count == 2


def test_ondemand_move_keeps_the_plain_retry_and_alerts_with_an_action(table, ladder):
    batch = _batch_mock()
    with (
        patch.object(ctl, "_batch", return_value=batch),
        patch.object(ctl, "_notify") as notify,
    ):
        assert ctl._handle_reclaim(_reclaimed(), _QUEUES[3])  # last rung: nothing above
    kw = batch.submit_job.call_args.kwargs
    assert kw["jobQueue"].endswith("-ondemand") and kw["retryStrategy"] == {
        "attempts": 2
    }
    assert "GpuRung" in notify.call_args.args[1]


def test_failed_submit_releases_the_claim_but_a_late_failure_keeps_it(table, ladder):
    batch = _batch_mock()
    batch.submit_job.side_effect = RuntimeError("throttled")
    with patch.object(ctl, "_batch", return_value=batch), patch.object(ctl, "_notify"):
        assert not ctl._handle_reclaim(_reclaimed(), _QUEUES[0])
    assert "Item" not in table.get_item(Key={"cache_key": "ocrctl#chandra-Big"})
    batch = _batch_mock()
    batch.terminate_job.side_effect = RuntimeError("late")
    job = {**_reclaimed(), "jobName": "chandra-Late"}
    with patch.object(ctl, "_batch", return_value=batch), patch.object(ctl, "_notify"):
        assert ctl._move_job(
            job, 0, "starved", terminate=True
        )  # submitted, terminate failed
        assert not ctl._move_job(job, 0, "starved", terminate=True)
    assert batch.submit_job.call_count == 1
    assert "Item" in table.get_item(Key={"cache_key": "ocrctl#chandra-Late"})


def test_runnable_job_on_a_rung_whose_price_rose_over_the_cap_moves_at_once(
    table, ladder
):
    ladder["g6.xlarge"] = 3.0  # rung 1 now over the $1.00 cap
    ladder["g5.2xlarge"] = 0.5
    batch = _batch_mock()
    job = {"jobName": "chandra-W", "jobId": "jw", "createdAt": int(time.time()) * 1000}
    with (
        patch.object(ctl, "_batch", return_value=batch),
        patch.object(
            ctl,
            "_list_jobs",
            side_effect=lambda q, s, **f: [job] if q == _QUEUES[1] else [],
        ),
        patch.object(ctl, "_notify"),
    ):
        assert ctl._route_spot_starved() == 1
    assert batch.submit_job.call_args.kwargs["jobQueue"] == _QUEUES[2]
    batch.terminate_job.assert_called_once()


def test_original_type_check_reports_an_unfit_type_and_an_unknown_price(
    ladder, monkeypatch, caplog
):
    monkeypatch.setattr(
        gpu_ladder, "shape_of", lambda ec2, t: gpu_ladder.Shape(4, 16384, 1, 15360)
    )
    monkeypatch.setattr(gpu_ladder, "load_on_demand", lambda *a, **k: None)
    with patch.object(ctl, "_table", return_value=MagicMock()):
        ctl._check_original_type()
    text = caplog.text
    assert "ocr.original_type_unfit" in text and "GpuRung0Type" in text
    assert "ocr.original_price_unknown" in text


def test_sweep_moves_reclaimed_jobs_from_every_rung(table, ladder):
    batch = _batch_mock()
    listed = {
        _QUEUES[0]: [_reclaimed("a", "ja")],
        _QUEUES[1]: [{"jobName": "b", "jobId": "jb", "statusReason": "OOM"}],
    }
    with (
        patch.object(ctl, "_batch", return_value=batch),
        patch.object(
            ctl, "_list_jobs", side_effect=lambda q, s, **f: listed.get(q, [])
        ),
        patch.object(ctl, "_notify"),
    ):
        assert ctl._sweep_reclaims() == 1
    assert batch.submit_job.call_args.kwargs["jobName"] == "a"


def test_starved_job_climbs_to_a_spot_rung_without_an_alert(table, ladder):
    batch = _batch_mock()
    old = (int(time.time()) - 2 * 3600) * 1000
    job = {"jobName": "chandra-S", "jobId": "js", "createdAt": old}
    with (
        patch.object(ctl, "_batch", return_value=batch),
        patch.object(
            ctl,
            "_list_jobs",
            side_effect=lambda q, s, **f: [job] if q == _QUEUES[0] else [],
        ),
        patch.object(ctl, "_notify") as notify,
    ):
        assert ctl._route_spot_starved() == 1
    assert batch.submit_job.call_args.kwargs["jobQueue"] == _QUEUES[1]
    batch.terminate_job.assert_called_once()  # it was still RUNNABLE
    notify.assert_not_called()  # Spot to Spot: logged only


def test_starved_job_that_is_not_old_enough_stays(table, ladder):
    job = {"jobName": "c", "jobId": "jc", "createdAt": int(time.time()) * 1000}
    with (
        patch.object(ctl, "_batch", return_value=_batch_mock()),
        patch.object(
            ctl,
            "_list_jobs",
            side_effect=lambda q, s, **f: [job] if q == _QUEUES[0] else [],
        ),
    ):
        assert ctl._route_spot_starved() == 0


def test_list_jobs_tolerates_a_queue_that_does_not_exist():
    from botocore.exceptions import ClientError

    batch = MagicMock()
    batch.get_paginator.return_value.paginate.side_effect = ClientError(
        {"Error": {"Code": "ClientException", "Message": "Job queue x does not exist"}},
        "ListJobs",
    )
    with patch.object(ctl, "_batch", return_value=batch):
        assert ctl._list_jobs("x", "RUNNABLE") == []
    batch.get_paginator.return_value.paginate.side_effect = ClientError(
        {"Error": {"Code": "AccessDenied", "Message": "no"}}, "ListJobs"
    )
    with patch.object(ctl, "_batch", return_value=batch), pytest.raises(ClientError):
        ctl._list_jobs("x", "RUNNABLE")


def test_stale_overwrite_is_compare_and_set(table, ladder):
    """Two handlers read the same stale claim: only one may move the job."""
    table.put_item(
        Item={
            "cache_key": "ocrctl#chandra-Big",
            "spot_job_id": "A",
            "moved_from": {"A"},
        }
    )
    batch = _batch_mock()
    real_get = ctl._table().get_item

    class Racing:
        """A table whose get_item lets a rival handler win between the read and the overwrite."""

        def __init__(self, inner):
            self._t = inner
            self.meta = inner.meta

        def get_item(self, **kw):
            out = real_get(**kw)
            self._t.update_item(
                Key=kw["Key"],
                UpdateExpression="SET spot_job_id = :r",
                ExpressionAttributeValues={":r": "RIVAL"},
            )
            return out

        def __getattr__(self, name):
            return getattr(self._t, name)

    real = ctl._table()
    with (
        patch.object(ctl, "_batch", return_value=batch),
        patch.object(ctl, "_table", return_value=Racing(real)),
    ):
        assert ctl._claim_move(_reclaimed(jid="B"), "ocrctl#chandra-Big", False) is None


def test_failed_submit_after_a_stale_overwrite_restores_the_prior_claim(table, ladder):
    table.put_item(
        Item={
            "cache_key": "ocrctl#chandra-Big",
            "spot_job_id": "A",
            "moved_from": {"A"},
            "moves": 1,
        }
    )
    batch = _batch_mock()
    batch.submit_job.side_effect = RuntimeError("throttled")
    with patch.object(ctl, "_batch", return_value=batch), patch.object(ctl, "_notify"):
        assert not ctl._handle_reclaim(_reclaimed(jid="B"), _QUEUES[1])
    row = table.get_item(Key={"cache_key": "ocrctl#chandra-Big"})["Item"]
    assert row["spot_job_id"] == "A" and row["moved_from"] == {
        "A"
    }  # A is still never re-moved
    # a describe_jobs failure releases the claim the same way
    batch = _batch_mock()
    batch.describe_jobs.side_effect = RuntimeError("throttled")
    with patch.object(ctl, "_batch", return_value=batch), patch.object(ctl, "_notify"):
        assert not ctl._handle_reclaim(_reclaimed(jid="B"), _QUEUES[1])
    assert (
        table.get_item(Key={"cache_key": "ocrctl#chandra-Big"})["Item"]["spot_job_id"]
        == "A"
    )


def test_terminate_failure_still_records_the_move(table, ladder):
    batch = _batch_mock()
    batch.terminate_job.side_effect = RuntimeError("already terminal")
    job = {"jobName": "chandra-T", "jobId": "jt"}
    with patch.object(ctl, "_batch", return_value=batch), patch.object(ctl, "_notify"):
        assert ctl._resubmit_to_ondemand(job)
    assert (
        "ondemand_job_id"
        in table.get_item(Key={"cache_key": "ocrctl#chandra-T"})["Item"]
    )


def test_claim_held_by_a_live_resubmitted_job_blocks(table, ladder):
    table.put_item(
        Item={
            "cache_key": "ocrctl#chandra-Big",
            "spot_job_id": "old",
            "resubmitted_job_id": "live",
        }
    )
    batch = _batch_mock()
    batch.describe_jobs.side_effect = lambda jobs: {
        "jobs": [
            {"status": "RUNNING" if jobs[0] == "live" else "FAILED", "container": {}}
        ]
    }
    with patch.object(ctl, "_batch", return_value=batch):
        assert (
            ctl._claim_move(_reclaimed(jid="new"), "ocrctl#chandra-Big", False) is None
        )


def test_ondemand_clock_carries_over_only_within_the_same_chain(table, ladder):
    table.put_item(
        Item={
            "cache_key": "ocrctl#chandra-Big",
            "spot_job_id": "A",
            "resubmitted_job_id": "B",
            "ondemand_started_at": 111,
        }
    )
    with patch.object(ctl, "_batch", return_value=_batch_mock()):
        assert ctl._claim_move(_reclaimed(jid="B"), "ocrctl#chandra-Big", True)
    assert (
        table.get_item(Key={"cache_key": "ocrctl#chandra-Big"})["Item"][
            "ondemand_started_at"
        ]
        == 111
    )
    with patch.object(
        ctl, "_batch", return_value=_batch_mock()
    ):  # a different, later job
        assert ctl._claim_move(_reclaimed(jid="Z"), "ocrctl#chandra-Big", True)
    assert (
        table.get_item(Key={"cache_key": "ocrctl#chandra-Big"})["Item"][
            "ondemand_started_at"
        ]
        != 111
    )


def test_move_job_makes_no_price_calls_for_an_already_moved_job(table, ladder):
    table.put_item(
        Item={
            "cache_key": "ocrctl#chandra-Big",
            "spot_job_id": "A",
            "moved_from": {"A"},
        }
    )
    with patch.object(ctl, "_next_move") as nm:
        assert not ctl._move_job(_reclaimed(jid="A"), 0, "reclaimed", terminate=False)
    nm.assert_not_called()


def test_only_the_final_reason_makes_a_reclaim():
    assert not ctl._is_reclaim(
        {"statusReason": "OOM", "attempts": [{"statusReason": "Host EC2 terminated"}]}
    )
    assert ctl._is_reclaim(
        {"attempts": [{"statusReason": "x"}, {"statusReason": "Host EC2 x"}]}
    )


def test_cap_alerts_once_and_scans_every_page(table):
    old = int(time.time()) - 49 * 3600
    table.put_item(
        Item={
            "cache_key": "ocrctl#chandra-Old",
            "ondemand_job_id": "od",
            "ondemand_started_at": old,
        }
    )
    batch = MagicMock()
    with (
        patch.object(ctl, "_batch", return_value=batch),
        patch.object(ctl, "_notify") as notify,
    ):
        assert ctl._enforce_ondemand_cap() == 1
        assert ctl._enforce_ondemand_cap() == 0  # already capped: no hourly repeat
    notify.assert_called_once()
    pages = [
        {
            "Items": [{"cache_key": "ocrctl#a"}],
            "LastEvaluatedKey": {"cache_key": "ocrctl#a"},
        },
        {"Items": [{"cache_key": "ocrctl#b"}]},
    ]
    fake = MagicMock()
    fake.scan.side_effect = pages
    with patch.object(ctl, "_table", return_value=fake):
        assert [r["cache_key"] for r in ctl._ondemand_rows()] == [
            "ocrctl#a",
            "ocrctl#b",
        ]


def test_sweep_ignores_live_jobs_that_the_filtered_listing_returns(table, ladder):
    """ListJobs ignores jobStatus when a filter is given: a RUNNING job with a stale reclaim
    reason must never be resubmitted."""
    batch = _batch_mock()
    live = {**_reclaimed("live", "jl"), "status": "RUNNING"}
    with (
        patch.object(ctl, "_batch", return_value=batch),
        patch.object(ctl, "_list_jobs", side_effect=lambda q, s, **f: [live]),
        patch.object(ctl, "_notify"),
    ):
        assert ctl._sweep_reclaims() == 0
    batch.submit_job.assert_not_called()

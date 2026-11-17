"""An ECS ``phase0-crosscheck`` task STOPPED: a clean exit, a bad-input exit, an operator stop and a
failure the task reported itself need nothing; one that died holding the merge claim is relaunched
ONCE (more memory), and alerts only if the retry dies too. A stop event is acted on only if it is
the task the claim says is live (EventBridge delivers at least once; an earlier task may stop late).
"""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml

from src.ingestion import ocr_chunk_manifest as cm
from tests.fake_s3 import FakeS3


def _stopped(
    exit_code=137,
    book="B",
    submission="s1",
    stop_code="EssentialContainerExited",
    task="arn:task/first",
):
    env = []
    if book is not None:
        env.append({"name": "BOOK_NAME", "value": book})
    if submission is not None:
        env.append({"name": "SUBMISSION_ID", "value": submission})
    return {
        "source": "aws.ecs",
        "detail-type": "ECS Task State Change",
        "detail": {
            "taskArn": task,
            "lastStatus": "STOPPED",
            "stopCode": stop_code,
            "stoppedReason": "Essential container in task exited",
            "containers": [{"name": "pipeline", "exitCode": exit_code}],
            "overrides": {
                "containerOverrides": [{"name": "pipeline", "environment": env}]
            },
        },
    }


@pytest.fixture
def mh(monkeypatch):
    import lambda_handlers.ocr_merge_handler as mh

    monkeypatch.setattr(mh, "BUCKET", "buck")
    return mh


def _claimed(submission="s1", task="arn:task/first", **extra):
    s3 = FakeS3()
    assert cm.claim_merge(s3, "buck", "B", cm.build_manifest(submission, [""]))
    held, _ = cm.read_claim(s3, "buck", "B")
    extra = {**extra, **({"task": task} if task else {})}
    body = json.dumps({**held, **extra}).encode()
    s3.put_object(Bucket="buck", Key=cm.merged_key("B"), Body=body)
    return s3


def _run(mh, s3, event, launch="arn:task/retry"):
    with (
        patch.object(mh, "_s3", return_value=s3),
        patch.object(mh, "_launch_crosscheck", return_value=launch) as relaunch,
        patch.object(mh, "observe_error") as alert,
    ):
        out = mh.handler(event, None)
    return out, relaunch, alert


def test_a_task_that_died_holding_the_claim_is_relaunched_once_with_more_memory(mh):
    s3 = _claimed()
    out, relaunch, alert = _run(mh, s3, _stopped(137))
    assert out == {"action": "retried", "book": "B", "retry": 1}
    relaunch.assert_called_once_with("B", "s1", mh.CROSSCHECK_RETRY_MEMORY)
    alert.assert_not_called()  # the first death is a log line, not an alert
    held, _ = cm.read_claim(s3, "buck", "B")
    assert held["retries"] == 1 and held["submission"] == "s1"
    assert (
        held["task"] == "arn:task/retry"
    )  # the live task is the retry, not the dead one


def test_the_second_death_alerts_once_and_frees_the_book_for_a_redrive(mh):
    s3 = _claimed(retries=1, task="arn:task/retry")
    out, relaunch, alert = _run(mh, s3, _stopped(137, task="arn:task/retry"))
    assert out == {"action": "alerted", "book": "B"}
    relaunch.assert_not_called()
    assert alert.call_args[0][:2] == ("ocr_merge", "crosscheck_died")
    note = alert.call_args.kwargs["note"]
    assert "exit=[137]" in note and '"force": true' in note and "chandra-B" in note
    assert cm.merged_key("B") not in s3.keys()  # a plain re-drive can merge again


@pytest.mark.parametrize(
    "exit_code", [0, 2]
)  # finished; bad input (a retry changes nothing)
def test_a_clean_or_bad_input_exit_is_ignored(mh, exit_code):
    out, relaunch, alert = _run(mh, _claimed(), _stopped(exit_code))
    assert out["action"] == "none"
    relaunch.assert_not_called()
    alert.assert_not_called()


def test_an_operator_stop_is_ignored(mh):
    out, relaunch, _ = _run(mh, _claimed(), _stopped(None, stop_code="UserInitiated"))
    assert out["action"] == "none" and not relaunch.called


def test_a_failure_the_task_reported_itself_is_not_retried(mh):
    """Its handler released the claim and alerted (exit 1): retrying would repeat the same bug."""
    out, relaunch, alert = _run(mh, FakeS3(), _stopped(1))  # no claim: released
    assert out["action"] == "none" and not relaunch.called
    alert.assert_not_called()  # no second alert for the same failure


def test_a_preflight_exit_that_left_the_claim_is_retried(mh):
    """The NAT / credit pre-flight exits 1 before the task's own handler runs: claim still held."""
    out, relaunch, _ = _run(mh, _claimed(), _stopped(1))
    assert out["action"] == "retried" and relaunch.called


def test_a_task_that_never_started_is_retried(mh):
    event = _stopped(None, stop_code="TaskFailedToStart")
    event["detail"]["containers"] = []
    out, relaunch, _ = _run(mh, _claimed(), event)
    assert out["action"] == "retried" and relaunch.called


def test_a_task_of_an_earlier_submission_is_ignored(mh):
    s3 = _claimed(submission="s2")  # the book was re-submitted since
    out, relaunch, alert = _run(mh, s3, _stopped(137, submission="s1"))
    assert out["action"] == "none" and not relaunch.called and not alert.called
    held, _ = cm.read_claim(s3, "buck", "B")
    assert held["submission"] == "s2"  # the newer submission's claim is untouched


def test_an_event_without_a_book_is_ignored(mh):
    out, relaunch, _ = _run(mh, _claimed(), _stopped(137, book=None))
    assert out["action"] == "none" and not relaunch.called


def test_a_failed_relaunch_frees_the_book(mh):
    s3 = _claimed()
    out, _, _ = _run(mh, s3, _stopped(137), launch="")
    assert out["action"] == "error"
    assert cm.merged_key("B") not in s3.keys()


def test_a_duplicate_delivery_of_the_first_stop_event_changes_nothing(mh):
    """EventBridge delivers at least once: the second copy of the first task's stop event must
    neither alert (the retry has not died) nor release the claim under the running retry.
    """
    s3 = _claimed()
    with (
        patch.object(mh, "_s3", return_value=s3),
        patch.object(
            mh, "_launch_crosscheck", return_value="arn:task/retry"
        ) as relaunch,
        patch.object(mh, "observe_error") as alert,
    ):
        first = mh.handler(_stopped(137), None)
        second = mh.handler(_stopped(137), None)  # the same event again
    assert first["action"] == "retried" and second["action"] == "none"
    assert relaunch.call_count == 1 and not alert.called
    held, _ = cm.read_claim(s3, "buck", "B")
    assert (
        held["task"] == "arn:task/retry" and held["retries"] == 1
    )  # the claim is intact


def test_the_retry_dying_is_the_only_thing_that_alerts(mh):
    s3 = _claimed()
    with (
        patch.object(mh, "_s3", return_value=s3),
        patch.object(mh, "_launch_crosscheck", return_value="arn:task/retry"),
        patch.object(mh, "observe_error") as alert,
    ):
        mh.handler(_stopped(137), None)  # the first task dies: relaunched
        out = mh.handler(_stopped(137, task="arn:task/retry"), None)  # the retry dies
    assert out["action"] == "alerted" and alert.call_count == 1


def test_a_late_event_of_an_earlier_task_of_the_same_submission_is_ignored(mh):
    """The first task reported its own failure and an operator re-drove (same submission): the
    new task holds the claim, so the old task's late stop event is not a death."""
    s3 = _claimed(task="arn:task/redriven")
    out, relaunch, alert = _run(mh, s3, _stopped(137, task="arn:task/old"))
    assert out["action"] == "none" and not relaunch.called and not alert.called
    held, _ = cm.read_claim(s3, "buck", "B")
    assert held["task"] == "arn:task/redriven"


def test_before_the_task_is_recorded_its_stop_event_is_still_acted_on(mh):
    out, relaunch, _ = _run(mh, _claimed(task=None), _stopped(137))
    assert out["action"] == "retried" and relaunch.called


def test_a_lost_claim_race_alerts_nobody_and_releases_nothing(mh):
    s3 = _claimed()
    with patch.object(cm, "note_retry", return_value=cm.LOST):
        out, relaunch, alert = _run(mh, s3, _stopped(137))
    assert out["action"] == "none" and not relaunch.called and not alert.called
    assert cm.merged_key("B") in s3.keys()


def test_the_retry_asks_ecs_for_more_memory(mh):
    ecs = MagicMock()
    ecs.run_task.return_value = {"tasks": [{"taskArn": "t"}], "failures": []}
    with patch("boto3.client", return_value=ecs):
        assert mh._launch_crosscheck("B", "s1", "4096") == "t"
        assert mh._launch_crosscheck("B", "s1") == "t"
    first, second = (c.kwargs["overrides"] for c in ecs.run_task.call_args_list)
    assert first["memory"] == "4096" and "memory" not in second
    env = first["containerOverrides"][0]["environment"]
    assert {"name": "SUBMISSION_ID", "value": "s1"} in env
    assert {"name": "BOOK_NAME", "value": "B"} in env


def test_a_retry_memory_fargate_would_refuse_falls_back(monkeypatch):
    import lambda_handlers.ocr_merge_handler as mh

    for bad in ("1000", "9000", "abc", "3000", ""):
        monkeypatch.setenv("CROSSCHECK_RETRY_MEMORY", bad)
        assert mh._retry_memory() == "4096"
    monkeypatch.setenv("CROSSCHECK_RETRY_MEMORY", "8192")
    assert mh._retry_memory() == "8192"


def test_note_retry_is_capped_and_scoped_to_the_claim():
    s3 = FakeS3()
    assert cm.note_retry(s3, "buck", "B", "s1") == cm.LOST  # no claim
    cm.claim_merge(s3, "buck", "B", cm.build_manifest("s1", [""]))
    assert cm.note_retry(s3, "buck", "B", "s2") == cm.LOST  # someone else's claim
    assert cm.note_retry(s3, "buck", "B", "s1") == 1
    assert cm.note_retry(s3, "buck", "B", "s1") == cm.EXHAUSTED  # used up
    assert cm.MAX_CROSSCHECK_RETRIES == 1


def test_note_retry_drops_the_dead_task_and_record_task_binds_the_new_one():
    s3 = _claimed()
    assert cm.note_retry(s3, "buck", "B", "s1") == 1
    held, _ = cm.read_claim(s3, "buck", "B")
    assert "task" not in held  # until the relaunched task exists
    assert cm.record_task(s3, "buck", "B", "s1", "arn:task/new") is True
    assert cm.read_claim(s3, "buck", "B")[0]["task"] == "arn:task/new"
    assert (
        cm.record_task(s3, "buck", "B", "s9", "arn:task/x") is False
    )  # not this submission's


def test_the_stack_routes_the_tasks_stop_events_to_the_merge_lambda():
    class _Loader(yaml.SafeLoader):
        pass

    def scalar(loader, suffix, node):
        return (
            loader.construct_scalar(node) if isinstance(node, yaml.ScalarNode) else None
        )

    _Loader.add_multi_constructor("!", scalar)
    root = Path(__file__).resolve().parent.parent
    tpl = yaml.load(
        (root / "cloudformation" / "compute.yaml").read_text(), Loader=_Loader
    )
    rule = tpl["Resources"]["CrosscheckStoppedRule"]["Properties"]
    pattern = rule["EventPattern"]
    assert pattern["source"] == ["aws.ecs"] and pattern["detail"]["lastStatus"] == [
        "STOPPED"
    ]
    # exactly the task family (not a prefix that would also match a sibling), in this cluster
    assert pattern["detail"]["group"] == [
        "family:${EnvironmentName}-wwii-phase0-crosscheck"
    ]
    assert "clusterArn" in pattern["detail"]
    assert [t["Id"] for t in rule["Targets"]] == ["OcrMergeLambda"]
    permission = tpl["Resources"]["CrosscheckStoppedPermission"]["Properties"]
    assert permission["Principal"] == "events.amazonaws.com"
    assert permission["Action"] == "lambda:InvokeFunction"

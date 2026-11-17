"""ECS rejects an environment override that names a container the task definition does not have
("Override for container named pipeline is not a container in the TaskDefinition"): the ClamAV scan
task's only container is ``clamav``, so launching the scan with a ``pipeline`` override failed every
time and fail-closed quarantined every binary upload as unscannable."""

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import yaml

os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
os.environ.setdefault("S3_BUCKET", "test-bucket")


def _launch(task_def, extra_env=None, book=""):
    import lambda_handlers.trigger_handler as th

    ecs = MagicMock()
    ecs.run_task.return_value = {"tasks": [{"taskArn": "t"}], "failures": []}
    with (
        patch.object(th, "ecs", ecs),
        patch.object(th, "_notify_launch"),
        patch.object(th, "dynamo"),
    ):
        th._launch_ecs_task(task_def, "test", book, extra_env, "lock#x", "fam")
    return ecs.run_task.call_args.kwargs["overrides"]


def test_the_av_scan_task_is_addressed_by_its_own_container_name():
    import lambda_handlers.trigger_handler as th

    over = _launch(th.AV_SCAN_TASK_DEF, [{"name": "AV_KEYS", "value": "[]"}])
    assert [c["name"] for c in over["containerOverrides"]] == ["clamav"]
    assert {"name": "AV_KEYS", "value": "[]"} in over["containerOverrides"][0][
        "environment"
    ]


def test_the_av_scan_task_is_recognised_by_arn_and_revision_too():
    import lambda_handlers.trigger_handler as th

    family = th._task_family(th.AV_SCAN_TASK_DEF)
    for form in (
        f"arn:aws:ecs:us-east-1:1:task-definition/{family}:3",
        f"{family}:2",
        family,
    ):
        assert th._override_container(form) == "clamav"


def test_every_other_task_keeps_the_pipeline_container():
    import lambda_handlers.trigger_handler as th

    for task_def in (
        th.PHASE1_TASK_DEF,
        th.PHASE2_TASK_DEF,
        th.PHASE3_TASK_DEF,
        th.PHASE0_TASK_DEF,
    ):
        over = _launch(task_def, [{"name": "X", "value": "1"}], book="B")
        assert [c["name"] for c in over["containerOverrides"]] == ["pipeline"]


def test_no_overrides_when_there_is_no_environment():
    import lambda_handlers.trigger_handler as th

    assert _launch(th.PHASE1_TASK_DEF) == {}


def test_the_names_the_code_uses_are_the_templates():
    """The template's container names are the source of truth: each task the trigger launches with
    environment overrides must be addressed by the container name its definition declares.
    """
    import lambda_handlers.trigger_handler as th

    class _Loader(yaml.SafeLoader):
        pass

    _Loader.add_multi_constructor(
        "!",
        lambda loader, suffix, node: (
            loader.construct_scalar(node) if isinstance(node, yaml.ScalarNode) else None
        ),
    )
    root = Path(__file__).resolve().parent.parent
    resources = yaml.load(
        (root / "cloudformation" / "compute.yaml").read_text(), Loader=_Loader
    )["Resources"]

    def container_of(resource):
        return [
            c["Name"] for c in resources[resource]["Properties"]["ContainerDefinitions"]
        ]

    assert container_of("AvScanTaskDef") == [
        th._override_container(th.AV_SCAN_TASK_DEF)
    ]
    for resource in (
        "Phase1TaskDef",
        "Phase2TaskDef",
        "Phase3TaskDef",
        "Phase0TaskDef",
        "Phase0VideoTaskDef",
    ):
        assert container_of(resource) == ["pipeline"], resource


# --- the rest of the scan launch: no shared lock, a timeout that outlasts the poll --------------------


def test_av_scans_take_no_shared_lock_so_concurrent_scans_both_launch():
    import lambda_handlers.trigger_handler as th

    ecs = MagicMock()
    ecs.run_task.return_value = {"tasks": [{"taskArn": "t"}], "failures": []}
    with (
        patch.object(th, "ecs", ecs),
        patch.object(th, "_notify_launch"),
        patch.object(th, "_fire_nat_warmup"),
        patch.object(th, "_cancel_delayed_teardown"),
        patch.object(
            th, "_acquire_task_lock", side_effect=AssertionError("scans must not lock")
        ),
    ):
        for keys in ('["a.pdf"]', '["b.pdf"]'):
            th._run_task(
                th.AV_SCAN_TASK_DEF,
                "av-scan",
                extra_env=[{"name": "AV_KEYS", "value": keys}],
            )
    assert ecs.run_task.call_count == 2
    names = {
        c.kwargs["overrides"]["containerOverrides"][0]["name"]
        for c in ecs.run_task.call_args_list
    }
    assert names == {"clamav"}


def test_a_scan_that_cannot_launch_raises_so_the_gate_fails_closed():
    import pytest

    import lambda_handlers.trigger_handler as th

    ecs = MagicMock()
    ecs.run_task.return_value = {"tasks": [], "failures": [{"reason": "capacity"}]}
    with (
        patch.object(th, "ecs", ecs),
        patch.object(th, "_fire_nat_warmup") as warm,
        patch.object(th, "_cancel_delayed_teardown") as keep,
        patch.object(th, "dynamo") as dynamo,
    ):
        with pytest.raises(RuntimeError):
            th._run_task(
                th.AV_SCAN_TASK_DEF,
                "av-scan",
                extra_env=[{"name": "AV_KEYS", "value": "[]"}],
            )
    warm.assert_called_once()  # the NAT the scan task needs is asked for ...
    keep.assert_called_once()  # ... and a pending teardown is cancelled
    # the failure path releases only the scan's own never-held key, not a shared family lock
    (released,) = [
        c.kwargs["Key"]["cache_key"] for c in dynamo.delete_item.call_args_list
    ]
    assert released.startswith("lock#av-scan#") and released != "lock#unknown"


def test_the_batch_passthrough_reaches_pipeline_tasks_only(monkeypatch):
    import lambda_handlers.trigger_handler as th

    monkeypatch.setenv("BATCH_PHASE2", "false")
    scan = _launch(th.AV_SCAN_TASK_DEF, [{"name": "AV_KEYS", "value": "[]"}])
    phase2 = _launch(th.PHASE2_TASK_DEF, [{"name": "X", "value": "1"}], book="B")
    assert "BATCH_PHASE2" not in {
        e["name"] for e in scan["containerOverrides"][0]["environment"]
    }
    assert {"name": "BATCH_PHASE2", "value": "false"} in phase2["containerOverrides"][
        0
    ]["environment"]


def test_the_trigger_lambda_outlasts_the_scan_poll():
    """The gate waits inside the trigger Lambda for the scan task's verdict; a timeout shorter than
    the poll killed it mid-wait, before it could quarantine or clear anything."""
    import inspect

    from src.ingestion import av_scan

    poll = inspect.signature(av_scan._poll_av_result).parameters
    window = poll["attempts"].default * poll["delay"].default

    class _Loader(yaml.SafeLoader):
        pass

    _Loader.add_multi_constructor(
        "!",
        lambda loader, suffix, node: (
            loader.construct_scalar(node) if isinstance(node, yaml.ScalarNode) else None
        ),
    )
    root = Path(__file__).resolve().parent.parent
    resources = yaml.load(
        (root / "cloudformation" / "compute.yaml").read_text(), Loader=_Loader
    )["Resources"]
    (trigger,) = [
        r["Properties"]
        for r in resources.values()
        if r.get("Type") == "AWS::Lambda::Function"
        and str(r["Properties"].get("FunctionName", "")).endswith("-wwii-trigger")
    ]
    assert trigger["Timeout"] >= window + 120, (trigger["Timeout"], window)

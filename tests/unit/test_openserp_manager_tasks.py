"""openserp_manager tells OpenSERP's own tasks from pipeline work (TODO gap 11).

A task ARN never contains the service name, so the old `"openserp" not in arn` check counted the
OpenSERP task as pipeline work and the idle monitor never scaled OpenSERP down.
"""

from unittest.mock import MagicMock

from lambda_handlers import openserp_manager as M

OPENSERP = "arn:aws:ecs:us-east-1:1:task/dev-wwii-pipeline/aaaa"
PHASE3 = "arn:aws:ecs:us-east-1:1:task/dev-wwii-pipeline/bbbb"


class _Paginator:
    def __init__(self, running, own):
        self.running, self.own = running, own

    def paginate(self, cluster, desiredStatus, serviceName=None):
        arns = self.own if serviceName else self.running
        for i in range(0, max(len(arns), 1), 100):  # list_tasks pages hold at most 100
            yield {"taskArns": arns[i : i + 100]}


def _ecs(running, own, desired=1):
    ecs = MagicMock()
    ecs.get_paginator.return_value = _Paginator(running, own)
    ecs.describe_services.return_value = {"services": [{"desiredCount": desired}]}

    def list_tasks(cluster, desiredStatus, **_):
        # RUNNING answers like the real first page, so the old unpaginated name check sees the
        # OpenSERP task (and the tests fail on it); no STOPPED tasks: idle long enough
        return {"taskArns": running[:100] if desiredStatus == "RUNNING" else []}

    ecs.list_tasks.side_effect = list_tasks
    return ecs


def _run(monkeypatch, ecs, lock_held=False):
    monkeypatch.setattr(M, "_nat_too_old", lambda max_hours=2: False)
    monkeypatch.setattr(M, "_any_lock_held", lambda: lock_held)
    monkeypatch.setattr("boto3.client", lambda svc, **k: ecs)
    return M.handler({}, None)


def test_openserp_alone_is_not_pipeline_work(monkeypatch):
    ecs = _ecs(running=[OPENSERP], own=[OPENSERP])
    assert _run(monkeypatch, ecs)["action"] == "teardown"
    ecs.update_service.assert_called_once_with(
        cluster=f"{M.ENV_NAME}-wwii-pipeline",
        service=f"{M.ENV_NAME}-wwii-openserp",
        desiredCount=0,
    )


def test_a_held_lock_keeps_openserp_up(monkeypatch):
    """A pipeline task is starting (lock or NAT lease held) but not yet running."""
    ecs = _ecs(running=[OPENSERP], own=[OPENSERP])
    assert _run(monkeypatch, ecs, lock_held=True) == {
        "action": "none",
        "reason": "lock held",
    }
    ecs.update_service.assert_not_called()


def test_a_pipeline_task_keeps_openserp_up(monkeypatch):
    ecs = _ecs(running=[OPENSERP, PHASE3], own=[OPENSERP])
    result = _run(monkeypatch, ecs)
    assert result == {"action": "none", "tasks": 1}
    ecs.update_service.assert_not_called()


def test_every_page_of_tasks_is_read(monkeypatch):
    """A pipeline task on the second page of list_tasks still counts."""
    others = [
        f"arn:aws:ecs:us-east-1:1:task/dev-wwii-pipeline/o{i}" for i in range(100)
    ]
    ecs = _ecs(running=others + [PHASE3], own=others)
    assert _run(monkeypatch, ecs) == {"action": "none", "tasks": 1}


def test_the_two_hour_guard_also_ignores_openserp(monkeypatch):
    ecs = _ecs(running=[OPENSERP], own=[OPENSERP])
    monkeypatch.setattr(M, "_nat_too_old", lambda max_hours=2: True)
    monkeypatch.setattr(M, "_any_lock_held", lambda: False)
    monkeypatch.setattr("boto3.client", lambda svc, **k: ecs)
    assert M.handler({}, None)["action"] == "force_teardown"

"""Tests for the uniform NAT-lease acquisition (M3 gap fix).

The gap: run_retrieve_only (poller-launched, needs NAT) held no lease, and the
lease was acquired per-path inconsistently. Fix: acquire once at __main__ entry
for every path. These tests cover the acquire/release helpers' gating so a task
in ECS always registers demand and a local run never touches DynamoDB.
"""

import os
from unittest.mock import patch

os.environ.setdefault("S3_BUCKET", "test-bucket")
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")

import ecs_entrypoint


def test_acquire_lease_noop_when_local(monkeypatch):
    """No ECS metadata => local run => must NOT touch nat_lease/DynamoDB."""
    monkeypatch.delenv("ECS_TASK_ID", raising=False)
    monkeypatch.delenv("ECS_CONTAINER_METADATA_URI_V4", raising=False)
    with patch("src.utils.nat_lease.acquire_lease") as acq:
        ecs_entrypoint._acquire_nat_lease()
    acq.assert_not_called()


def test_acquire_lease_fires_in_ecs(monkeypatch):
    """ECS metadata present => acquire a lease."""
    monkeypatch.setenv("ECS_TASK_ID", "task-abc")
    with patch("src.utils.nat_lease.acquire_lease") as acq:
        ecs_entrypoint._acquire_nat_lease()
    acq.assert_called_once()


def test_acquire_lease_via_metadata_uri(monkeypatch):
    """Fargate auto-injects METADATA_URI_V4 even without ECS_TASK_ID => acquire."""
    monkeypatch.delenv("ECS_TASK_ID", raising=False)
    monkeypatch.setenv("ECS_CONTAINER_METADATA_URI_V4", "http://169.254.170.2/v4/x")
    with patch("src.utils.nat_lease.acquire_lease") as acq:
        ecs_entrypoint._acquire_nat_lease()
    acq.assert_called_once()


def test_release_lease_noop_when_local(monkeypatch):
    monkeypatch.delenv("ECS_TASK_ID", raising=False)
    monkeypatch.delenv("ECS_CONTAINER_METADATA_URI_V4", raising=False)
    with patch("src.utils.nat_lease.release_lease") as rel:
        ecs_entrypoint._release_nat_lease()
    rel.assert_not_called()


def test_acquire_lease_never_raises(monkeypatch):
    """A lease failure must never crash the task."""
    monkeypatch.setenv("ECS_TASK_ID", "task-abc")
    with patch("src.utils.nat_lease.acquire_lease", side_effect=RuntimeError("boom")):
        ecs_entrypoint._acquire_nat_lease()  # must not raise

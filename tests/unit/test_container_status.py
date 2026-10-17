"""Container status API (option 3) + nat_teardown._containers_busy polling."""

import json
import os
import time
from unittest.mock import MagicMock, patch

os.environ.setdefault("ENV_NAME", "dev")
os.environ.setdefault("AWS_REGION", "us-east-1")

from ecs_modules.container_status import ProgressState  # noqa: E402
import lambda_handlers.nat_teardown as nt  # noqa: E402


def test_progress_state_starts_busy_then_goes_idle():
    st = ProgressState(idle_seconds=1)
    assert st.snapshot()["busy"] is True  # just started = working
    st._last = time.time() - 5  # simulate no progress for 5s (> 1s threshold)
    snap = st.snapshot()
    assert snap["busy"] is False
    assert snap["idle_seconds"] >= 5


def test_progress_mark_resets_to_busy():
    st = ProgressState(idle_seconds=100)
    st._last = time.time() - 50
    st.mark("phase2")
    snap = st.snapshot()
    assert snap["busy"] is True
    assert snap["phase"] == "phase2"


def test_containers_busy_true_when_any_busy():
    with (
        patch.object(
            nt, "_running_pipeline_task_ips", return_value=[("t1", "10.0.0.1")]
        ),
        patch("urllib.request.urlopen") as uo,
    ):
        resp = MagicMock()
        resp.read.return_value = json.dumps({"busy": True, "idle_seconds": 3}).encode()
        uo.return_value.__enter__.return_value = resp
        assert nt._containers_busy() is True


def test_containers_busy_false_when_all_idle():
    with (
        patch.object(
            nt, "_running_pipeline_task_ips", return_value=[("t1", "10.0.0.1")]
        ),
        patch("urllib.request.urlopen") as uo,
    ):
        resp = MagicMock()
        resp.read.return_value = json.dumps(
            {"busy": False, "idle_seconds": 999}
        ).encode()
        uo.return_value.__enter__.return_value = resp
        assert nt._containers_busy() is False


def test_containers_busy_true_when_unreachable_failsafe():
    with (
        patch.object(
            nt, "_running_pipeline_task_ips", return_value=[("t1", "10.0.0.1")]
        ),
        patch("urllib.request.urlopen", side_effect=OSError("conn refused")),
    ):
        assert nt._containers_busy() is True  # unreachable == uncertain == keep NAT


def test_containers_busy_false_when_no_tasks():
    with patch.object(nt, "_running_pipeline_task_ips", return_value=[]):
        assert nt._containers_busy() is False  # queue check covers no-container cases


def test_containers_busy_true_when_non_private_ip():
    """A task reporting a non-RFC1918 IP is unexpected -> keep NAT (never poll a public IP)."""
    with patch.object(
        nt, "_running_pipeline_task_ips", return_value=[("t1", "8.8.8.8")]
    ):
        assert nt._containers_busy() is True


def test_is_private_ipv4():
    assert nt._is_private_ipv4("10.0.1.5") is True
    assert nt._is_private_ipv4("172.16.3.9") is True
    assert nt._is_private_ipv4("8.8.8.8") is False
    assert nt._is_private_ipv4("not-an-ip") is False

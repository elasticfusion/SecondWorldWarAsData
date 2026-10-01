"""Tests for the batch-routing defect fixes (St. Vith 2026-09-27).

Defect: a batch run took the IN-PROCESS extractor path (which does NOT enqueue a
batch_job# record for the poller) because _should_use_batch_mode silently
swallowed a config-read error and returned False. Fixes:
1. _should_use_batch_mode reads batch.<phase> and does not silently swallow errors.
2. run_phase, in AWS/ECS mode, refuses to run the in-process path for a --batch
   request — it forces submit-only so the batch is enqueued for the poller.
"""

import os
from unittest.mock import MagicMock, patch

os.environ.setdefault("S3_BUCKET", "test-bucket")
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")

import ecs_entrypoint

# --- _should_use_batch_mode ---


def test_batch_mode_true_when_config_enables_phase2():
    from unittest.mock import mock_open

    with patch("builtins.open", mock_open(read_data="batch:\n  phase2: true\n")):
        assert ecs_entrypoint._should_use_batch_mode("phase2_extract.py") is True


def test_batch_mode_false_when_disabled():
    from unittest.mock import mock_open

    with patch("builtins.open", mock_open(read_data="batch:\n  phase2: false\n")):
        assert ecs_entrypoint._should_use_batch_mode("phase2_extract.py") is False


def test_batch_mode_logs_error_on_read_failure(caplog):
    """A config-read failure must be logged loudly, not silently swallowed."""
    import logging

    with patch("builtins.open", side_effect=OSError("cannot read")):
        with caplog.at_level(logging.ERROR):
            result = ecs_entrypoint._should_use_batch_mode("phase2_extract.py")
    assert result is False
    assert any("Failed to read batch config" in r.message for r in caplog.records)


# --- run_phase AWS-mode submit-only guard ---


def test_aws_batch_request_forces_submit_only(monkeypatch):
    """In ECS mode, a --batch run whose batch-mode check says no must still route
    to submit-only (so the batch is enqueued), NOT the in-process path."""
    monkeypatch.setenv("ECS_TASK_ID", "task-123")
    called = {}

    with (
        patch.object(ecs_entrypoint, "_should_use_batch_mode", return_value=False),
        patch.object(
            ecs_entrypoint,
            "run_submit_only",
            side_effect=lambda ps, ea: called.setdefault("submit_only", (ps, ea)),
        ),
        patch.object(ecs_entrypoint, "_acquire_nat_lease"),
        patch.object(ecs_entrypoint, "_cancel_stale_teardown"),
        patch.object(ecs_entrypoint, "_load_secrets"),
        patch.object(ecs_entrypoint, "_preflight_credit_check"),
        patch.object(ecs_entrypoint, "_preflight_notification_subscriptions"),
        patch.object(ecs_entrypoint, "_patch_config"),
        patch.object(ecs_entrypoint, "_start_openserp_if_needed"),
        patch.object(ecs_entrypoint, "_setup_symlinks"),
        patch.object(ecs_entrypoint, "_download_inputs"),
        patch.object(ecs_entrypoint, "BackgroundSync", MagicMock()),
        patch("subprocess.run") as subrun,
    ):
        ecs_entrypoint.run_phase("phase2_extract.py", ["--batch"])

    assert "submit_only" in called  # routed to submit-only
    subrun.assert_not_called()  # did NOT run the in-process subprocess path


def test_local_batch_request_does_not_force_submit_only(monkeypatch):
    """Outside ECS (local), the guard does not fire — in-process path is allowed."""
    monkeypatch.delenv("ECS_TASK_ID", raising=False)
    monkeypatch.delenv("ECS_CONTAINER_METADATA_URI_V4", raising=False)

    with (
        patch.object(ecs_entrypoint, "_should_use_batch_mode", return_value=False),
        patch.object(ecs_entrypoint, "run_submit_only") as submit_only,
        patch.object(ecs_entrypoint, "_acquire_nat_lease"),
        patch.object(ecs_entrypoint, "_cancel_stale_teardown"),
        patch.object(ecs_entrypoint, "_load_secrets"),
        patch.object(ecs_entrypoint, "_preflight_credit_check"),
        patch.object(ecs_entrypoint, "_preflight_notification_subscriptions"),
        patch.object(ecs_entrypoint, "_patch_config"),
        patch.object(ecs_entrypoint, "_start_openserp_if_needed"),
        patch.object(ecs_entrypoint, "_setup_symlinks"),
        patch.object(ecs_entrypoint, "_download_inputs"),
        patch.object(ecs_entrypoint, "BackgroundSync", MagicMock()),
        patch.object(ecs_entrypoint, "_post_process"),
        patch.object(ecs_entrypoint, "_final_sync"),
        patch.object(ecs_entrypoint, "_release_nat_lease"),
        patch("subprocess.run", return_value=MagicMock(returncode=0)),
    ):
        ecs_entrypoint.run_phase("phase2_extract.py", ["--batch"])

    submit_only.assert_not_called()  # local: guard does not force submit-only

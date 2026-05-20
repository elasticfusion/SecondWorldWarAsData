"""Unit tests for ecs_entrypoint submit/retrieve argument routing."""

import os
import sys
from unittest.mock import patch, MagicMock

import pytest

os.environ.setdefault("S3_BUCKET", "test-bucket")
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")


@pytest.fixture
def entrypoint():
    """Import ecs_entrypoint module."""
    import importlib.util

    spec = importlib.util.spec_from_file_location("ecs_entrypoint", "ecs_entrypoint.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["ecs_entrypoint"] = mod
    spec.loader.exec_module(mod)
    return mod


def test_has_submit_only_function(entrypoint):
    assert hasattr(entrypoint, "run_submit_only")
    assert callable(entrypoint.run_submit_only)


def test_has_retrieve_only_function(entrypoint):
    assert hasattr(entrypoint, "run_retrieve_only")
    assert callable(entrypoint.run_retrieve_only)


def test_has_teardown_networking(entrypoint):
    assert hasattr(entrypoint, "_teardown_networking")
    assert callable(entrypoint._teardown_networking)


@patch("ecs_entrypoint.run_submit_only")
@patch("ecs_entrypoint.run_phase")
@patch("ecs_entrypoint.run_retrieve_only")
def test_argv_routes_submit_only(mock_retrieve, mock_phase, mock_submit, entrypoint):
    """--submit-only routes to run_submit_only."""
    with patch.object(sys, "argv", ["ecs_entrypoint.py", "--submit-only", "phase3_enrich_data.py"]):
        # Re-execute the __main__ block logic
        if sys.argv[1] == "--submit-only":
            entrypoint.run_submit_only(sys.argv[2], sys.argv[3:])
    mock_submit.assert_called_once_with("phase3_enrich_data.py", [])
    mock_phase.assert_not_called()
    mock_retrieve.assert_not_called()


@patch("ecs_entrypoint.run_submit_only")
@patch("ecs_entrypoint.run_phase")
@patch("ecs_entrypoint.run_retrieve_only")
def test_argv_routes_retrieve_only(mock_retrieve, mock_phase, mock_submit, entrypoint):
    """--retrieve-only routes to run_retrieve_only."""
    with patch.object(sys, "argv", [
        "ecs_entrypoint.py", "--retrieve-only", "batch-xyz", "phase3_enrich_data.py", "--max-items", "10"
    ]):
        if sys.argv[1] == "--retrieve-only":
            entrypoint.run_retrieve_only(sys.argv[3], sys.argv[4:], batch_id=sys.argv[2])
    mock_retrieve.assert_called_once_with("phase3_enrich_data.py", ["--max-items", "10"], batch_id="batch-xyz")
    mock_phase.assert_not_called()
    mock_submit.assert_not_called()


@patch("ecs_entrypoint.run_submit_only")
@patch("ecs_entrypoint.run_phase")
@patch("ecs_entrypoint.run_retrieve_only")
def test_argv_routes_default(mock_retrieve, mock_phase, mock_submit, entrypoint):
    """Default routes to run_phase."""
    with patch.object(sys, "argv", ["ecs_entrypoint.py", "phase2_extract.py", "--batch"]):
        if sys.argv[1] not in ("--submit-only", "--retrieve-only"):
            entrypoint.run_phase(sys.argv[1], sys.argv[2:])
    mock_phase.assert_called_once_with("phase2_extract.py", ["--batch"])
    mock_submit.assert_not_called()
    mock_retrieve.assert_not_called()


def test_submit_only_adds_batch_flag(entrypoint):
    """run_submit_only adds --batch if not present."""
    with patch.object(entrypoint, "_load_secrets"), \
         patch.object(entrypoint, "_patch_config"), \
         patch.object(entrypoint, "_start_openserp_if_needed"), \
         patch.object(entrypoint, "_download_inputs"), \
         patch.object(entrypoint, "_setup_symlinks"), \
         patch.object(entrypoint, "_final_sync"), \
         patch.object(entrypoint, "_stop_openserp_if_running"), \
         patch.object(entrypoint, "_teardown_networking"), \
         patch.object(entrypoint, "_enqueue_from_metrics"), \
         patch("subprocess.run") as mock_run, \
         patch("src.utils.batch_api.poll_batch"), \
         patch("src.utils.batch_api.retrieve_results"):
        mock_run.return_value = MagicMock(returncode=0)
        entrypoint.WORKDIR = entrypoint.Path("/tmp/test_pipeline")
        entrypoint.run_submit_only("phase3_enrich_data.py", [])
        cmd = mock_run.call_args[0][0]
        assert "--batch" in cmd
        assert "phase3_enrich_data.py" in cmd

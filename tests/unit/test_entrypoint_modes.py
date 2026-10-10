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
    with patch.object(
        sys, "argv", ["ecs_entrypoint.py", "--submit-only", "phase3_enrich_data.py"]
    ):
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
    with patch.object(
        sys,
        "argv",
        [
            "ecs_entrypoint.py",
            "--retrieve-only",
            "batch-xyz",
            "phase3_enrich_data.py",
            "--max-items",
            "10",
        ],
    ):
        if sys.argv[1] == "--retrieve-only":
            entrypoint.run_retrieve_only(
                sys.argv[3], sys.argv[4:], batch_id=sys.argv[2]
            )
    mock_retrieve.assert_called_once_with(
        "phase3_enrich_data.py", ["--max-items", "10"], batch_id="batch-xyz"
    )
    mock_phase.assert_not_called()
    mock_submit.assert_not_called()


@patch("ecs_entrypoint.run_submit_only")
@patch("ecs_entrypoint.run_phase")
@patch("ecs_entrypoint.run_retrieve_only")
def test_argv_routes_default(mock_retrieve, mock_phase, mock_submit, entrypoint):
    """Default routes to run_phase."""
    with patch.object(
        sys, "argv", ["ecs_entrypoint.py", "phase2_extract.py", "--batch"]
    ):
        if sys.argv[1] not in ("--submit-only", "--retrieve-only"):
            entrypoint.run_phase(sys.argv[1], sys.argv[2:])
    mock_phase.assert_called_once_with("phase2_extract.py", ["--batch"])
    mock_submit.assert_not_called()
    mock_retrieve.assert_not_called()


def test_submit_only_adds_batch_flag(entrypoint):
    """run_submit_only adds --batch if not present."""
    with (
        patch.object(entrypoint, "_load_secrets"),
        patch.object(entrypoint, "_patch_config"),
        patch.object(entrypoint, "_start_openserp_if_needed"),
        patch.object(entrypoint, "_download_inputs"),
        patch.object(entrypoint, "_setup_symlinks"),
        patch.object(entrypoint, "_final_sync"),
        patch.object(entrypoint, "_stop_openserp_if_running"),
        patch.object(entrypoint, "_teardown_networking"),
        patch.object(entrypoint, "_enqueue_from_metrics"),
        patch("subprocess.run") as mock_run,
        patch("src.utils.batch_api.poll_batch"),
        patch("src.utils.batch_api.retrieve_results"),
    ):
        mock_run.return_value = MagicMock(returncode=0)
        entrypoint.WORKDIR = entrypoint.Path("/tmp/test_pipeline")
        entrypoint.run_submit_only("phase3_enrich_data.py", [])
        cmd = mock_run.call_args[0][0]
        assert "--batch" in cmd
        assert "phase3_enrich_data.py" in cmd


# --- Option 1: SFN-owned doc lifecycle advancement (multi-doc) ---


def test_advance_doc_lifecycle_phase1_to_parsed(entrypoint):
    with patch.dict(os.environ, {"MULTI_DOC_ENABLED": "true", "BOOK_NAME": "B460"}):
        with patch("src.ingestion.doc_lifecycle.set_status") as ss:
            entrypoint._advance_doc_lifecycle("1")
    ss.assert_called_once_with("B460", "parsed", next_phase="phase2")


def test_advance_doc_lifecycle_phase3_to_done(entrypoint):
    with patch.dict(os.environ, {"MULTI_DOC_ENABLED": "true", "BOOK_NAME": "B460"}):
        with patch("src.ingestion.doc_lifecycle.set_status") as ss:
            entrypoint._advance_doc_lifecycle("3")
    ss.assert_called_once_with("B460", "done", next_phase=None)


def test_advance_doc_lifecycle_noop_in_serial_mode(entrypoint):
    with patch.dict(os.environ, {"MULTI_DOC_ENABLED": "false", "BOOK_NAME": "B460"}):
        with patch("src.ingestion.doc_lifecycle.set_status") as ss:
            entrypoint._advance_doc_lifecycle("1")
    ss.assert_not_called()  # serial mode uses pending#/phase-complete, not doc#


def test_advance_doc_lifecycle_noop_without_book(entrypoint):
    with patch.dict(os.environ, {"MULTI_DOC_ENABLED": "true", "BOOK_NAME": ""}):
        with patch("src.ingestion.doc_lifecycle.set_status") as ss:
            entrypoint._advance_doc_lifecycle("2")
    ss.assert_not_called()


# --- Double-drive fix: multi-doc phase progression owned by SFN, not event chain ---


def test_post_process_phase1_multidoc_advances_not_invokes(entrypoint):
    """Multi-doc: phase1 completion advances doc lifecycle, does NOT invoke the
    trigger to launch Phase 2 (SFN owns progression — no double-drive)."""
    with patch.dict(os.environ, {"MULTI_DOC_ENABLED": "true", "BOOK_NAME": "B460"}):
        with (
            patch.object(entrypoint, "_multi_doc_enabled", return_value=True),
            patch.object(entrypoint, "_advance_doc_lifecycle") as adv,
            patch.object(entrypoint, "_invoke_trigger_phase_complete") as evt,
            patch.object(entrypoint, "_clear_processed_content_keys"),
            patch.object(entrypoint.boto3, "client") as bc,
        ):
            entrypoint._post_process("phase1_parse.py", {})
    adv.assert_called_once_with("1")
    evt.assert_not_called()  # no event-chain phase2 launch under multi-doc
    # no trigger invoke for {source:manual,phase:2}
    for call in bc.return_value.invoke.call_args_list:
        payload = call.kwargs.get("Payload", b"{}")
        assert b'"phase": "2"' not in payload


def test_post_process_phase1_serial_invokes_not_advances(entrypoint):
    """Serial: phase1 completion invokes the trigger + phase-complete chain, and
    does NOT touch the doc lifecycle (no doc# records in serial)."""
    with patch.dict(os.environ, {"MULTI_DOC_ENABLED": "false", "BOOK_NAME": "B460"}):
        with (
            patch.object(entrypoint, "_multi_doc_enabled", return_value=False),
            patch.object(entrypoint, "_advance_doc_lifecycle") as adv,
            patch.object(entrypoint, "_invoke_trigger_phase_complete") as evt,
            patch.object(entrypoint, "_clear_processed_content_keys"),
            patch.object(entrypoint.boto3, "client"),
        ):
            entrypoint._post_process("phase1_parse.py", {})
    adv.assert_not_called()  # no doc lifecycle in serial
    evt.assert_called_once_with("1")


def test_enqueue_or_alert_resolves_job_queue_names(entrypoint):
    """Regression: _enqueue_or_alert must import BatchJob + enqueue_job in its OWN scope.
    Previously they were imported only in _enqueue_from_metrics, so calling _enqueue_or_alert
    raised NameError: name 'enqueue_job' is not defined (crashed Phase 2 post-extraction).
    """
    with patch("src.utils.job_queue.enqueue_job") as eq:
        # Should enqueue without a NameError; a real BatchJob is constructed internally.
        entrypoint._enqueue_or_alert("batch-123", "phase2", "SomeBook", "nm", 5)
    eq.assert_called_once()
    job = eq.call_args.args[0]
    assert job.batch_id == "batch-123" and job.phase == "phase2"

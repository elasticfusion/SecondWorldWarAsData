"""Tests for the M4-final kill-switch routing in trigger_handler (§17.3)."""

import os
from unittest.mock import MagicMock, patch

os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
os.environ.setdefault("ECS_CLUSTER", "dev-wwii-pipeline")
os.environ.setdefault("CACHE_TABLE", "dev-wwii-api-cache")

from lambda_handlers import trigger_handler as th


def test_multi_doc_active_requires_flag_and_arn():
    with (
        patch.object(th, "MULTI_DOC_ENABLED", True),
        patch.object(th, "DISPATCHER_STATE_MACHINE_ARN", "arn:sm"),
    ):
        assert th._multi_doc_active() is True
    with (
        patch.object(th, "MULTI_DOC_ENABLED", True),
        patch.object(th, "DISPATCHER_STATE_MACHINE_ARN", ""),
    ):
        assert th._multi_doc_active() is False  # no ARN wired
    with (
        patch.object(th, "MULTI_DOC_ENABLED", False),
        patch.object(th, "DISPATCHER_STATE_MACHINE_ARN", "arn:sm"),
    ):
        assert th._multi_doc_active() is False  # switch off


def test_start_dispatcher_starts_execution():
    sfn = MagicMock()
    sfn.list_executions.return_value = {"executions": []}
    with (
        patch.object(th, "DISPATCHER_STATE_MACHINE_ARN", "arn:sm"),
        patch.object(th.boto3, "client", return_value=sfn),
    ):
        assert th._start_dispatcher("content-uploaded") is True
    sfn.start_execution.assert_called_once()


def test_start_dispatcher_skips_when_already_running():
    sfn = MagicMock()
    sfn.list_executions.return_value = {"executions": [{"executionArn": "x"}]}
    with (
        patch.object(th, "DISPATCHER_STATE_MACHINE_ARN", "arn:sm"),
        patch.object(th.boto3, "client", return_value=sfn),
    ):
        assert th._start_dispatcher("content-uploaded") is True
    sfn.start_execution.assert_not_called()  # don't pile up drains


def test_start_dispatcher_returns_false_on_error():
    """On failure the caller falls back to the serial path — never strand work."""
    sfn = MagicMock()
    sfn.list_executions.side_effect = RuntimeError("sfn down")
    with (
        patch.object(th, "DISPATCHER_STATE_MACHINE_ARN", "arn:sm"),
        patch.object(th.boto3, "client", return_value=sfn),
    ):
        assert th._start_dispatcher("content-uploaded") is False


def test_content_keys_keeps_media_drops_compressed_and_junk():
    keys = [
        "contentrepository/FMS/B-Series/B 400-499/B400.pdf",
        "contentrepository/Maps/map1.jpg",
        "contentrepository/BreakoutAndPursuit/chapter1/chapter1a-content.md",
        "contentrepository/FMS/B-Series/B 400-499.zip",  # compressed -> ignored
        "contentrepository/FMS/notes.rar",  # compressed -> ignored
        "contentrepository/FMS/archive.7z",  # compressed -> ignored
        "contentrepository/FMS/bundle.tar.gz",  # compressed -> ignored
        "contentrepository/FMS/thumbs.db",  # non-content -> skipped
    ]
    kept = th._content_keys(keys)
    assert "contentrepository/FMS/B-Series/B 400-499/B400.pdf" in kept
    assert "contentrepository/Maps/map1.jpg" in kept
    assert any(k.endswith(".md") for k in kept)
    assert not any(k.endswith((".zip", ".rar", ".7z", ".tar.gz", ".gz")) for k in kept)
    assert not any(k.endswith(".db") for k in kept)
    assert len(kept) == 3


def test_content_keys_empty_when_only_compressed():
    keys = [
        "contentrepository/x.zip",
        "contentrepository/y.rar",
        "contentrepository/z.tar.gz",
        "contentrepository/w.7z",
    ]
    assert th._content_keys(keys) == []

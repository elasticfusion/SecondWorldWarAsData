"""Regression tests for PR-D — live Lambda silent-drop fixes (PATTERN-8 audit findings
#11, #12, #13, #14, #44).

Governing rule: every failure/drop/degraded path must emit a LOUD canonical
``OBSERV <scope>.<event> status=error`` at the point of loss — never generic-only, never
silent, never count=0-as-ok. Each test asserts (a) the canonical OBSERV line is emitted and
(b) the behavioral contract (no silent success / caller honors failure).

The OBSERV sink (src.utils.observe) logs on the "observe" logger; caplog captures it. We assert
on the stable greppable token ``OBSERV <scope>.<event> status=error`` so a drift in wording still
fails loudly.
"""

import logging
import os
from unittest.mock import MagicMock, patch

import pytest

os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
os.environ.setdefault("AWS_REGION", "us-east-1")
os.environ.setdefault("S3_BUCKET", "test-bucket")
os.environ.setdefault("CACHE_TABLE", "test-wwii-api-cache")
os.environ.setdefault("ENV_NAME", "test")
os.environ.setdefault("ECS_CLUSTER", "test-wwii-pipeline")
os.environ.setdefault("PRIVATE_SUBNET_IDS", "subnet-abc123")
os.environ.setdefault("SECURITY_GROUP_ID", "sg-abc123")
os.environ.setdefault("NOTIFICATION_TOPIC_ARN", "")

import lambda_handlers.batch_poller as bp
import lambda_handlers.ocr_merge_handler as om
import lambda_handlers.trigger_handler as th


def _observ_lines(caplog, scope_event):
    """All captured log messages carrying the canonical OBSERV token for scope.event."""
    token = f"OBSERV {scope_event} status=error"
    return [r.getMessage() for r in caplog.records if token in r.getMessage()]


# --- #11: ocr_merge_handler handler `if not book` -------------------------------------------------


def test_11_book_underivable_emits_canonical_observ(caplog):
    """A SUCCEEDED Chandra job whose book cannot be derived must be a LOUD canonical anomaly,
    not a generic-only logger.error — a paid OCR output would otherwise dead-end silently.
    """
    event = {
        "detail": {
            "status": "SUCCEEDED",
            "jobQueue": "test-wwii-chandra-gpu",
            "jobName": "weird-job-name",  # no 'chandra-' prefix, no s3 pdf arg
        }
    }
    with caplog.at_level(logging.ERROR):
        out = om.handler(event, None)
    # behavioral contract: not dropped silently — reported as an error action
    assert out["action"] == "error"
    assert out["reason"] == "no book"
    # canonical OBSERV at the point of loss
    assert _observ_lines(
        caplog, "ocr_merge.book_underivable"
    ), "expected OBSERV ocr_merge.book_underivable status=error"


# --- #44: ocr_merge_handler `_route_to_oob` launch failure ---------------------------------------


def _structured_s3():
    s3 = MagicMock()
    s3.get_paginator.return_value.paginate.return_value = [
        {"Contents": [{"Key": "ocr-output/ETO_OOB/input/input.md"}]}
    ]
    good = b"# OOB\n\n" + b"| Unit | Commander |\n|---|---|\n|1st Inf|Smith|\n" * 4
    s3.get_object.return_value = {"Body": MagicMock(read=lambda: good)}
    s3.head_object.return_value = {}  # .structured marker EXISTS -> OOB route
    return s3


def test_44_oob_launch_exception_no_success_key(caplog):
    """A phase0-ingest launch EXCEPTION must emit a canonical OBSERV and NOT return a
    success key (the OOB markdown is in S3 but nothing will parse it)."""
    s3 = _structured_s3()
    ecs = MagicMock()
    ecs.run_task.side_effect = RuntimeError("capacity")
    with (
        patch.object(om, "BUCKET", "buck"),
        patch.object(om, "_s3", return_value=s3),
        patch("boto3.client", return_value=ecs),
        caplog.at_level(logging.ERROR),
    ):
        out = om.merge_ocr_output("ETO_OOB")
    # #44: NO success key on launch failure
    assert out == ""
    assert _observ_lines(caplog, "ocr_merge.oob_launch_error")


def test_44_oob_launch_no_task_placed_no_success_key(caplog):
    """An ECS placement that placed no task (run_task does NOT raise) must also be loud +
    return no success key."""
    s3 = _structured_s3()
    ecs = MagicMock()
    ecs.run_task.return_value = {"tasks": [], "failures": [{"reason": "Capacity"}]}
    with (
        patch.object(om, "BUCKET", "buck"),
        patch.object(om, "_s3", return_value=s3),
        patch("boto3.client", return_value=ecs),
        caplog.at_level(logging.ERROR),
    ):
        out = om.merge_ocr_output("ETO_OOB")
    assert out == ""
    assert _observ_lines(caplog, "ocr_merge.oob_launch_error")


def test_44_oob_launch_success_returns_key():
    """A confirmed launch still returns the OOB key (no false positive from the new guard)."""
    s3 = _structured_s3()
    ecs = MagicMock()
    ecs.run_task.return_value = {"tasks": [{"taskArn": "arn:task/1"}], "failures": []}
    with (
        patch.object(om, "BUCKET", "buck"),
        patch.object(om, "_s3", return_value=s3),
        patch("boto3.client", return_value=ecs),
    ):
        out = om.merge_ocr_output("ETO_OOB")
    assert out == "contentrepository/ETO_OOB/ocr_output/ETO_OOB.md"


# --- #12: batch_poller `_check_batch_status` except -> 'pending' ---------------------------------


def test_12_status_check_error_emits_canonical_observ(caplog):
    """A status-check exception returns the 'pending' skip sentinel (so a transient fault
    doesn't mark a batch failed) — but it MUST be a loud canonical anomaly so a persistent
    broken check is distinguishable from a still-running batch."""
    with patch.object(bp, "requests") as req:
        req.get.side_effect = RuntimeError("connection reset")
        with caplog.at_level(logging.ERROR):
            status = bp._check_batch_status("fake-key", "batch-123", 0)
    assert status == "pending"  # behavioral contract preserved
    lines = _observ_lines(caplog, "batch.status_check_error")
    assert lines, "expected OBSERV batch.status_check_error status=error"
    # carries the batch_id for correlation
    assert any("batch_id=batch-123" in m for m in lines)


# --- #13: trigger_handler `_submit_ocr` except -> False ------------------------------------------


def test_13_submit_ocr_error_emits_canonical_observ(caplog):
    """An OCR submit exception must emit a canonical OBSERV (key=..) at the point of loss and
    still return False — the raw doc is dropped from intake with nothing to re-drive it.
    """
    with (
        patch.object(th, "_safe_stem_from_key", return_value="B460"),
        patch.object(th, "_ocr_media_mismatch", return_value=False),
        patch.object(th, "_claim_or_reclaim_ocr", side_effect=RuntimeError("boom")),
        caplog.at_level(logging.ERROR),
    ):
        ok = th._submit_ocr("contentrepository/NARA/B460.pdf")
    assert ok is False  # behavioral contract
    lines = _observ_lines(caplog, "ocr.submit_error")
    assert lines, "expected OBSERV ocr.submit_error status=error"
    assert any("key=contentrepository/NARA/B460.pdf" in m for m in lines)


def test_13_caller_honors_submit_failure(caplog):
    """The batch submit helper must HONOR a False return (not silently ignore it) — a dropped
    OCR submit surfaces a batch-level warning."""
    with (
        patch.object(th, "_ensure_nat_for_ocr"),
        patch.object(th, "_submit_ocr", return_value=False) as submit,
        caplog.at_level(logging.WARNING),
    ):
        th._submit_ocr_batch(["contentrepository/NARA/B460.pdf"])
    submit.assert_called_once()
    assert any(
        "OCR submit did not proceed" in r.getMessage() for r in caplog.records
    ), "caller must surface the dropped OCR submit"


# --- #14: trigger_handler `_update_manifest` retry-exhaustion -------------------------------------


def test_14_manifest_keys_dropped_retries_exhausted(caplog):
    """When the conditional-write retry budget is exhausted, the keys were NOT queued — this must
    be a loud canonical anomaly carrying the dropped count (never count=0-as-ok) emitted at the
    point of loss BEFORE the exception propagates to the caller."""
    s3 = MagicMock()
    # Always read an empty manifest (etag None -> create path) ...
    s3.get_object.side_effect = Exception("no manifest yet")
    # ... and always fail the conditional PUT with a RETRYABLE conflict so the loop exhausts
    # all 8 attempts and raises at the exhaustion point (keys dropped).
    err = Exception("conflict")
    err.response = {"Error": {"Code": "PreconditionFailed"}}
    s3.put_object.side_effect = err
    keys = [
        "contentrepository/B1/chapter1/chapter1-content.md",
        "contentrepository/B2/chapter1/chapter1-content.md",
    ]
    with (
        patch.object(th, "s3", s3),
        patch.object(th, "time", MagicMock()),  # no real sleeps
        caplog.at_level(logging.ERROR),
    ):
        with pytest.raises(Exception):
            th._update_manifest(keys)
    lines = _observ_lines(caplog, "manifest.keys_dropped")
    assert lines, "expected OBSERV manifest.keys_dropped status=error"
    assert any("count=2" in m for m in lines)


def test_14_manifest_keys_dropped_non_retryable(caplog):
    """A non-retryable write error (e.g. AccessDenied) also drops the keys — it too must emit the
    canonical OBSERV at the point of loss before surfacing."""
    s3 = MagicMock()
    s3.get_object.side_effect = Exception("no manifest yet")
    err = Exception("denied")
    err.response = {"Error": {"Code": "AccessDenied"}}
    s3.put_object.side_effect = err
    keys = ["contentrepository/B1/chapter1/chapter1-content.md"]
    with (
        patch.object(th, "s3", s3),
        patch.object(th, "time", MagicMock()),
        caplog.at_level(logging.ERROR),
    ):
        with pytest.raises(Exception):
            th._update_manifest(keys)
    lines = _observ_lines(caplog, "manifest.keys_dropped")
    assert lines, "expected OBSERV manifest.keys_dropped status=error"
    assert any("count=1" in m for m in lines)

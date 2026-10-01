"""Tests for the extension-guess + reject-the-failures net.

Extension routing is a fast guess; routes that fail (declared ext != bytes, or
OCR produced nothing usable) off-ramp to needs-review instead of silently
dropping or feeding garbage downstream.
"""

import os
from unittest.mock import MagicMock, patch

os.environ.setdefault("S3_BUCKET", "test-bucket")

from src.ingestion.review_reject import reject_to_review

# --- shared helper ---


def test_reject_to_review_writes_marker_and_alerts():
    s3 = MagicMock()
    with patch.dict(os.environ, {"NOTIFICATION_TOPIC_ARN": "arn:sns:topic"}):
        sns = MagicMock()
        with patch("boto3.client", return_value=sns):
            key = reject_to_review(
                s3,
                "buck",
                "contentrepository/x/B9.pdf",
                "B9",
                "OCR intake: declared pdf, bytes say html",
                category="ocr-media-mismatch",
            )
    assert key == "needs-review/ocr-media-mismatch/B9.json"
    # marker NOT under contentrepository/ (must never re-trigger parse)
    put = s3.put_object.call_args.kwargs
    assert put["Key"].startswith("needs-review/")
    assert "contentrepository/" not in put["Key"]
    sns.publish.assert_called_once()


def test_reject_to_review_best_effort_on_marker_failure():
    s3 = MagicMock()
    s3.put_object.side_effect = RuntimeError("s3 down")
    # must not raise — rejecting can't crash the handler
    with patch.dict(os.environ, {"NOTIFICATION_TOPIC_ARN": ""}):
        reject_to_review(s3, "buck", "k", "B", "reason")


# --- OCR intake magic-byte mismatch (trigger) ---


def test_ocr_intake_rejects_mismatch_before_gpu_job():
    import lambda_handlers.trigger_handler as th

    class _V:
        is_mismatch = True
        reason = "declared .pdf, bytes are html"

    with (
        patch.object(th, "BUCKET", "buck"),
        patch("boto3.client") as bc,
        patch("src.ingestion.media_detection.detect_media_mismatch", return_value=_V()),
        patch("src.ingestion.review_reject.reject_to_review") as rej,
    ):
        bc.return_value.get_object.return_value = {
            "Body": MagicMock(read=lambda: b"<!doctype html><html>")
        }
        assert th._ocr_media_mismatch("contentrepository/x/B9.pdf", "B9") is True
    rej.assert_called_once()


def test_ocr_intake_passes_matching_file():
    import lambda_handlers.trigger_handler as th

    class _V:
        is_mismatch = False
        reason = ""

    with (
        patch.object(th, "BUCKET", "buck"),
        patch("boto3.client") as bc,
        patch("src.ingestion.media_detection.detect_media_mismatch", return_value=_V()),
    ):
        bc.return_value.get_object.return_value = {
            "Body": MagicMock(read=lambda: b"%PDF-1.5")
        }
        assert th._ocr_media_mismatch("contentrepository/x/B9.pdf", "B9") is False


def test_ocr_intake_fail_open_on_error():
    """A transient S3 error must NOT block a legit doc — proceed to OCR (False)."""
    import lambda_handlers.trigger_handler as th

    with patch.object(th, "BUCKET", "buck"), patch("boto3.client") as bc:
        bc.return_value.get_object.side_effect = RuntimeError("s3 timeout")
        assert th._ocr_media_mismatch("contentrepository/x/B9.pdf", "B9") is False


# --- OCR result validation (merge handler) ---


def _merge_env(monkeypatch):
    import lambda_handlers.ocr_merge_handler as mh

    monkeypatch.setattr(mh, "BUCKET", "buck")
    return mh


def test_merge_rejects_empty_ocr(monkeypatch):
    mh = _merge_env(monkeypatch)
    s3 = MagicMock()
    # no input.md keys -> empty
    s3.get_paginator.return_value.paginate.return_value = [{"Contents": []}]
    with (
        patch.object(mh, "_s3", return_value=s3),
        patch("src.ingestion.review_reject.reject_to_review") as rej,
    ):
        out = mh.merge_ocr_output("B9")
    assert out == ""
    assert rej.call_args.kwargs.get("category") == "ocr-empty" or rej.call_args[0]
    rej.assert_called_once()


def test_merge_rejects_near_empty_ocr(monkeypatch):
    mh = _merge_env(monkeypatch)
    s3 = MagicMock()
    s3.get_paginator.return_value.paginate.return_value = [
        {"Contents": [{"Key": "ocr-output/B9/input/input.md"}]}
    ]
    s3.get_object.return_value = {
        "Body": MagicMock(read=lambda: b"   \n  ")
    }  # whitespace
    with (
        patch.object(mh, "_s3", return_value=s3),
        patch("src.ingestion.review_reject.reject_to_review") as rej,
    ):
        out = mh.merge_ocr_output("B9")
    assert out == ""
    rej.assert_called_once()


def test_merge_promotes_good_ocr(monkeypatch):
    mh = _merge_env(monkeypatch)
    s3 = MagicMock()
    s3.get_paginator.return_value.paginate.return_value = [
        {"Contents": [{"Key": "ocr-output/B9/input/input.md"}]}
    ]
    good = b"# Chapter\n\n" + b"Real OCR text content that is clearly usable. " * 5
    s3.get_object.return_value = {"Body": MagicMock(read=lambda: good)}
    s3.head_object.side_effect = Exception("404")  # no .structured marker (narrative)
    with (
        patch.object(mh, "_s3", return_value=s3),
        patch("src.ingestion.review_reject.reject_to_review") as rej,
    ):
        out = mh.merge_ocr_output("B9")
    assert out == "contentrepository/B9/chapter1/chapter1-content.md"
    rej.assert_not_called()

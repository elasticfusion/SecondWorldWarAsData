"""Tests for the OCR->parse handoff merge Lambda (UNATTENDED_READINESS #1)."""

import os
from unittest.mock import MagicMock, patch

os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
os.environ.setdefault("S3_BUCKET", "dev-wwii-data-pipeline")

from lambda_handlers import ocr_merge_handler as om


def _event(
    status="SUCCEEDED", queue="dev-wwii-chandra-gpu", job_name="chandra-B460", cmd=None
):
    detail = {"status": status, "jobQueue": queue, "jobName": job_name}
    if cmd is not None:
        detail["container"] = {"command": cmd}
    return {"detail": detail}


def test_book_from_command_s3_path():
    d = {
        "jobName": "chandra-B460",
        "container": {
            "command": [
                "s3://dev-wwii-data-pipeline/contentrepository/NARA/B-Series/B 400-499/B460.pdf",
                "s3://dev-wwii-data-pipeline/ocr-output/B460/",
            ]
        },
    }
    assert om._book_from_event(d) == "B460"


def test_book_from_jobname_fallback():
    assert om._book_from_event({"jobName": "chandra-B421"}) == "B421"


def test_handler_ignores_non_succeeded():
    out = om.handler(_event(status="RUNNING"), None)
    assert out["action"] == "none"


def test_handler_ignores_non_chandra_queue():
    out = om.handler(_event(queue="dev-wwii-other-queue"), None)
    assert out["action"] == "none"


def test_merge_whole_pdf_layout():
    """Whole-PDF OCR: ocr-output/{book}/input/input.md (no chunk dir)."""
    s3 = MagicMock()
    paginator = MagicMock()
    paginator.paginate.return_value = [
        {
            "Contents": [
                {"Key": "ocr-output/B460/input/input.md"},
                {"Key": "ocr-output/B460/input/input.html"},  # ignored
                {"Key": "ocr-output/B460/input/input_metadata.json"},  # ignored
            ]
        }
    ]
    s3.get_paginator.return_value = paginator
    s3.get_object.return_value = {"Body": MagicMock(read=lambda: b"# B460 page 1")}
    with patch.object(om, "_s3", return_value=s3):
        out = om.merge_ocr_output("B460")
    assert out == "contentrepository/B460/B460.md"
    put = s3.put_object.call_args.kwargs
    assert put["Key"] == "contentrepository/B460/B460.md"
    assert b"B460 page 1" in put["Body"]


def test_merge_chunked_layout_sorts_pages():
    """Chunked OCR: multiple chunk-*/input/input.md, merged in key order."""
    s3 = MagicMock()
    paginator = MagicMock()
    paginator.paginate.return_value = [
        {
            "Contents": [
                {"Key": "ocr-output/Big/chunk-p0051-0100/input/input.md"},
                {"Key": "ocr-output/Big/chunk-p0001-0050/input/input.md"},
            ]
        }
    ]
    s3.get_paginator.return_value = paginator
    bodies = {
        "ocr-output/Big/chunk-p0001-0050/input/input.md": b"PAGES 1-50",
        "ocr-output/Big/chunk-p0051-0100/input/input.md": b"PAGES 51-100",
    }
    s3.get_object.side_effect = lambda Bucket, Key: {
        "Body": MagicMock(read=lambda k=Key: bodies[k])
    }
    with patch.object(om, "_s3", return_value=s3):
        om.merge_ocr_output("Big")
    merged = s3.put_object.call_args.kwargs["Body"].decode()
    # sorted by key => p0001-0050 chunk first
    assert merged.index("PAGES 1-50") < merged.index("PAGES 51-100")


def test_merge_no_markdown_returns_empty():
    s3 = MagicMock()
    paginator = MagicMock()
    paginator.paginate.return_value = [{"Contents": []}]
    s3.get_paginator.return_value = paginator
    with patch.object(om, "_s3", return_value=s3):
        assert om.merge_ocr_output("Empty") == ""


def test_handler_full_promote_path():
    with patch.object(
        om, "merge_ocr_output", return_value="contentrepository/B460/B460.md"
    ):
        out = om.handler(_event(), None)
    assert out["action"] == "promoted"
    assert out["book"] == "B460"


def test_book_from_command_with_spaces_in_path():
    """B-series PDFs live under '.../B 400-499/B460.pdf' (spaces in path)."""
    d = {
        "jobName": "chandra-B460",
        "container": {
            "command": [
                "s3://dev-wwii-data-pipeline/contentrepository/NARA/B-Series/B 400-499/B460.pdf",
            ]
        },
    }
    assert om._book_from_event(d) == "B460"


def test_handler_no_book_returns_error():
    """A malformed job (no chandra- prefix, no s3 pdf arg) must fail loud, not silently."""
    out = om.handler(_event(job_name="weird-job-name"), None)
    assert out["action"] == "error"


def test_merge_missing_bucket_raises():
    """Missing S3_BUCKET must raise (loud failure), not write to a wrong bucket."""
    with patch.object(om, "BUCKET", ""):
        try:
            om.merge_ocr_output("B460")
            assert False, "expected RuntimeError"
        except RuntimeError as e:
            assert "S3_BUCKET" in str(e)

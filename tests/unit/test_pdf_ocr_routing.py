"""Tests for PDF -> OCR routing in the trigger (Option B)."""

import os
from unittest.mock import MagicMock, patch

os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
os.environ.setdefault("ECS_CLUSTER", "dev-wwii-pipeline")
os.environ.setdefault("CACHE_TABLE", "dev-wwii-api-cache")
os.environ.setdefault("S3_BUCKET", "dev-wwii-data-pipeline")

from lambda_handlers import trigger_handler as th


def test_split_by_media_separates_pdf_from_parseable():
    keys = [
        "contentrepository/NARA/B-Series/B 400-499/B460.pdf",
        "contentrepository/B405/B405.md",
        "contentrepository/x/doc.docx",
    ]
    pdfs, others = th._split_by_media(keys)
    assert pdfs == ["contentrepository/NARA/B-Series/B 400-499/B460.pdf"]
    assert set(others) == {
        "contentrepository/B405/B405.md",
        "contentrepository/x/doc.docx",
    }


def test_submit_ocr_submits_chandra_batch_job():
    batch = MagicMock()
    with patch.object(th, "_batch_client", return_value=batch):
        ok = th._submit_ocr("contentrepository/NARA/B-Series/B 400-499/B460.pdf")
    assert ok is True
    kwargs = batch.submit_job.call_args.kwargs
    assert kwargs["jobQueue"] == "dev-wwii-chandra-gpu"
    assert kwargs["jobDefinition"] == "dev-wwii-chandra"
    cmd = kwargs["containerOverrides"]["command"]
    # whole-PDF job: [s3_input, s3_output_prefix], no page-range
    assert (
        cmd[0]
        == "s3://dev-wwii-data-pipeline/contentrepository/NARA/B-Series/B 400-499/B460.pdf"
    )
    assert cmd[1] == "s3://dev-wwii-data-pipeline/ocr-output/B460/"
    assert len(cmd) == 2


def test_submit_ocr_returns_false_on_error():
    batch = MagicMock()
    batch.submit_job.side_effect = RuntimeError("batch down")
    with patch.object(th, "_batch_client", return_value=batch):
        assert th._submit_ocr("contentrepository/x/y.pdf") is False


def test_submit_ocr_job_name_sanitized():
    """Job name must strip spaces (Batch job names can't contain spaces)."""
    batch = MagicMock()
    with patch.object(th, "_batch_client", return_value=batch):
        th._submit_ocr("contentrepository/NARA/B-Series/B 400-499/B460.pdf")
    name = batch.submit_job.call_args.kwargs["jobName"]
    assert " " not in name
    assert name.startswith("chandra-")

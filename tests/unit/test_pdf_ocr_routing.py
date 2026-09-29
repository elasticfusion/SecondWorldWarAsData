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
    with (
        patch.object(th, "_batch_client", return_value=batch),
        patch.object(th, "dynamo", MagicMock()),
        patch.object(th, "_pdf_has_large_page", return_value=False),
    ):
        ok = th._submit_ocr("contentrepository/NARA/B-Series/B 400-499/B460.pdf")
    assert ok is True
    kwargs = batch.submit_job.call_args.kwargs
    assert kwargs["jobQueue"] == th.OCR_JOB_QUEUE
    assert kwargs["jobDefinition"] == th.OCR_JOB_DEF
    cmd = kwargs["containerOverrides"]["command"]
    # whole-PDF job: [s3_input, s3_output_prefix], no page-range
    assert (
        cmd[0] == f"s3://{th.BUCKET}/contentrepository/NARA/B-Series/B 400-499/B460.pdf"
    )
    assert cmd[1] == f"s3://{th.BUCKET}/ocr-output/B460/"
    assert len(cmd) == 2


def test_submit_ocr_large_page_routes_to_highvram():
    """Anticipate-first: a PDF with an oversized page is routed to the 24GB
    high-VRAM queue at submit, avoiding a wasted 16GB OOM attempt."""
    batch = MagicMock()
    with (
        patch.object(th, "_batch_client", return_value=batch),
        patch.object(th, "dynamo", MagicMock()),
        patch.object(th, "_pdf_has_large_page", return_value=True),
    ):
        ok = th._submit_ocr("contentrepository/x/bigmap.pdf")
    assert ok is True
    assert batch.submit_job.call_args.kwargs["jobQueue"] == th.OCR_HIGHVRAM_QUEUE


def test_submit_ocr_returns_false_on_error():
    batch = MagicMock()
    batch.submit_job.side_effect = RuntimeError("batch down")
    with (
        patch.object(th, "_batch_client", return_value=batch),
        patch.object(th, "dynamo", MagicMock()),
    ):
        assert th._submit_ocr("contentrepository/x/y.pdf") is False


def test_submit_ocr_job_name_sanitized():
    """Job name must strip spaces (Batch job names can't contain spaces)."""
    batch = MagicMock()
    with (
        patch.object(th, "_batch_client", return_value=batch),
        patch.object(th, "dynamo", MagicMock()),
    ):
        th._submit_ocr("contentrepository/NARA/B-Series/B 400-499/B460.pdf")
    name = batch.submit_job.call_args.kwargs["jobName"]
    assert " " not in name
    assert name.startswith("chandra-")


# --- Best-guess page-range chunking (#2) ---


def test_ocr_chunks_image_single_never_chunked():
    with patch.object(th, "_pdf_page_count", return_value=999):  # ignored for images
        assert th._ocr_chunks("contentrepository/x/scan.jpg") == [""]
        assert th._ocr_chunks("contentrepository/x/scan.tif") == [""]
        assert th._ocr_chunks("contentrepository/x/scan.png") == [""]


def test_ocr_chunks_small_pdf_whole():
    with (
        patch.object(th, "_pdf_page_count", return_value=30),
        patch.object(th, "_OCR_CHUNK_PAGES", 50),
    ):
        assert th._ocr_chunks("contentrepository/B/B.pdf") == [""]


def test_ocr_chunks_large_pdf_split():
    with (
        patch.object(th, "_pdf_page_count", return_value=120),
        patch.object(th, "_OCR_CHUNK_PAGES", 50),
    ):
        assert th._ocr_chunks("contentrepository/Big/Big.pdf") == [
            "1-50",
            "51-100",
            "101-120",
        ]


def test_ocr_chunks_unreadable_pdf_whole_fallback():
    """page_count 0 (unreadable/encrypted) -> safe whole-PDF job, not a crash."""
    with patch.object(th, "_pdf_page_count", return_value=0):
        assert th._ocr_chunks("contentrepository/Bad/Bad.pdf") == [""]


def test_ocr_chunks_unknown_media_whole_fallback():
    assert th._ocr_chunks("contentrepository/x/mystery.dat") == [""]


def test_submit_ocr_large_pdf_submits_chunk_set():
    from unittest.mock import MagicMock

    batch = MagicMock()
    with (
        patch.object(th, "_batch_client", return_value=batch),
        patch.object(th, "dynamo", MagicMock()),
        patch.object(th, "_ocr_chunks", return_value=["1-50", "51-100"]),
    ):
        ok = th._submit_ocr("contentrepository/Big/Big.pdf")
    assert ok is True
    assert batch.submit_job.call_count == 2  # one job per chunk
    cmds = [
        c.kwargs["containerOverrides"]["command"]
        for c in batch.submit_job.call_args_list
    ]
    assert any("--page-range" in c and "1-50" in c for c in cmds)
    assert any("--page-range" in c and "51-100" in c for c in cmds)

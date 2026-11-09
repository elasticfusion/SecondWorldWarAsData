"""Tests for the OCR->parse handoff merge Lambda (UNATTENDED_READINESS #1)."""

import os
from unittest.mock import patch

import yaml

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


def test_book_from_image_input():
    """Book name derives from an image input too (Chandra OCRs scanned maps)."""
    d = {
        "jobName": "chandra-normandy",
        "container": {
            "command": [
                "s3://dev-wwii-data-pipeline/contentrepository/maps/normandy.png",
                "s3://dev-wwii-data-pipeline/ocr-output/normandy/",
            ]
        },
    }
    assert om._book_from_event(d) == "normandy"


def test_handler_ignores_non_succeeded():
    out = om.handler(_event(status="RUNNING"), None)
    assert out["action"] == "none"


def test_handler_ignores_non_chandra_queue():
    out = om.handler(_event(queue="dev-wwii-other-queue"), None)
    assert out["action"] == "none"


def _merge(objects, book):
    from tests.fake_s3 import FakeS3

    s3 = FakeS3(objects, bucket=om.BUCKET)
    with (
        patch.object(om, "_s3", return_value=s3),
        patch(
            "src.ingestion.translation.normalize_markdown_to_english",
            lambda md, **k: md,
        ),
        patch("src.ingestion.review_reject.reject_to_review") as rej,
    ):
        out = om.merge_ocr_output(book)
    return out, s3, rej


def test_merge_whole_pdf_layout():
    """Whole-PDF OCR: ocr-output/{book}/input/input.md (no chunk dir)."""
    out, s3, rej = _merge(
        {
            "ocr-output/B460/input/input.md": "# B460 page 1\n\nReal OCR markdown body with "
            "enough usable text to pass the near-empty gate.",
            "ocr-output/B460/input/input.html": "<p>ignored</p>",
            "ocr-output/B460/input/input_metadata.json": "{}",
        },
        "B460",
    )
    assert out == "contentrepository/B460/chapter1/chapter1-content.md"
    keys = s3.keys("contentrepository/", bucket=om.BUCKET)
    assert "contentrepository/B460/chapter1/chapter1-meta.yaml" in keys
    # P2-C1: the deferred-metadata gap is recorded as a needs-review/metadata marker.
    assert [c.kwargs.get("category") for c in rej.call_args_list] == ["metadata"]
    assert "B460 page 1" in s3.text(out, bucket=om.BUCKET)
    raw_meta = s3.text(
        "contentrepository/B460/chapter1/chapter1-meta.yaml", bucket=om.BUCKET
    )
    meta = yaml.safe_load(raw_meta)
    # SAFE yaml.safe_dump (P2-C1): author/series/chapter_title/license are '' (empty), NOT a
    # 'TODO ...' masquerade.
    assert meta["book"] == "B460"
    assert meta["author"] == "" and meta["series"] == "" and meta["license"] == ""
    assert meta["chapter_title"] == ""
    assert "TODO" not in raw_meta


def test_merge_chunked_layout_sorts_pages():
    """Chunked OCR (no manifest: the legacy layout): chunk-*/input/input.md, merged in page
    order whatever the listing order."""
    out, s3, _ = _merge(
        {
            "ocr-output/Big/chunk-p0051-0100/input/input.md": "PAGES 51-100 of real OCR'd "
            "markdown content for the second chunk.",
            "ocr-output/Big/chunk-p0001-0050/input/input.md": "PAGES 1-50 of real OCR'd "
            "markdown content for the first chunk.",
        },
        "Big",
    )
    merged = s3.text(out, bucket=om.BUCKET)
    assert merged.index("PAGES 1-50") < merged.index("PAGES 51-100")
    assert (
        '<a id="page51"></a>' in merged
    )  # each part's first PDF page from its chunk dir


def test_merge_no_markdown_returns_empty():
    out, s3, _ = _merge({}, "Empty")
    assert out == "" and not s3.keys("contentrepository/", bucket=om.BUCKET)


def test_handler_full_promote_path():
    with patch.object(
        om,
        "merge_ocr_output",
        return_value="contentrepository/B460/chapter1/chapter1-content.md",
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

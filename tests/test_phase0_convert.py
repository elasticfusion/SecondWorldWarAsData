"""Tests for the Phase-0 convert step (phase0_convert.py) — EPUB/DOCX -> chapter
structure so parse triggers, mirroring the OCR-merge output convention."""

import os
from unittest.mock import MagicMock, patch

os.environ.setdefault("S3_BUCKET", "dev-wwii-data-pipeline")
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")

import phase0_convert as p0


def test_book_from_key_strips_ext_and_spaces():
    assert (
        p0._book_from_key("contentrepository/books/Patton at the Bulge.epub")
        == "Patton_at_the_Bulge"
    )
    assert p0._book_from_key("x/y/DarkDecember.docx") == "DarkDecember"


def test_convert_key_writes_chapter_structure():
    """A successful convert writes meta THEN content under
    contentrepository/{book}/chapter1/ (the structure phase1 discovery needs)."""
    s3 = MagicMock()
    with (
        patch.object(p0.boto3, "client", return_value=s3),
        patch.object(p0, "convert_to_markdown", return_value="# Patton\n\nText."),
    ):
        out = p0.convert_key("contentrepository/books/Patton at the Bulge.epub")
    assert out == "contentrepository/Patton_at_the_Bulge/chapter1/chapter1-content.md"
    # both meta + content written; meta first
    put_keys = [c.kwargs["Key"] for c in s3.put_object.call_args_list]
    assert put_keys == [
        "contentrepository/Patton_at_the_Bulge/chapter1/chapter1-meta.yaml",
        "contentrepository/Patton_at_the_Bulge/chapter1/chapter1-content.md",
    ]


def test_convert_key_unsupported_ext_skips():
    s3 = MagicMock()
    with patch.object(p0.boto3, "client", return_value=s3):
        assert p0.convert_key("contentrepository/x/thing.pdf") == ""
    s3.put_object.assert_not_called()


def test_convert_key_conversion_failure_returns_empty():
    s3 = MagicMock()
    with (
        patch.object(p0.boto3, "client", return_value=s3),
        patch.object(
            p0, "convert_to_markdown", side_effect=p0.ConverterError("bad epub")
        ),
        # Grok MD-correction second pass also can't recover -> reject-to-review.
        patch.object(p0, "_grok_correct_fallback", return_value=None),
        patch("src.ingestion.review_reject.reject_to_review") as rej,
    ):
        assert p0.convert_key("contentrepository/books/broken.epub") == ""
    rej.assert_called_once()  # escalated to human review, not silently empty


def test_main_requires_convert_key(monkeypatch):
    monkeypatch.delenv("CONVERT_KEY", raising=False)
    assert p0.main() == 2


def test_convert_rejects_media_type_mismatch(monkeypatch, tmp_path):
    """A .epub whose bytes are actually HTML is REJECTED — no convert, a
    needs-review marker is written + an alert published; returns ''."""
    s3 = MagicMock()

    # Make the downloaded temp file contain HTML bytes (mismatch vs .epub).
    def _fake_download(bucket, key, local):
        with open(local, "wb") as fh:
            fh.write(b"<!DOCTYPE html>\n<html>...")

    s3.download_file.side_effect = _fake_download
    convert_called = MagicMock()
    with (
        patch.object(p0.boto3, "client", return_value=s3),
        patch.object(p0, "convert_to_markdown", convert_called),
    ):
        out = p0.convert_key("contentrepository/books/fake.epub")
    assert out == ""
    convert_called.assert_not_called()  # never fed to pandoc
    # a needs-review marker was written (not under contentrepository/)
    marker_writes = [
        c
        for c in s3.put_object.call_args_list
        if c.kwargs.get("Key", "").startswith("needs-review/media-mismatch/")
    ]
    assert marker_writes, "expected a needs-review marker"

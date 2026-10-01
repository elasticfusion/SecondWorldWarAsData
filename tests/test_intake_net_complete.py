"""Tests for the completed intake net:
#1 unrecognized extension at the door -> reject-to-review (not silent drop)
#2 convert failure -> Grok MD-correction second pass -> else reject-to-review
"""

import os
from unittest.mock import MagicMock, patch

os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
os.environ.setdefault("S3_BUCKET", "test-bucket")

from src.ingestion.md_correction import correct_markdown

# --- md_correction helper ---


def test_correct_markdown_recovers():
    client = MagicMock()
    client.chat_completion.return_value = (
        "# Title\n\nA properly reformatted paragraph of recovered prose text here."
    )
    out = correct_markdown(
        "garbled   text\x0c\x0c with artifacts " * 3, grok_client=client
    )
    assert out and out.startswith("# Title")


def test_correct_markdown_skips_trivial_input():
    client = MagicMock()
    assert correct_markdown("tiny", grok_client=client) is None
    client.chat_completion.assert_not_called()  # don't waste a call


def test_correct_markdown_none_when_grok_returns_empty():
    client = MagicMock()
    client.chat_completion.return_value = "   "
    assert correct_markdown("x" * 100, grok_client=client) is None


def test_correct_markdown_never_raises():
    client = MagicMock()
    client.chat_completion.side_effect = RuntimeError("grok down")
    assert correct_markdown("x" * 100, grok_client=client) is None


# --- #1 door reject on unrecognized extension ---


def test_content_keys_rejects_unrecognized_type():
    import lambda_handlers.trigger_handler as th

    with (
        patch.object(th, "BUCKET", "buck"),
        patch.object(th.boto3, "client", return_value=MagicMock()),
        patch("src.ingestion.review_reject.reject_to_review") as rej,
    ):
        out = th._content_keys(
            ["contentrepository/x/weird.xyz", "contentrepository/x/good.pdf"]
        )
    assert out == ["contentrepository/x/good.pdf"]  # recognized kept
    rej.assert_called_once()  # unknown off-ramped, not silently dropped
    assert rej.call_args.kwargs.get("category") == "unrecognized-type"


def test_content_keys_still_ignores_archives_silently():
    import lambda_handlers.trigger_handler as th

    with (
        patch.object(th, "BUCKET", "buck"),
        patch.object(th.boto3, "client", return_value=MagicMock()),
        patch("src.ingestion.review_reject.reject_to_review") as rej,
    ):
        out = th._content_keys(["contentrepository/x/bundle.zip"])
    assert out == []
    rej.assert_not_called()  # archives are expected-ignored, not a failure


# --- #2 convert-fail -> Grok correction -> else reject ---


def test_convert_failure_grok_correction_recovers(tmp_path, monkeypatch):
    import phase0_convert as pc
    from src.ingestion.text_converters import ConverterError

    monkeypatch.setattr(pc, "BUCKET", "buck")
    s3 = MagicMock()
    monkeypatch.setattr(pc, "boto3", MagicMock(client=lambda *a, **k: s3))

    monkeypatch.setattr(
        "src.ingestion.text_converters.convert_to_markdown",
        MagicMock(side_effect=ConverterError("pandoc exploded")),
        raising=False,
    )
    monkeypatch.setattr(
        pc,
        "_grok_correct_fallback",
        lambda local, book: "# Recovered\n\nUsable markdown from Grok second pass.",
    )
    with (
        patch("src.ingestion.media_detection.detect_media_mismatch") as mm,
        patch("src.ingestion.review_reject.reject_to_review") as rej,
    ):
        mm.return_value = MagicMock(is_mismatch=False)
        out = pc.convert_key("contentrepository/x/book.docx")
    # recovered -> promoted (content key returned), NOT rejected
    assert out.endswith("chapter1-content.md")
    rej.assert_not_called()


def test_convert_failure_correction_fails_then_rejects(tmp_path, monkeypatch):
    import phase0_convert as pc
    from src.ingestion.text_converters import ConverterError

    monkeypatch.setattr(pc, "BUCKET", "buck")
    s3 = MagicMock()
    monkeypatch.setattr(pc, "boto3", MagicMock(client=lambda *a, **k: s3))
    monkeypatch.setattr(
        "src.ingestion.text_converters.convert_to_markdown",
        MagicMock(side_effect=ConverterError("pandoc exploded")),
        raising=False,
    )
    monkeypatch.setattr(pc, "_grok_correct_fallback", lambda local, book: None)
    with (
        patch("src.ingestion.media_detection.detect_media_mismatch") as mm,
        patch("src.ingestion.review_reject.reject_to_review") as rej,
    ):
        mm.return_value = MagicMock(is_mismatch=False)
        out = pc.convert_key("contentrepository/x/book.docx")
    assert out == ""
    rej.assert_called_once()
    assert rej.call_args.kwargs.get("category") == "convert-failed"

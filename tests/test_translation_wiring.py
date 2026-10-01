"""Tests for translation wiring across the Phase-0 tracks.

Shared adapter + per-track integration: English/disabled/error pass through
unchanged (fail-safe); non-English is translated to English before promote-to-parse.
"""

import os
from unittest.mock import MagicMock, patch

os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
os.environ.setdefault("S3_BUCKET", "test-bucket")

from src.ingestion import translation as tr

# --- shared adapter ---


def test_normalize_disabled_passthrough():
    with patch.object(tr, "translation_enabled", return_value=False):
        assert tr.normalize_markdown_to_english("Hallo Welt") == "Hallo Welt"


def test_normalize_english_passthrough():
    grok = MagicMock()
    grok.chat_completion.return_value = "English"  # detect -> English
    with patch.object(tr, "translation_enabled", return_value=True):
        out = tr.normalize_markdown_to_english("A clearly English sentence.", grok=grok)
    assert out == "A clearly English sentence."


def test_normalize_non_english_translates():
    grok = MagicMock()
    # first call = detect (German), subsequent = translate
    grok.chat_completion.side_effect = ["German", "The attack began at dawn."]
    with patch.object(tr, "translation_enabled", return_value=True):
        out = tr.normalize_markdown_to_english(
            "Der Angriff begann im Morgengrauen.", grok=grok
        )
    assert out == "The attack began at dawn."


def test_normalize_error_keeps_original():
    grok = MagicMock()
    grok.chat_completion.side_effect = RuntimeError("grok down")
    with patch.object(tr, "translation_enabled", return_value=True):
        out = tr.normalize_markdown_to_english("Der Angriff.", grok=grok)
    assert out == "Der Angriff."  # fail-safe


def test_translation_enabled_env():
    with patch.dict(os.environ, {"PHASE0_TRANSLATE": "1"}):
        assert tr.translation_enabled({}) is True
    assert tr.translation_enabled({"ingestion": {"translate": True}}) is True
    assert tr.translation_enabled({"ingestion": {}}) is False


# --- CONVERT track integration ---


def test_convert_translates_before_promote(monkeypatch):
    import phase0_convert as pc

    monkeypatch.setattr(pc, "BUCKET", "buck")
    s3 = MagicMock()
    monkeypatch.setattr(pc, "boto3", MagicMock(client=lambda *a, **k: s3))
    monkeypatch.setattr(
        pc, "convert_to_markdown", lambda *a, **k: "# Kapitel\n\nDeutscher Text."
    )
    # translation returns English
    monkeypatch.setattr(
        "src.ingestion.translation.normalize_markdown_to_english",
        lambda md, **k: "# Chapter\n\nEnglish text.",
    )
    with patch("src.ingestion.media_detection.detect_media_mismatch") as mm:
        mm.return_value = MagicMock(is_mismatch=False)
        pc.convert_key("contentrepository/x/book.docx")
    # the content.md written carries the ENGLISH markdown
    content_writes = [
        c
        for c in s3.put_object.call_args_list
        if c.kwargs.get("Key", "").endswith("chapter1-content.md")
    ]
    assert content_writes
    assert b"English text" in content_writes[0].kwargs["Body"]


# --- OCR-narrative integration (per-page) ---


def test_ocr_merge_translates_narrative(monkeypatch):
    import lambda_handlers.ocr_merge_handler as mh

    monkeypatch.setattr(mh, "BUCKET", "buck")
    s3 = MagicMock()
    s3.get_paginator.return_value.paginate.return_value = [
        {"Contents": [{"Key": "ocr-output/B9/input/input.md"}]}
    ]
    s3.get_object.return_value = {
        "Body": MagicMock(
            read=lambda: b"# Doc\n\nDeutscher Bericht mit genug Text hier."
        )
    }
    s3.head_object.side_effect = Exception("404")  # not structured -> narrative

    called = {}

    def fake_norm(md, **k):
        called["per_page"] = k.get("per_page")
        return "# Doc\n\nEnglish report with enough text here."

    with (
        patch.object(mh, "_s3", return_value=s3),
        patch("src.ingestion.translation.normalize_markdown_to_english", fake_norm),
    ):
        out = mh.merge_ocr_output("B9")
    assert out == "contentrepository/B9/chapter1/chapter1-content.md"
    assert called["per_page"] is True  # OCR uses per-page detection
    content = [
        c
        for c in s3.put_object.call_args_list
        if c.kwargs.get("Key", "").endswith("chapter1-content.md")
    ][0]
    assert b"English report" in content.kwargs["Body"]


# --- VIDEO segment translation ---


def test_video_translate_segments_preserves_structure(monkeypatch):
    import phase0_video as pv

    class Seg:
        def __init__(self, text, tc, spk):
            self.text, self.timecode, self.speaker = text, tc, spk

    segs = [
        Seg("Der Angriff.", "00:00:01-00:00:03", 0),
        Seg("", "00:00:03-00:00:04", 1),
    ]

    grok = MagicMock()
    grok.chat_completion.side_effect = ["German", "The attack."]
    with (
        patch("src.ingestion.translation.translation_enabled", return_value=True),
        patch("src.grok_client.GrokClient", return_value=grok),
        patch("src.utils.config.load_config", return_value={}),
        patch("src.utils.config.get_paths", return_value={"api_cache": "x"}),
    ):
        pv._translate_segments(segs)
    assert segs[0].text == "The attack."  # translated
    assert segs[0].timecode == "00:00:01-00:00:03"  # timecode preserved
    assert segs[0].speaker == 0  # speaker preserved
    assert segs[1].text == ""  # empty untouched


def test_video_translate_segments_english_untouched(monkeypatch):
    import phase0_video as pv

    class Seg:
        def __init__(self, text):
            self.text = text

    segs = [Seg("The attack began at dawn.")]
    grok = MagicMock()
    grok.chat_completion.return_value = "English"  # detect -> English, no translate
    with (
        patch("src.ingestion.translation.translation_enabled", return_value=True),
        patch("src.grok_client.GrokClient", return_value=grok),
        patch("src.utils.config.load_config", return_value={}),
        patch("src.utils.config.get_paths", return_value={"api_cache": "x"}),
    ):
        pv._translate_segments(segs)
    assert segs[0].text == "The attack began at dawn."  # unchanged

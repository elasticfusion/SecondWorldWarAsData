"""Tests for the document-image vision captioner + its images.py integration.

Covers the reliability policy: grounded caption on high confidence, needs_review
on low confidence / empty description, unknown (never crash, never fabricate) on
error, context-only when there are no image bytes, and that _process_single_image
populates description + caption fields when a captioner is supplied.
"""

from pathlib import Path
from unittest.mock import MagicMock

from src.extraction.image_captioner import ImageCaptioner, ImageCaption


def _captioner(vision_result):
    client = MagicMock()
    client.extract_json_with_image_base64.return_value = vision_result
    return ImageCaptioner(grok_client=client)


def test_caption_high_confidence_accepts():
    c = _captioner(
        {
            "description": "US infantry advancing near St. Vith, winter 1944.",
            "classification": "photograph",
            "confidence": 0.9,
        }
    )
    r = c.caption(b"\xff\xd8jpgbytes", {"book": "B9", "place_name": "St. Vith"})
    assert r.method == "grok-vision"
    assert "St. Vith" in r.description
    assert r.needs_review is False
    assert r.classification == "photograph"


def test_caption_low_confidence_needs_review():
    c = _captioner(
        {
            "description": "possibly a bridge",
            "classification": "photograph",
            "confidence": 0.4,
        }
    )
    r = c.caption(b"jpg", {"book": "B9"})
    assert r.needs_review is True  # below AUTO_ACCEPT_CONFIDENCE


def test_caption_empty_description_needs_review():
    c = _captioner({"description": "", "classification": "unknown", "confidence": 0.9})
    r = c.caption(b"jpg", {})
    assert r.needs_review is True


def test_caption_error_returns_unknown_never_raises():
    client = MagicMock()
    client.extract_json_with_image_base64.side_effect = RuntimeError("vision down")
    c = ImageCaptioner(grok_client=client)
    r = c.caption(b"jpg", {"alt_text": "fallback alt"})
    assert r.method == "error"
    assert r.needs_review is True
    assert r.classification == "unknown"
    assert r.description == "fallback alt"  # falls back to context, never fabricates


def test_caption_no_image_bytes_is_context_only():
    c = ImageCaptioner(grok_client=MagicMock())
    r = c.caption(None, {"alt_text": "A photo of Patton"})
    assert r.method == "context-only"
    assert r.needs_review is True
    assert r.description == "A photo of Patton"


def test_process_single_image_populates_caption(tmp_path):
    import src.extraction.images as im

    # a local image file
    img_path = tmp_path / "x.jpg"
    img_path.write_bytes(b"\xff\xd8fakejpeg")

    cap = MagicMock()
    cap.caption.return_value = ImageCaption(
        description="7th Armored at the St. Vith crossroads.",
        classification="photograph",
        confidence=0.88,
        needs_review=False,
        method="grok-vision",
    )
    img = {"url": "http://x/x.jpg", "alt_text": "crossroads"}
    # no event data / dirs needed for this path; download already done -> inject local
    with_download = MagicMock(return_value=(str(img_path), "jpg"))
    import unittest.mock as m

    with m.patch.object(im, "_download_image", with_download):
        record, local = im._process_single_image(
            img, None, "B9", tmp_path, tmp_path, True, tmp_path, cap
        )
    assert record["description"] == "7th Armored at the St. Vith crossroads."
    assert record["caption_method"] == "grok-vision"
    assert record["needs_review"] is False
    cap.caption.assert_called_once()


def test_process_single_image_no_captioner_keeps_alt(tmp_path):
    import src.extraction.images as im

    img = {"url": "", "alt_text": "just alt text"}
    record, _ = im._process_single_image(
        img, None, "B9", tmp_path, tmp_path, False, tmp_path, None
    )
    # no captioner -> description stays the alt text (prior behavior), no caption fields
    assert record["description"] == "just alt text"
    assert "caption_method" not in record

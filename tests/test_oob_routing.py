"""Tests for OOB structured-reference routing.

A structured-reference doc (e.g. ETO Order of Battle) still gets Chandra OCR, but
its OCR markdown must route to the deterministic phase0_ingest (OOB parser) track,
NOT narrative LLM extraction. Signal: reserved _oob/ path or configured stem ->
.structured sidecar marker -> merge handler forks to the OOB track.
"""

import os
from unittest.mock import MagicMock, patch

os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
os.environ.setdefault("S3_BUCKET", "test-bucket")


# --- trigger: structured-reference signal ---


def test_is_structured_reference_oob_prefix():
    import lambda_handlers.trigger_handler as th

    assert th._is_structured_reference("contentrepository/_oob/eto_oob.pdf") is True
    assert th._is_structured_reference("contentrepository/oob/14th_armored.pdf") is True


def test_is_structured_reference_narrative_is_false():
    import lambda_handlers.trigger_handler as th

    with patch("src.utils.config.load_config", return_value={}):
        assert th._is_structured_reference("contentrepository/NARA/B401.pdf") is False


def test_is_structured_reference_config_stem():
    import lambda_handlers.trigger_handler as th

    cfg = {"ingestion": {"structured_stems": ["eto_order_of_battle"]}}
    with patch("src.utils.config.load_config", return_value=cfg):
        assert (
            th._is_structured_reference("contentrepository/x/ETO_Order_of_Battle.pdf")
            is True
        )


# --- merge handler: structured fork ---


def test_merge_routes_structured_to_oob_not_narrative():
    import lambda_handlers.ocr_merge_handler as mh
    from tests.fake_s3 import FakeS3

    good = "# OOB\n\n" + "| Unit | Commander |\n|---|---|\n|1st Inf|Smith|\n" * 4
    s3 = FakeS3(
        {
            "ocr-output/ETO_OOB/input/input.md": good,
            "ocr-output/ETO_OOB/.structured": "oob",
        }
    )
    with (
        patch.object(mh, "BUCKET", "buck"),
        patch.object(mh, "_s3", return_value=s3),
        patch("boto3.client") as bc,
    ):
        ecs = MagicMock()
        # #44: _launch_phase0_ingest inspects the run_task response — a confirmed launch
        # returns a task (no failures).
        ecs.run_task.return_value = {
            "tasks": [{"taskArn": "arn:aws:ecs:us-east-1:0:task/1"}],
            "failures": [],
        }
        bc.return_value = ecs
        out = mh.merge_ocr_output("ETO_OOB")

    # routed to OOB input layout, NOT contentrepository chapter structure
    assert out == "contentrepository/ETO_OOB/ocr_output/ETO_OOB.md"
    ecs.run_task.assert_called_once()
    assert "phase0-ingest" in ecs.run_task.call_args.kwargs["taskDefinition"]
    # did NOT write the narrative chapter content
    assert not any("chapter1-content.md" in k for k in s3.puts)


def test_merge_narrative_when_no_structured_marker():
    import lambda_handlers.ocr_merge_handler as mh
    from tests.fake_s3 import FakeS3

    good = "# Chapter\n\n" + "Real narrative prose content here, plenty of it. " * 4
    s3 = FakeS3({"ocr-output/B9/input/input.md": good})  # NO .structured marker

    with (
        patch.object(mh, "BUCKET", "buck"),
        patch.object(mh, "_s3", return_value=s3),
        patch("src.ingestion.review_reject.reject_to_review"),
        patch(
            "src.ingestion.translation.normalize_markdown_to_english",
            lambda md, **k: md,
        ),
    ):
        out = mh.merge_ocr_output("B9")

    # normal narrative promote
    assert out == "contentrepository/B9/chapter1/chapter1-content.md"

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

    s3 = MagicMock()
    s3.get_paginator.return_value.paginate.return_value = [
        {"Contents": [{"Key": "ocr-output/ETO_OOB/input/input.md"}]}
    ]
    good = b"# OOB\n\n" + b"| Unit | Commander |\n|---|---|\n|1st Inf|Smith|\n" * 4
    s3.get_object.return_value = {"Body": MagicMock(read=lambda: good)}
    s3.head_object.return_value = {}  # .structured marker EXISTS

    with (
        patch.object(mh, "BUCKET", "buck"),
        patch.object(mh, "_s3", return_value=s3),
        patch("boto3.client") as bc,
    ):
        ecs = MagicMock()
        bc.return_value = ecs
        out = mh.merge_ocr_output("ETO_OOB")

    # routed to OOB input layout, NOT contentrepository chapter structure
    assert out == "contentrepository/ETO_OOB/ocr_output/ETO_OOB.md"
    # phase0-ingest launched
    ecs.run_task.assert_called_once()
    assert "phase0-ingest" in ecs.run_task.call_args.kwargs["taskDefinition"]
    # did NOT write the narrative chapter1-content.md
    written = [c.kwargs.get("Key", "") for c in s3.put_object.call_args_list]
    assert not any("chapter1-content.md" in k for k in written)


def test_merge_narrative_when_no_structured_marker():
    import lambda_handlers.ocr_merge_handler as mh

    s3 = MagicMock()
    s3.get_paginator.return_value.paginate.return_value = [
        {"Contents": [{"Key": "ocr-output/B9/input/input.md"}]}
    ]
    good = b"# Chapter\n\n" + b"Real narrative prose content here, plenty of it. " * 4
    s3.get_object.return_value = {"Body": MagicMock(read=lambda: good)}
    s3.head_object.side_effect = Exception("404")  # NO .structured marker

    with patch.object(mh, "BUCKET", "buck"), patch.object(mh, "_s3", return_value=s3):
        out = mh.merge_ocr_output("B9")

    # normal narrative promote
    assert out == "contentrepository/B9/chapter1/chapter1-content.md"

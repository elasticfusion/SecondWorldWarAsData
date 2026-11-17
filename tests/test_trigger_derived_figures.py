"""Guard: a book's derived figures (``contentrepository/{TITLE}/_figures/...``, written by
src/ingestion/pdf_figures) are the pipeline's own output, not uploads - never queued for OCR
(an image under contentrepository/ would otherwise go to Chandra), parse or review.
"""

import os
from unittest.mock import MagicMock, patch

os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
os.environ.setdefault("S3_BUCKET", "test-bucket")


def _run(keys):
    # Lazy import: see tests/test_trigger_meta_sidecar.py (moto ordering).
    import lambda_handlers.trigger_handler as th

    with (
        patch.object(th, "BUCKET", "buck"),
        patch.object(th.boto3, "client", return_value=MagicMock()),
        patch("src.ingestion.review_reject.reject_to_review") as rej,
    ):
        out = th._content_keys(keys)
    return out, rej


def test_figures_are_skipped_not_queued_nor_rejected(caplog):
    import logging

    keys = [
        "contentrepository/Cross-Channel_Attack/_figures/files/p0545-0.png",
        "contentrepository/Cross-Channel_Attack/_figures/figures.json",
        "contentrepository/_system/Cross-Channel_Attack/_figures/files/p0026-0.png",
        "contentrepository/Cross-Channel_Attack/_figures/",  # a console folder marker
        "contentrepository/Cross-Channel_Attack/chapter8/chapter8-content.md",
    ]
    with caplog.at_level(logging.INFO):
        out, rej = _run(keys)
    assert out == [
        "contentrepository/Cross-Channel_Attack/chapter8/chapter8-content.md"
    ]
    rej.assert_not_called()
    lines = [
        r.getMessage() for r in caplog.records if "derived_figures" in r.getMessage()
    ]
    assert lines == [
        "OBSERV trigger.derived_figures status=ok count=4 (book figures, not new content)"
    ]  # one line per batch, not one per image


def test_a_contributor_cannot_skip_the_av_gate_with_a_figures_folder():
    """SECURITY: only the system namespace is exempt - a contributor's ``_figures`` upload is
    content like any other (AV scan, OCR)."""
    for k in (
        "contentrepository/user-42/Some_Diary/_figures/files/a.jpg",
        "contentrepository/contrib-7/Book/_figures/x.pdf",
        "contentrepository/org-cmh/Book/_figures/x.png",
    ):
        out, rej = _run([k])
        assert out == [k], k
        rej.assert_not_called()


def test_lookalikes_are_still_content():
    """Only the reserved folder right under the title is exempt: a user's own folder of map
    scans is content to OCR."""
    for k in (
        "contentrepository/Maps/images/map1.jpg",  # not the reserved name
        "contentrepository/Book/_Figures/map1.jpg",  # case-sensitive
        "contentrepository/Book/chapter1/_figures/map1.jpg",  # not right under the title
        "contentrepository/_figures/map1.jpg",  # no title
        "other/Book/_figures/map1.jpg",  # wrong root
    ):
        out, rej = _run([k])
        assert out == [k], k
        rej.assert_not_called()


def test_traversal_shapes_are_not_exempt():
    import lambda_handlers.trigger_handler as th

    for k in (
        "contentrepository/Book/_figures/../chapter1/x.pdf",
        "contentrepository/Book//_figures/x.png",
        "contentrepository/Book/_figures//x.png",
        "contentrepository/_system/../Book/_figures/x.png",
    ):
        assert th._is_derived_figure(k) is False, k

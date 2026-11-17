"""The OCR merge waits for every chunk of the book's OCR submission, merges once, keeps the raw
OCR, publishes the book as its chapters - or, with a validation URL, hands it to the Phase 0
cross-check task."""

from unittest.mock import patch

import pytest

from src.ingestion import ocr_chunk_manifest as cm
from tests.fake_s3 import FakeS3

SEP = "\n\n1" + "-" * 48 + "\n\n"
CH1 = (
    "## CHAPTER I\n\n# Roots\n\nThe planners met at Casablanca in January."
    + SEP
    + "They agreed."
)
CH2 = (
    "## CHAPTER II\n\n# Outline\n\nThe staff drew up the outline plan in July."
    + SEP
    + "It was approved."
)


def _book(validation_url="", done=("chunk-p0001-0002", "chunk-p0003-0004")):
    manifest = cm.build_manifest(
        "sub1", ["1-2", "3-4"], "contentrepository/x/B.pdf", validation_url
    )
    texts = {"chunk-p0001-0002": CH1, "chunk-p0003-0004": CH2}
    objects = {cm.manifest_key("B"): manifest.to_json()}
    objects.update({cm.chunk_markdown_key("B", c): texts[c] for c in done})
    return FakeS3(objects)


@pytest.fixture
def mh(monkeypatch):
    import lambda_handlers.ocr_merge_handler as mh

    monkeypatch.setattr(mh, "BUCKET", "buck")
    with (
        patch("src.ingestion.review_reject.reject_to_review"),
        patch(
            "src.ingestion.translation.normalize_markdown_to_english",
            lambda md, **k: md,
        ),
    ):
        yield mh


def _merge(mh, s3, book="B"):
    with patch.object(mh, "_s3", return_value=s3):
        return mh.merge_ocr_output(book)


def test_a_partial_submission_waits_and_publishes_nothing(mh):
    s3 = _book(done=("chunk-p0001-0002",))
    assert _merge(mh, s3) == ""
    assert not s3.keys("contentrepository/") and cm.merged_key("B") not in s3.keys()


def test_the_complete_submission_is_published_as_its_chapters(mh):
    s3 = _book()
    out = _merge(mh, s3)
    assert out == "contentrepository/B/chapter1/chapter1-content.md"
    assert s3.keys("contentrepository/") == [
        "contentrepository/B/chapter1/chapter1-content.md",
        "contentrepository/B/chapter1/chapter1-meta.yaml",
        "contentrepository/B/chapter2/chapter2-content.md",
        "contentrepository/B/chapter2/chapter2-meta.yaml",
    ]
    # every meta before any content: each content upload starts a parse
    order = [k for k in s3.puts if k.startswith("contentrepository/")]
    assert order.index("contentrepository/B/chapter2/chapter2-meta.yaml") < order.index(
        "contentrepository/B/chapter1/chapter1-content.md"
    )
    meta = s3.text("contentrepository/B/chapter2/chapter2-meta.yaml")
    assert (
        "chapter_title: Outline" in meta
        and "source_key: contentrepository/x/B.pdf" in meta
    )
    assert "pdf_pages: 3-4" in meta
    # the paragraph cut by the page break is rejoined, the raw OCR kept as Chandra wrote it
    assert 'in January.<a id="page2"></a>' not in s3.text(out)
    assert "-" * 48 in s3.text("ocr-output/B/merged/raw.md")
    log = s3.text("ocr-output/B/merged/postprocess.json")
    assert '"not cross-checked"' in log or "no validation URL" in log


def test_the_second_last_chunk_does_not_merge_again(mh):
    s3 = _book()
    assert _merge(mh, s3)
    before = list(s3.puts)
    assert _merge(mh, s3) == ""  # claimed by the first
    assert s3.puts == before


def test_a_failed_merge_releases_its_claim_for_the_retry(mh):
    s3 = _book()
    with patch("src.ingestion.ocr_promote.promote", side_effect=RuntimeError("boom")):
        with pytest.raises(RuntimeError):
            _merge(mh, s3)
    assert cm.merged_key("B") not in s3.keys()
    assert _merge(mh, s3)  # the retry merges


def test_a_stale_chunk_of_an_earlier_submission_is_not_merged(mh):
    s3 = _book()
    s3.put_object(
        Bucket="buck", Key="ocr-output/B/chunk-p0001-0050/input/input.md", Body=b"OLD"
    )
    _merge(mh, s3)
    assert "OLD" not in s3.text("ocr-output/B/merged/raw.md")


def test_a_re_ocr_does_not_publish_on_the_earlier_runs_chunks(mh):
    """The first new chunk to finish must not trigger a merge that mixes in the old run's text."""
    s3 = _book()
    for c in ("chunk-p0001-0002", "chunk-p0003-0004"):
        s3.age(cm.chunk_markdown_key("B", c), 7200)  # the earlier submission's output
    s3.put_object(
        Bucket="buck",
        Key=cm.manifest_key("B"),
        Body=cm.build_manifest("sub2", ["1-2", "3-4"]).to_json(),
    )
    assert _merge(mh, s3) == ""  # nothing of sub2 is in yet
    s3.put_object(
        Bucket="buck",
        Key=cm.chunk_markdown_key("B", "chunk-p0001-0002"),
        Body=CH1.encode(),
    )
    assert _merge(mh, s3) == ""  # one of two
    s3.put_object(
        Bucket="buck",
        Key=cm.chunk_markdown_key("B", "chunk-p0003-0004"),
        Body=CH2.encode(),
    )
    assert _merge(mh, s3)  # both new chunks are in


def test_with_a_validation_url_the_cross_check_task_publishes(mh):
    s3 = _book(validation_url="https://www.ibiblio.org/hyperwar/x/index.html")
    with patch.object(mh, "_launch_task", return_value="arn:task/1") as launch:
        out = _merge(mh, s3)
    assert out == "ocr-output/B/merged/raw.md"
    assert launch.call_args[0][:2] == ("B", mh.PHASE0_CROSSCHECK_TASK_DEF)
    # the task is told which submission it publishes (a re-OCR meanwhile supersedes it)
    assert launch.call_args.kwargs["extra_env"] == [
        {"name": "SUBMISSION_ID", "value": "sub1"}
    ]
    assert not s3.keys("contentrepository/")  # the task publishes, after correcting


def test_a_failed_cross_check_launch_hands_nothing_on(mh):
    s3 = _book(validation_url="https://h.org/index.html")
    with patch.object(mh, "_launch_task", return_value=False):
        assert _merge(mh, s3) == ""
    assert cm.merged_key("B") not in s3.keys()  # a re-drive can merge and launch again


def test_a_malformed_manifest_publishes_nothing_and_alerts(mh):
    s3 = FakeS3({cm.manifest_key("B"): b"{bad", "ocr-output/B/input/input.md": CH1})
    with patch.object(mh, "observe_error") as alert:
        assert _merge(mh, s3) == ""
    assert alert.call_args[0][:2] == ("ocr_merge", "manifest_invalid")
    assert not s3.keys("contentrepository/")


def test_without_a_manifest_the_legacy_layout_still_merges(mh):
    s3 = FakeS3({"ocr-output/B/input/input.md": CH1})
    assert _merge(mh, s3) == "contentrepository/B/chapter1/chapter1-content.md"


def test_a_forced_redrive_takes_over_a_claim_whose_task_died(mh):
    s3 = _book()
    assert _merge(mh, s3)  # the first merge claimed it ...
    with patch.object(mh, "_s3", return_value=s3):
        assert mh.merge_ocr_output("B") == ""  # ... a plain retry is refused
        assert mh.merge_ocr_output("B", force=True)  # an operator re-drive is not


def test_the_handler_passes_force_from_the_event(mh):
    event = {
        "force": True,
        "detail": {
            "status": "SUCCEEDED",
            "jobQueue": "dev-wwii-chandra-gpu",
            "jobName": "chandra-B",
        },
    }
    with patch.object(mh, "merge_ocr_output", return_value="k") as merge:
        mh.handler(event, None)
    assert merge.call_args.kwargs == {"force": True}


def test_a_long_held_claim_is_reported_with_the_forced_redrive(mh):
    s3 = _book()
    assert _merge(mh, s3)
    s3.put_object(
        Bucket="buck",
        Key=cm.merged_key("B"),
        Body=b'{"submission": "sub1", "claimed_at": %d}' % (cm.time.time() - 3000),
    )
    with patch.object(mh, "observe_warn") as warn:
        assert _merge(mh, s3) == ""
    assert warn.call_args[0][:2] == ("ocr_merge", "claim_stale")
    assert '"force": true' in warn.call_args.kwargs["note"]

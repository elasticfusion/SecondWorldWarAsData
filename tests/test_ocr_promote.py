"""Publishing an OCR'd book's chapters: logs, held-back apparatus, stale chapters."""

import json
from unittest.mock import patch

from src.ingestion import ocr_promote
from tests.fake_s3 import FakeS3

SEP = "\n\n1" + "-" * 48 + "\n\n"
PARTS = [
    (
        1,
        "# Foreword\n\nA foreword."
        + SEP
        + "## CHAPTER I\n\n# Roots\n\nThe УТАН landing, UTAH beach.",
    ),
    (3, "# Index\n\nAbbeville, 12"),
]


def test_held_back_apparatus_and_the_log():
    s3 = FakeS3()
    keys = ocr_promote.promote(
        s3, "buck", "B", PARTS, source_key="contentrepository/x/B.pdf"
    )
    assert keys == ["contentrepository/B/chapter1/chapter1-content.md"]
    assert "UTAH landing" in s3.text(keys[0])  # sanitised before publishing
    log = json.loads(s3.text("ocr-output/B/merged/postprocess.json"))
    assert log["publication"]["summary"] == {
        "chapters": [1],
        "held_back": ["Front matter", "Index"],
    }
    assert log["sanitise"]["summary"]["homoglyph"] == 1
    assert log["crosscheck"]["status"].startswith("no validation URL")


def test_a_stale_chapter_of_an_earlier_promotion_is_reported_not_deleted():
    s3 = FakeS3({"contentrepository/B/chapter7/chapter7-content.md": "old"})
    with patch("src.utils.observe.observe_warn") as warn:
        ocr_promote.promote(s3, "buck", "B", PARTS, source_key="k")
    assert warn.call_args[0][:2] == ("ocr_promote", "stale_chapters")
    assert warn.call_args.kwargs["stale"] == [
        "contentrepository/B/chapter7/chapter7-content.md"
    ]
    assert "contentrepository/B/chapter7/chapter7-content.md" in s3.keys()


def test_translation_runs_on_each_part():
    seen = []
    ocr_promote.promote(
        FakeS3(),
        "buck",
        "B",
        PARTS,
        source_key="k",
        translate=lambda t: seen.append(t) or t,
    )
    assert len(seen) == 2


def test_a_redrive_does_not_republish_unchanged_chapters():
    s3 = FakeS3()
    ocr_promote.promote(s3, "buck", "B", PARTS, source_key="k")
    before = list(s3.puts)
    ocr_promote.promote(s3, "buck", "B", PARTS, source_key="k")
    again = s3.puts[len(before) :]
    assert [
        k for k in again if k.startswith("contentrepository/")
    ] == []  # no new Phase 1


def test_a_changed_chapter_is_republished():
    s3 = FakeS3()
    ocr_promote.promote(s3, "buck", "B", PARTS, source_key="k")
    changed = [(1, PARTS[0][1].replace("landing", "assault")), PARTS[1]]
    n = len(s3.puts)
    ocr_promote.promote(s3, "buck", "B", changed, source_key="k")
    assert [k for k in s3.puts[n:] if k.startswith("contentrepository/")] == [
        "contentrepository/B/chapter1/chapter1-content.md"
    ]


def test_an_unreadable_chapter_heading_is_reported():
    parts = [(1, "## CHAPTER I\n\nx\n\n1" + "-" * 48 + "\n\n## CHAPTER XIl\n\ny")]
    with patch("src.utils.observe.observe_warn") as warn:
        ocr_promote.promote(FakeS3(), "buck", "B", parts, source_key="k")
    assert warn.call_args[0][:2] == ("ocr_promote", "unreadable_headings")
    assert warn.call_args.kwargs["sections"] == ["CHAPTER XIL"]


def test_a_validation_url_that_checked_no_chapter_is_reported():
    with patch("src.utils.observe.observe_warn") as warn:
        ocr_promote.promote(
            FakeS3(), "buck", "B", PARTS, source_key="k",
            validation_url="https://h.org/index.html", fetch=lambda u: None,
        )  # fmt: skip
    assert warn.call_args[0][:2] == ("ocr_promote", "crosscheck_incomplete")
    assert warn.call_args.kwargs["chapters"] == {"1": "no_transcription"}


def test_a_book_resubmitted_during_the_run_is_not_published():
    s3 = FakeS3()

    def superseded():
        raise RuntimeError("re-submitted")

    import pytest

    with pytest.raises(RuntimeError):
        ocr_promote.promote(
            s3, "buck", "B", PARTS, source_key="k", still_current=superseded
        )
    assert not s3.keys("contentrepository/")  # nothing published over the newer run


def test_a_changed_meta_alone_republishes_the_content_so_phase_1_reparses():
    s3 = FakeS3()
    ocr_promote.promote(s3, "buck", "B", PARTS, source_key="first/source.pdf")
    n = len(s3.puts)
    ocr_promote.promote(
        s3, "buck", "B", PARTS, source_key="second/source.pdf"
    )  # same text
    again = [k for k in s3.puts[n:] if k.startswith("contentrepository/")]
    assert again == [
        "contentrepository/B/chapter1/chapter1-meta.yaml",
        "contentrepository/B/chapter1/chapter1-content.md",
    ]

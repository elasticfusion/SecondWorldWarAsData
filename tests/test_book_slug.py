"""A book's slug is IMMUTABLE (owner rule 2026-11-11) and is derived in ONE place, by two pinned
rules: a SOURCE document is named by its file, CHAPTER CONTENT by its folder - and a source and the
chapters Phase 0 makes from it get the same slug. A change to these values re-keys future uploads
relative to existing books (their BookIDs and EventIDs): make it only deliberately."""

import os

import pytest

os.environ.setdefault("S3_BUCKET", "test-bucket")

from src.ingestion import book_slug as bs  # noqa: E402
from src.utils.deterministic_ids import book_id, event_id  # noqa: E402

SOURCES = [
    ("contentrepository/USA-E-XChannel/USA-E-XChannel.pdf", "USA-E-XChannel"),
    ("contentrepository/books/Patton at the Bulge.epub", "Patton_at_the_Bulge"),
    (
        "contentrepository/Foo/part1.pdf",
        "part1",
    ),  # the folder a source sits in names nothing
    ("contentrepository/_system/videos/Battle of the Bulge.mp4", "Battle_of_the_Bulge"),
    ("s3://dev-wwii-data-pipeline/contentrepository/x/B405.pdf", "B405"),
    ("USA-E-XChannel", "USA-E-XChannel"),  # a bare name
    ("contentrepository/x/Bad Name!.pdf", bs.UNKNOWN),
    ("contentrepository/x/..", bs.UNKNOWN),
    ("", bs.UNKNOWN),
]
CONTENT = [
    ("contentrepository/USA-E-XChannel/chapter8/chapter8-content.md", "USA-E-XChannel"),
    ("contentrepository/Foo/notes.md", "Foo"),
    ("contentrepository/_system/Foo/chapter1/chapter1-content.md", "Foo"),
    ("contentrepository/user-42/My Book/chapter1/chapter1-content.md", "My_Book"),
    ("contentrepository/Foo.md", "Foo"),  # a loose file: its stem
    ("contentrepository/Bad Name!/x.md", bs.UNKNOWN),
]


@pytest.mark.parametrize("key, slug", SOURCES)
def test_a_source_is_named_by_its_file(key, slug):
    assert bs.book_slug(key) == slug


@pytest.mark.parametrize("key, slug", CONTENT)
def test_chapter_content_is_named_by_its_folder(key, slug):
    assert bs.content_slug(key) == slug


@pytest.mark.parametrize("key, _slug", [s for s in SOURCES if s[1] != bs.UNKNOWN])
def test_a_source_and_its_chapters_are_one_book(key, _slug):
    slug = bs.book_slug(key)
    chapter = f"contentrepository/{slug}/chapter1/chapter1-content.md"
    assert bs.content_slug(chapter) == slug
    assert book_id(bs.content_slug(chapter)) == book_id(slug)


@pytest.mark.parametrize("key, _slug", SOURCES)
def test_every_source_path_derives_the_same_slug(key, _slug):
    """The OCR/convert/video submits, the AV scan and the Phase-0 tasks."""
    import lambda_handlers.trigger_handler as th
    import phase0_convert
    import phase0_video
    from src.ingestion import av_scan

    expected = bs.book_slug(key)
    assert th._safe_stem_from_key(key) == expected
    assert av_scan._book_from_key(key) == expected
    assert phase0_convert._book_from_key(key) == expected
    assert phase0_video._book_from_key(key) == expected


@pytest.mark.parametrize("key, _slug", CONTENT)
def test_every_content_path_derives_the_same_slug(key, _slug):
    """The title gate and Phase 1/2's BOOK_NAME."""
    import lambda_handlers.trigger_handler as th

    assert (th._title_from_key(key) or bs.UNKNOWN) == bs.content_slug(key)


def test_the_ids_are_pinned():
    # golden values: these must never change for an existing book
    assert book_id("USA-E-XChannel") == "57SJMAQBZYEWDEHACZVT8EZ7AP"
    # chapter 8's EventID as it is in S3 (output/content/USA-E-XChannel/)
    assert (
        event_id("USA-E-XChannel", "The Sixth of June") == "761S0P9QED2Q16K3PJ2QGAHC5E"
    )
    assert book_id("USA-E-XChannel") != book_id("USA-E-XChannel-8")

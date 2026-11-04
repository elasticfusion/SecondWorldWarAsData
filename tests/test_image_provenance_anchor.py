"""Guard: every source-book image traces to its source.

XChannel ch8: 6 figures had EventID/Sub-eventID/SourceSectionID all null. The parser hard-coded
``paragraph_number=0`` for every image, and linking only matched an image URL inside a sub-event's
fulltext — so a figure placed BETWEEN paragraphs got no link at all.
"""

from types import SimpleNamespace

from src import parser as P
from src.extraction import images as I

EID, S1, S2 = (
    "01M6J3NGH7SB27Q1Q9QAKG9H7N",
    "01M6J3NGB3DR9QD7VT6ST9Y8SH",
    "01M6J3NGJ6MPN7Q9HDRMAZP4Z8",
)
EVENT = {
    "Chapter": "The Sixth of June",
    "Event": {
        "EventID": EID,
        "Sub-events": [
            {
                "Sub-eventID": S1,
                "Sub-event_fulltext": {"Paragraph_2": "a", "Paragraph_3": "b"},
            },
            {"Sub-eventID": S2, "Sub-event_fulltext": {"Paragraph_5": "c"}},
        ],
    },
}


def _doc(paras, images):
    return SimpleNamespace(
        paragraphs=[SimpleNamespace(text=t, absolute_number=n) for n, t in paras],
        images=[
            SimpleNamespace(url=u, resource_id=None, paragraph_number=0) for u in images
        ],
    )


def test_parser_anchors_images_to_preceding_paragraph():
    content = (
        "Intro words before.\n\n![x](http://h/img-1.jpg)\n\n"
        "First real paragraph text here.\n\n![y](http://h/img-2.jpg)\n\n"
        "Second paragraph of the chapter.\n"
    )
    doc = _doc(
        [
            (2, "First real paragraph text here."),
            (3, "Second paragraph of the chapter."),
        ],
        ["http://h/img-1.jpg", "http://h/img-2.jpg"],
    )
    P._anchor_images_to_paragraphs(doc, content)
    assert [i.paragraph_number for i in doc.images] == [
        2,
        2,
    ]  # before-all -> first paragraph


def test_unlocatable_image_stays_zero_and_is_observed(caplog):
    import logging

    caplog.set_level(logging.WARNING)
    doc = _doc([(2, "Para text.")], ["http://h/missing.jpg"])
    P._anchor_images_to_paragraphs(doc, "Para text.\n")
    assert doc.images[0].paragraph_number == 0
    assert any("parser.image_unanchored" in r.getMessage() for r in caplog.records)


def test_anchor_paragraph_inside_sub_event_links_that_sub_event():
    ctx = I._find_sub_event_for_image({"url": "u", "paragraph_number": 3}, EVENT)
    assert ctx["EventID"] == EID and ctx["Sub-eventID"] == S1


def test_figure_between_sub_events_links_chapter_event_not_a_guess():
    ctx = I._find_sub_event_for_image({"url": "u", "paragraph_number": 4}, EVENT)
    assert ctx["EventID"] == EID and ctx["Sub-eventID"] is None


def test_no_event_file_still_none():
    assert I._find_sub_event_for_image({"url": "u", "paragraph_number": 4}, {}) is None


def test_fulltext_url_match_still_wins():
    ev = {
        "Event": {
            "EventID": EID,
            "Sub-events": [
                {
                    "Sub-eventID": S2,
                    "Sub-event_fulltext": {"Paragraph_5": "see http://h/i.jpg"},
                }
            ],
        }
    }
    ctx = I._find_sub_event_for_image(
        {"url": "http://h/i.jpg", "paragraph_number": 1}, ev
    )
    assert ctx["Sub-eventID"] == S2

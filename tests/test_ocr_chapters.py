"""An OCR'd book is split at its own chapter headings: citation is by chapter and paragraph."""

import pytest

from src.ingestion.ocr_chapters import paginated_book, split_book

SEP = "\n\n{}" + "-" * 48 + "\n\n"


def _chunk(*pages):
    out = pages[0]
    for i, page in enumerate(pages[1:], start=1):
        out += SEP.format(i) + page
    return out


BOOK = [
    (1, _chunk("# Foreword\n\nA foreword.", "# Contents\n\nI. Roots ... 1")),
    (
        3,
        _chunk(
            "## CHAPTER I\n\n# The Roots of Strategy\n\nText one.",
            "More of one.\n\n## CHAPTER II\n\n# Outline Overlord\n\nText two.",
            "# Appendix A\n\n## DIGEST OF OPERATION\n\nDigest.",
            "# Appendix B\n\nFuehrer Headquarters\n\nOrders.",
            "# Glossary\n\nAAF Army Air Forces",
            "# Bibliographical Note\n\nSources.",
            "# Index\n\nAbbeville, 12",
        ),
    ),
]


def _sections():
    return split_book(paginated_book(BOOK))


def test_pages_are_anchored_with_their_pdf_page():
    book = paginated_book(BOOK)
    assert '<a id="page1"></a>' in book and '<a id="page9"></a>' in book
    assert "-" * 48 not in book


def test_sections_and_what_is_published():
    got = [(s.kind, s.label, s.number, s.first_page, s.last_page) for s in _sections()]
    assert got == [
        ("front", "Front matter", 0, 1, 2),
        ("chapter", "CHAPTER I", 1, 3, 4),
        ("chapter", "CHAPTER II", 2, 4, 4),
        ("appendix", "Appendix A", 3, 5, 5),
        ("appendix", "Appendix B", 4, 6, 6),
        ("matter", "Glossary", 0, 7, 7),
        ("matter", "Bibliographical Note", 5, 8, 8),
        ("matter", "Index", 0, 9, 9),
    ]


def test_titles():
    titles = {s.label: s.chapter_title for s in _sections()}
    assert titles["CHAPTER I"] == "The Roots of Strategy"
    assert titles["Appendix A"] == "Appendix A: DIGEST OF OPERATION"
    assert titles["Appendix B"] == "Appendix B"  # opens with its document, not a title
    assert titles["Bibliographical Note"] == "Bibliographical Note"


def test_a_chapter_opening_mid_page_carries_that_pages_anchor():
    two = next(s for s in _sections() if s.label == "CHAPTER II")
    assert two.text.startswith('<a id="page4"></a>\n\n## CHAPTER II')
    one = next(s for s in _sections() if s.label == "CHAPTER I")
    assert "More of one." in one.text and "CHAPTER II" not in one.text


def test_a_section_does_not_end_on_the_next_sections_page_anchor():
    one = next(s for s in _sections() if s.label == "Appendix A")
    assert not one.text.rstrip().endswith("</a>")


def test_a_document_without_chapters_is_one_chapter():
    (only,) = split_book(
        paginated_book([(1, _chunk("Report of action.", "### Notes\n\nmore"))])
    )
    assert (only.number, only.kind, only.first_page, only.last_page) == (
        1,
        "chapter",
        1,
        2,
    )


def test_arabic_and_roman_chapter_numbers():
    book = paginated_book(
        [(1, _chunk("# Chapter 4\n\n# Four\n\nx", "## CHAPTER XIV\n\n# Fourteen\n\ny"))]
    )
    assert [s.number for s in split_book(book)] == [4, 14]


def test_a_repeated_running_head_is_text_not_a_new_chapter():
    book = paginated_book(
        [
            (
                1,
                _chunk(
                    "## CHAPTER VIII\n\n# The Sixth\n\nA.",
                    "## CHAPTER VIII\n\nstill the same chapter.",
                ),
            )
        ]
    )
    (only,) = split_book(book)
    assert only.number == 8 and "still the same chapter" in only.text
    assert only.warnings == ["repeated heading 'CHAPTER VIII' kept as text"]


def test_an_unreadable_chapter_number_is_held_back_and_reported_not_numbered():
    book = paginated_book(
        [(1, _chunk("## CHAPTER I\n\nx", "## CHAPTER XIl\n\ny", "## CHAPTER III\n\nz"))]
    )
    got = [(s.label, s.number, s.warnings) for s in split_book(book)]
    assert got[1] == (
        "CHAPTER XIL",
        0,
        ["unreadable chapter number in 'CHAPTER XIL': held back"],
    )
    assert [g[1] for g in got] == [1, 0, 3]


def test_a_chapter_titled_introduction_is_still_a_chapter():
    book = paginated_book(
        [
            (
                1,
                _chunk(
                    "## CHAPTER I\n\n# Introduction\n\nThe text of the chapter.",
                    "# Glossary\n\nAAF",
                ),
            )
        ]
    )
    one, glossary = split_book(book)
    assert (one.number, one.title) == (1, "Introduction")
    assert "The text of the chapter." in one.text and (
        glossary.kind,
        glossary.number,
    ) == ("matter", 0)


def test_appendix_numbers_follow_their_letters_not_their_order():
    book = paginated_book(
        [
            (
                1,
                _chunk(
                    "## CHAPTER I\n\nx",
                    "# Appendix A\n\na",
                    "# Appendix C\n\nc",
                    "# Bibliographical Note\n\nb",
                ),
            )
        ]
    )
    assert [(s.label, s.number) for s in split_book(book)] == [
        ("CHAPTER I", 1),
        ("Appendix A", 2),
        ("Appendix C", 4),  # B's heading was missed: C keeps its number
        ("Bibliographical Note", 5),
    ]


def test_roman_numerals_must_be_canonical():
    from src.ingestion.ocr_chapters import _roman_value as value

    assert [value(t) for t in ("I", "IV", "IX", "XIV", "XL", "XCIX", "C", "xi", "12")] == [
        1, 4, 9, 14, 40, 99, 100, 11, 12,
    ]  # fmt: skip
    assert [value(t) for t in ("XIl", "IIII", "VX", "XIL", "", "0")] == [None] * 6


def test_a_digit_slip_in_a_chapter_number_is_held_back_not_absorbed():
    book = paginated_book([(1, _chunk("## CHAPTER I\n\nx", "## CHAPTER I1\n\ny"))])
    one, slip = split_book(book)
    assert (one.number, slip.number, slip.label) == (1, 0, "CHAPTER I1")
    assert slip.warnings == ["unreadable chapter number in 'CHAPTER I1': held back"]


def test_a_heading_that_merely_starts_with_chapter_is_not_a_chapter():
    book = paginated_book([(1, _chunk("## CHAPTER I\n\nx", "## Chapter Summary\n\ny"))])
    (only,) = split_book(book)
    assert "Chapter Summary" in only.text

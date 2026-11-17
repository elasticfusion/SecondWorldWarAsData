"""Phase 0 cross-check of an OCR'd book against its validation URL: chapters matched to their
transcription pages, corrected only where the book settles it, proved by a second check, and
left as read when anything is off."""

from src.ingestion.ocr_chapters import Section
from src.ingestion.ocr_crosscheck import (
    correct_sections,
    from_chandra,
    to_chandra,
    transcription_pages,
)

BASE = "https://www.ibiblio.org/hyperwar/USA/USA-E-XChannel/"
INDEX = (
    "<html><body>"
    '<a href="USA-E-XChannel-preface.html">FOREWORD</a>'
    '<a href="USA-E-XChannel-1.html">THE ROOTS OF STRATEGY</a>'
    '<a href="USA-E-XChannel-2.html">OUTLINE OVERLORD (JANUARY-JULY 1943)</a>'
    '<a href="USA-E-XChannel-G.html">DIVISIONS AVAILABLE TO GERMANY ON 6 JUNE 1944</a>'
    '<a href="USA-E-XChannel-1.html#page3">3</a>'
    '<a href="../../Glossary.html">THE ROOTS OF STRATEGY</a>'
    '<a href="https://evil.example/x.html">OUTLINE OVERLORD</a>'
    "</body></html>"
)


def _section(number, label, title, text="", first=1):
    return Section("chapter", label, title, first, first, text, number)


def test_round_trip_to_chandra_and_back():
    text = '<a id="page7"></a>\n\nOne.\n\n<a id="page8"></a>\n\nTwo\n\n<a id="page9"></a>\n\nThree.\n'
    first, md, pages = to_chandra(text)
    assert (first, pages) == (7, [7, 8, 9])
    assert "1" + "-" * 48 in md and "2" + "-" * 48 in md
    assert from_chandra(md, pages) == text


def test_no_reversible_pages_means_no_cross_check():
    assert to_chandra("no anchors") is None
    assert to_chandra('text first<a id="page1"></a> x') is None
    assert (
        to_chandra('<a id="page1"></a> a <a id="page3"></a> b') is None
    )  # out of sequence


def test_chapters_are_matched_to_their_pages_by_title_in_the_same_folder():
    sections = [
        _section(1, "CHAPTER I", "The Roots of Strategy"),
        _section(2, "CHAPTER II", "Outline Overlord (January–July 1943)"),
        _section(
            17, "Appendix G", "DIVISIONS AVAILABLE TO GERMANY ON 6 JUNE 1944 (By Type)"
        ),
        _section(18, "Appendix H", ""),
    ]
    urls = transcription_pages(BASE + "index.html", sections, lambda u: INDEX)
    assert urls == {
        1: BASE + "USA-E-XChannel-1.html",
        2: BASE + "USA-E-XChannel-2.html",
        17: BASE + "USA-E-XChannel-G.html",
    }


def test_a_page_without_chapter_links_is_the_one_documents_transcription():
    url = "https://h.org/report.html"
    urls = transcription_pages(
        url, [_section(1, "", "")], lambda u: "<p>The report.</p>"
    )
    assert urls == {1: url}


def test_an_unreachable_validation_url_checks_nothing():
    assert (
        transcription_pages(
            "https://h.org/x.html", [_section(1, "", "")], lambda u: None
        )
        == {}
    )


WORDS = (
    "The division landed on the beach at dawn and moved inland toward the town of Carentan "
    "where the Schwarzwalder regiment held the bridge "
)
BODY = (WORDS * 6).strip()
HTML = f"<html><body><p>{BODY}</p><p>{BODY}</p></body></html>"


def _ocr_section(ocr_first_page_text):
    text = (
        f'<a id="page1"></a>\n\n{ocr_first_page_text}\n\n<a id="page2"></a>\n\n{BODY}\n'
    )
    return Section("chapter", "", "", 1, 2, text, 1)


def test_a_chapter_is_corrected_where_the_book_settles_it():
    s = _ocr_section(BODY.replace("Schwarzwalder", "Schwarzwaldor", 1))
    book = correct_sections([s], "https://h.org/x.html", lambda u: HTML)
    (check,) = book.chapters
    assert check.status == "corrected" and check.corrections["applied"] == 1
    assert "Schwarzwaldor" not in s.text and s.text.count("Schwarzwalder") == 12
    assert book.summary()["applied"] == 1


def test_a_chapter_that_does_not_align_is_left_as_read():
    original = _ocr_section(BODY).text
    s = _ocr_section(BODY)
    unrelated = (
        "<p>" + " ".join(["completely different words about the navy"] * 30) + "</p>"
    )
    (check,) = correct_sections(
        [s], "https://h.org/x.html", lambda u: unrelated
    ).chapters
    assert check.status == "not_aligned" and s.text == original


def test_a_chapter_without_a_transcription_is_left_as_read():
    s = _ocr_section(BODY)
    pages = {"https://h.org/x.html": HTML}
    sections = [
        s,
        Section("chapter", "CHAPTER II", "Other", 3, 3, '<a id="page3"></a>\n\nx\n', 2),
    ]
    book = correct_sections(sections, "https://h.org/x.html", pages.get)
    assert {c.number: c.status for c in book.chapters} == {
        1: "no_transcription",
        2: "no_transcription",
    }


# --- review findings ---------------------------------------------------------------------------


def test_an_exact_title_beats_a_title_that_merely_starts_the_same():
    index = (
        '<a href="x-longer.html">THE ALLIED PLAN FOR THE INVASION OF FRANCE</a>'
        '<a href="x-exact.html">THE ALLIED PLAN</a>'
    )
    base = "https://h.org/b/index.html"
    urls = transcription_pages(
        base, [_section(1, "CHAPTER I", "The Allied Plan")], lambda u: index
    )
    assert urls == {1: "https://h.org/b/x-exact.html"}


def test_each_page_goes_to_one_chapter_best_match_first():
    index = '<a href="a.html">THE ALLIED PLAN</a><a href="b.html">THE ALLIED PLAN FOR FRANCE</a>'
    sections = [
        _section(1, "CHAPTER I", "The Allied Plan for France"),
        _section(2, "CHAPTER II", "The Allied Plan"),
    ]
    urls = transcription_pages("https://h.org/b/index.html", sections, lambda u: index)
    assert urls == {1: "https://h.org/b/b.html", 2: "https://h.org/b/a.html"}


def test_a_transcription_holding_a_fraction_of_the_chapter_is_not_the_chapter():
    s = _ocr_section(BODY)
    short = "<html><body><p>" + " ".join(BODY.split()[:20]) + "</p></body></html>"
    (check,) = correct_sections([s], "https://h.org/x.html", lambda u: short).chapters
    assert check.status == "not_aligned"


def test_one_failing_chapter_does_not_stop_the_book():
    from unittest.mock import patch

    from src.ingestion import ocr_crosscheck
    from src.ingestion import pdf_text_corrections as C

    first = _ocr_section(BODY.replace("Schwarzwalder", "Schwarzwaldor", 1))
    first.title = "A"
    second = _ocr_section(BODY.replace("Schwarzwalder", "Schwarzwaldor", 1))
    second.number, second.label, second.title = 2, "CHAPTER II", "B"
    pages = {
        "https://h.org/b/index.html": '<a href="a.html">A</a><a href="b.html">B</a>',
        "https://h.org/b/a.html": HTML,
        "https://h.org/b/b.html": HTML,
    }
    real, calls = C.correct_verified, []

    def fail_the_first(*a, **k):
        calls.append(1)
        if len(calls) == 1:
            raise ValueError("page count changed")
        return real(*a, **k)

    with patch.object(ocr_crosscheck.C, "correct_verified", fail_the_first):
        book = correct_sections(
            [first, second], "https://h.org/b/index.html", pages.get
        )
    assert [c.status for c in book.chapters] == ["error", "corrected"]
    assert book.chapters[0].error == "ValueError: page count changed"
    assert "Schwarzwaldor" in first.text and "Schwarzwaldor" not in second.text


def test_a_chapter_with_nothing_to_correct_is_verified_not_corrected():
    s = _ocr_section(BODY)
    (check,) = correct_sections([s], "https://h.org/x.html", lambda u: HTML).chapters
    assert check.status == "verified_no_change"


def test_only_chapters_need_a_person_when_unchecked():
    from src.ingestion.ocr_crosscheck import BookCheck, ChapterCheck

    book = BookCheck("u")
    book.chapters = [
        ChapterCheck(1, "CHAPTER I", kind="chapter", status="no_transcription"),
        ChapterCheck(11, "Appendix A", kind="appendix", status="no_transcription"),
        ChapterCheck(2, "CHAPTER II", kind="chapter", status="corrected"),
        ChapterCheck(3, "CHAPTER III", kind="chapter", status="no_pages"),
        ChapterCheck(4, "CHAPTER IV", kind="chapter", status="verified_no_change"),
    ]
    assert [c.number for c in book.unchecked_chapters()] == [1, 3]


def test_a_page_without_a_charset_is_not_read_as_latin_1():
    from unittest.mock import MagicMock, patch

    from src.ingestion import ocr_crosscheck

    resp = MagicMock(
        status_code=200,
        headers={"content-type": "text/html"},
        apparent_encoding="utf-8",
    )
    resp.encoding = "ISO-8859-1"
    resp.text = "Saint-Lô"
    resp.content = b"<html><body>no charset declared</body></html>"
    with patch("src.utils.url_hygiene.safe_get", return_value=resp):
        ocr_crosscheck.fetch_html("https://h.org/x.html")
    assert resp.encoding == "utf-8"
    resp.headers = {"content-type": "text/html; charset=windows-1252"}
    resp.encoding = "windows-1252"
    with patch("src.utils.url_hygiene.safe_get", return_value=resp):
        ocr_crosscheck.fetch_html("https://h.org/x.html")
    assert resp.encoding == "windows-1252"  # a declared charset is respected


def test_a_page_declaring_its_charset_in_a_meta_tag_is_read_with_it():
    from unittest.mock import MagicMock, patch

    from src.ingestion import ocr_crosscheck

    resp = MagicMock(
        status_code=200,
        headers={"content-type": "text/html"},
        apparent_encoding="ascii",
    )
    resp.encoding = "ISO-8859-1"
    resp.text = "Saint-Lô"
    resp.content = b'<html><head><meta http-equiv="Content-Type" content="text/html; charset=windows-1252">'
    with patch("src.utils.url_hygiene.safe_get", return_value=resp):
        ocr_crosscheck.fetch_html("https://h.org/x.html")
    assert resp.encoding == "windows-1252"
    resp.content = b'<meta charset="no-such-charset">'
    with patch("src.utils.url_hygiene.safe_get", return_value=resp):
        ocr_crosscheck.fetch_html("https://h.org/x.html")
    assert resp.encoding == "ascii"  # an unknown declaration falls back to detection


# --- the list of differences a person must look at ----------------------------------------------

# The name is printed twice: too few for the book to establish it against the transcription's
# spelling (three or more would make the transcription the odd one out), so it needs a person.
NAME_BODY = (
    BODY
    + " The company under Capt. F. V. Schwarzwaldor crossed the canal at dawn. "
    + "Later Capt. F. V. Schwarzwaldor held the far bank against the counterattack."
)


def test_a_difference_the_rules_cannot_settle_is_listed_for_a_person():
    """The book never prints the transcription's spelling, so the OCR reading stands - and the
    page, both readings and the context are recorded."""
    html = (
        "<html><body><p>"
        + NAME_BODY.replace("Schwarzwaldor", "Schwarzwalder")
        + "</p><p>"
        + BODY
        + "</p></body></html>"
    )
    s = _ocr_section(NAME_BODY)  # page 1: the name twice; page 2: more of the chapter
    (check,) = correct_sections([s], "https://h.org/x.html", lambda u: html).chapters
    assert check.review_total == check.review.__len__() > 0
    item = check.review[0]
    assert (item["pdf"], item["html"], item["stream"]) == (
        "Schwarzwaldor",
        "Schwarzwalder",
        "narrative",
    )
    assert item["pdf_page"] == 1 and "Capt F V" in item["context"]
    assert "Schwarzwaldor" in s.text  # not changed: the PDF's reading stands


def test_the_review_list_is_capped_but_always_counted():
    from src.ingestion import ocr_crosscheck as oc
    from src.ingestion.pdf_html_crosscheck import CrossCheck, Difference

    diffs = [
        Difference("review", "a", "b", 1, 1, None, "ctx")
        for _ in range(oc._MAX_REVIEW + 5)
    ]
    diffs.append(Difference("book_form", "c", "d", 1, 1, None, ""))
    check = oc.ChapterCheck(1, "CHAPTER I")
    oc._record_review(check, [CrossCheck("narrative", 1, 1, 1, diffs)])
    assert (
        check.review_total == oc._MAX_REVIEW + 5 and len(check.review) == oc._MAX_REVIEW
    )


def test_the_books_summary_counts_the_review_items():
    from src.ingestion.ocr_crosscheck import BookCheck, ChapterCheck

    book = BookCheck("u")
    book.chapters = [
        ChapterCheck(1, "I", review_total=3),
        ChapterCheck(2, "II", review_total=4),
    ]
    assert book.summary()["review"] == 7
    assert "review" in book.to_dict()["chapters"][0]

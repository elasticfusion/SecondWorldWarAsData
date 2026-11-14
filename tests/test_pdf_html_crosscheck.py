"""PDF (authoritative) vs HyperWar HTML cross-check (src/ingestion/pdf_html_crosscheck).

Cases are the real Cross-Channel Attack ch. VIII disagreements (CMH PDF: its 2010 OCR text layer
and Chandra's OCR, vs HyperWar): "lOlst"/"101st", "A rmy"/"Army", "Vednesday"/"Wednesday",
"VII"/"VIII", "Spieled"/"Speidel", "Stag"/"Stagg", "Dallmann"/"Dollmann", "УТАН"/"UTAH",
"co-ordinated"/"coordinated", "Schwarzwaldor"/"Schwarzwalder".
"""

from __future__ import annotations

import importlib.util
import json
import sys
from collections import Counter
from pathlib import Path

import pytest

from src.ingestion import pdf_html_crosscheck as X
from src.ingestion.pdf_html_crosscheck import Token

# Whole-book counts in the shape pdf_vocabulary produces (lower-case words and compounds).
BOOK = Counter(
    {"army": 300, "a": 900, "last": 40, "minute": 12, "lastminute": 0, "101st": 200,
     "wednesday": 9, "position": 30, "positions": 25, "fall": 40, "fail": 30,
     "speidel": 14, "dollmann": 40, "dallmann": 1, "killed": 120, "marshall": 30,
     "co-ordinated": 28, "coordinated": 1, "co": 30, "ordinated": 28, "counterattack": 82,
     "counter": 5, "attack": 90, "per": 40, "cent": 3, "percent": 51, "périers": 3,
     "johnson": 60, "johnson's": 1, "vii": 200, "viii": 2}
)  # fmt: skip


def _t(*texts, kind="body"):
    return [Token(t, page=5, printed=300, kind=kind) for t in texts]


def _j(*pairs):
    """PDF tokens with their source joiners: ("co", ""), ("ordinated", "-")."""
    return [Token(w, page=5, printed=300, joined=j) for w, j in pairs]


@pytest.mark.parametrize(
    "html,pdf,cls",
    [
        (["Army"], ["A", "rmy"], "ocr_spacing"),
        (
            ["last", "minute"],
            ["lastminute"],
            "review",
        ),  # the book never prints either form
        (["101st"], ["lOlst"], "ocr_confusion"),
        (["Wednesday"], ["Vednesday"], "ocr_confusion"),
        (["O"], ["0"], "review"),  # a lone digit is a claim on either side
        (["VIII"], ["VII"], "review"),  # a corps number is a claim, never guessed
        (["13"], ["18"], "review"),
        (["positions"], ["position"], "review"),  # two valid words
        (["fall"], ["fail"], "review"),  # look-alikes, but both are words of the book
        ([], ["exercises"], "pdf_only"),
        (["exercises"], [], "html_only"),
    ],
)
def test_classes(html, pdf, cls):
    assert X.classify(_t(*html), _t(*pdf), BOOK) == cls


def test_without_the_books_evidence_nothing_but_homoglyphs_is_decided():
    assert X.classify(_t("Army"), _t("A", "rmy")) == "review"
    assert X.classify(_t("101st"), _t("lOlst")) == "review"
    assert X.classify(_t("Periers"), _t("Périers")) == "review"
    assert X.classify(_t("UTAH"), _t("УТАН")) == "ocr_homoglyph"


def test_the_book_itself_decides_which_reading_is_wrong():
    vocab = Counter({"speidel": 14, "spieled": 0, "dollmann": 40, "dallmann": 1})
    assert X.classify(_t("Spieled"), _t("Speidel"), vocab) == "html_error"
    assert X.classify(_t("Dollmann"), _t("Dallmann"), vocab) == "ocr_frequency"


def test_frequency_needs_a_clear_majority():
    vocab = Counter({"position": 30, "positions": 25})
    assert X.classify(_t("positions"), _t("position"), vocab) == "review"


def test_numbers_are_never_decided_by_frequency():
    vocab = Counter({"vii": 200, "viii": 2, "11th": 40, "111th": 1})
    assert X.classify(_t("VIII"), _t("VII"), vocab) == "review"
    assert X.classify(_t("11th"), _t("111th"), vocab) == "review"  # a unit designation


def test_a_numeral_look_alike_is_never_blamed_on_the_pdf():
    """The transcription's "Il" against the book's "II" (letters only: could be a word) is
    looked at; its "l500" against "1500" (a digit inside no valid number) is its own error. The
    PDF's number is never the one changed."""
    vocab = Counter({"ii": 50, "1500": 3})
    assert X.classify(_t("Il"), _t("II"), vocab) == "review"
    assert X.classify(_t("l500"), _t("1500"), vocab) == "html_error"


def test_running_heads_and_captions_are_layout():
    assert X.classify([], _t("THE", "SIXTH", "OF", "JUNE", kind="layout")) == "layout"
    assert X.classify([], _t("CROSSING", "THE", "CHANNEL", "Columns", "of")) == "layout"


def test_a_sentence_opening_with_a_unit_name_is_not_a_caption():
    assert X.classify([], _t("XIX", "CORPS", "ordered", "the", "attack")) == "pdf_only"


def test_alignment_reports_each_difference_with_its_page():
    html = [
        Token(w, printed=300) for w in "the 101st Airborne Army was at Utah".split()
    ]
    pdf = _t("the", "lOlst", "Airborne", "A", "rmy", "was", "at", "Utah")
    check = X.crosscheck("narrative", html, pdf, vocab=BOOK)
    assert [(d.cls, d.html, d.pdf) for d in check.differences] == [
        ("ocr_confusion", "101st", "lOlst"),
        ("ocr_spacing", "Army", "A rmy"),
    ]
    assert all(d.pdf_page == 5 and d.printed_page == 300 for d in check.differences)
    assert check.correctable_share == 1.0


def test_words_that_do_not_correspond_are_not_paired():
    """An equal-length replace pairs word for word only when each pair is a misreading."""
    html = [Token(w) for w in "held the town".split()]
    pdf = _t("lost", "a", "village")
    check = X.crosscheck("narrative", html, pdf, vocab=BOOK)
    assert [(d.html, d.pdf) for d in check.differences] == [
        ("held the town", "lost a village")
    ]


def test_shares_ignore_layout():
    check = X.CrossCheck("n", 10, 10, 9)
    check.differences = [
        X.Difference("layout", "", "THE SIXTH OF JUNE", 1, 1, None, ""),
        X.Difference("ocr_spacing", "Army", "A rmy", 1, 1, None, ""),
        X.Difference("html_error", "Spieled", "Speidel", 1, 1, None, ""),
        X.Difference("review", "VIII", "VII", 1, 1, None, ""),
    ]
    assert check.correctable_share == pytest.approx(1 / 3)
    assert check.decided_share == pytest.approx(2 / 3)
    assert X.summary(check)["real_differences"] == 3
    assert X.summary(check)["aligned_share"] == 0.9


# --- the PDF text layer reader, on a generated two-column page ------------------------------------


@pytest.fixture
def two_column_pdf(tmp_path):
    fitz = pytest.importorskip("fitz")
    doc = fitz.open()
    page = doc.new_page(width=480, height=700)
    page.insert_text((200, 50), "301", fontsize=10)  # running head page number
    page.insert_text((40, 100), "The troops moved toward the river at", fontsize=10.4)
    page.insert_text((40, 114), "dawn and the fortifica-", fontsize=10.4)
    page.insert_text((40, 128), "tions fell before noon.22", fontsize=10.4)
    page.insert_text((260, 100), "Second column begins here.", fontsize=10.4)
    page.insert_text((40, 600), "22 Hoffmann Report.", fontsize=7.6)  # a footnote
    path = tmp_path / "p.pdf"
    doc.save(str(path))
    return fitz.open(str(path))


def test_pdf_reader_orders_columns_joins_hyphens_and_separates_notes(two_column_pdf):
    tokens = X.pdf_tokens(two_column_pdf, 1, 1)
    body = [t.text for t in tokens if t.kind == "body"]
    notes = [t.text for t in tokens if t.kind == "note"]
    assert body[:3] == ["The", "troops", "moved"]
    assert "fortifications" in body  # "fortifica-" + "tions"
    assert body[-4:] == ["Second", "column", "begins", "here"]  # left column first
    assert notes == ["22", "Hoffmann", "Report"]
    assert {t.printed for t in tokens} == {301}
    assert [t.text for t in tokens if t.kind == "layout"] == ["301"]


def test_pdf_vocabulary_counts_the_whole_book(two_column_pdf):
    vocab = X.pdf_vocabulary(two_column_pdf)
    assert vocab["column"] == 1 and vocab["the"] == 3
    assert X.pdf_vocabulary(two_column_pdf, skip=[1]) == Counter()


def test_pdf_vocabulary_counts_compounds_printed_within_a_line(tmp_path):
    fitz = pytest.importorskip("fitz")
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((40, 100), "co-ordinated attack, co-ordinated again", fontsize=10)
    path = tmp_path / "c.pdf"
    doc.save(str(path))
    vocab = X.pdf_vocabulary(fitz.open(str(path)))
    assert vocab["co-ordinated"] == 2 and vocab["ordinated"] == 2


def test_token_vocabulary_counts_words_and_compounds():
    tokens = _j(("un", ""), ("co", "-"), ("ordinated", "-"), ("attack", ""), ("co", ""))
    vocab = X.token_vocabulary(tokens)
    assert vocab["un-co-ordinated"] == 1 and vocab["co"] == 2 and vocab["attack"] == 1


def test_a_word_broken_over_three_lines_joins_whole():
    out: list = []
    for line in ("the in-", "dica-", "ted route"):
        X._add_tokens(out, line, 1, 300, "body")
    assert [t.text for t in out] == ["the", "indicated", "route"]


# --- the HTML side ------------------------------------------------------------------------------------


def test_html_streams_split_narrative_and_notes_and_carry_pages():
    html = (
        '<html><body><a id="page300"></a><p>The 101st landed.</p>'
        '<center><img src="x.jpg"><br>A CAPTION</center>'
        '<p>It held <a id="page301"></a>the town.</p><h3>Footnotes</h3>'
        '<p>[<a href="#cn1" name="fn1">1</a>] Hoffmann Report.</p></body></html>'
    )
    narrative, notes = X.html_streams(html)
    assert [t.text for t in narrative] == "The 101st landed It held the town".split()
    assert [t.printed for t in narrative][-2:] == [301, 301]
    assert "CAPTION" not in [t.text for t in narrative]
    assert [t.text for t in notes] == ["1", "Hoffmann", "Report"]


def test_html_streams_split_at_the_notes_heading_not_a_mention():
    html = "<p>Footnotes were kept in the war diary.</p><h3>Footnotes</h3><p>[1] A note.</p>"
    narrative, notes = X.html_streams(html)
    assert [t.text for t in narrative][:2] == ["Footnotes", "were"]
    assert [t.text for t in notes] == ["1", "A", "note"]
    assert X.html_streams("<p>No notes here.</p>")[1] == []


def test_html_streams_drop_the_transcribers_credit():
    html = (
        "<html><body><p>Text.</p><h3>Footnotes</h3><p>[1] A note.</p>"
        "<center><address>Transcribed and formatted for HTML by Someone</address></center>"
        "</body></html>"
    )
    _narrative, notes = X.html_streams(html)
    assert [t.text for t in notes] == ["1", "A", "note"]


# --- rules added for the Chandra reading (Phase 0's OCR of a scanned PDF) ------------------------


def test_words_are_normalised_before_comparing():
    assert X.words("Johnson’s men") == ["Johnson's", "men"]
    assert X.words("Pe\u0301riers") == ["Périers"]  # decomposed accent: one word


def test_joined_words_record_compounds_and_digit_grouping_only():
    assert X.joined_words("co-ordinated 1,240 men, 6 June") == [
        ("co", ""), ("ordinated", "-"), ("1", ""), ("240", ","),
        ("men", ""), ("6", ""), ("June", ""),
    ]  # fmt: skip
    # a hyphen then a space is a German suspended compound as often as a kept line break
    assert X.joined_words("Atlantik- und Kanal-kueste") == [
        ("Atlantik", ""), ("und", ""), ("Kanal", ""), ("kueste", "-"),
    ]  # fmt: skip
    assert X.joined_words("-leading hyphen") == [("leading", ""), ("hyphen", "")]


def test_the_books_established_form_decides_spacing():
    co = _j(("co", ""), ("ordinated", "-"))
    counter = _j(("counter", ""), ("attack", "-"))
    # the book prints "co-ordinated": HyperWar normalised it, the PDF is right
    assert X.classify(_t("coordinated"), co, BOOK) == "book_form"
    # the book never hyphenates "counterattack" mid-line: a kept line-break hyphen
    assert X.classify(_t("counterattack"), counter, BOOK) == "ocr_spacing"
    assert X.classify(_t("Army"), _t("A", "rmy"), BOOK) == "ocr_spacing"
    # two established words and the book uses both forms: look at the page
    assert X.classify(_t("percent"), _t("per", "cent"), BOOK) == "review"
    # the OCR joined a compound the book hyphenates
    assert X.classify(_t("co", "ordinated"), _t("coordinated"), BOOK) == "ocr_spacing"
    assert X.classify(_t("1240"), _j(("1", ""), ("240", ",")), BOOK) == "book_form"


def test_a_form_attested_once_is_no_evidence():
    """ "half-hearted" once - the disputed token itself - is not the book's established form."""
    vocab = Counter({"half-hearted": 1, "half": 9, "hearted": 1})
    pdf = _j(("half", ""), ("hearted", "-"))
    assert X.classify(_t("halfhearted"), pdf, vocab) == "review"


def test_accents_the_transcription_dropped_are_the_books():
    assert X.classify(_t("Periers"), _t("Périers"), BOOK) == "html_accent"
    # the OCR dropping an accent is not settled
    assert X.classify(_t("Périers"), _t("Periers"), BOOK) == "review"
    # an accent printed once may be the OCR's own invention
    assert X.classify(_t("Vichy"), _t("Vîchy"), Counter({"vîchy": 1})) == "review"
    # a ligature is not an accent
    assert X.classify(_t("first"), _t("ﬁrst"), Counter({"ﬁrst": 3})) != "html_accent"


def test_cyrillic_and_greek_small_caps_are_homoglyphs():
    assert X.classify(_t("UTAH"), _t("УТАН")) == "ocr_homoglyph"
    assert X.classify(_t("OMAHA"), _t("ОМАНА")) == "ocr_homoglyph"
    assert X.classify(_t("TOE"), _t("ΤΟΕ")) == "ocr_homoglyph"  # Greek capitals
    assert X.words("on УТАН Beach") == ["on", "УТАН", "Beach"]


def test_a_look_alike_on_the_transcriptions_side_is_its_error():
    vocab = Counter({"killed": 120, "kilied": 0, "101st": 200})
    assert X.classify(_t("kilied"), _t("killed"), vocab) == "html_error"
    assert X.classify(_t("101st"), _t("lOlst"), vocab) == "ocr_confusion"


def test_a_reading_absent_from_the_book_is_the_transcriptions():
    assert X.classify(_t("Marsball"), _t("Marshall"), BOOK) == "html_error"
    # absent, but the PDF reading occurs only twice (it counts itself): not enough
    assert (
        X.classify(
            _t("Schwarzwalder"), _t("Schwarzwaldor"), Counter({"schwarzwaldor": 2})
        )
        == "review"
    )


def test_possessives_are_never_decided_by_frequency():
    assert X.classify(_t("Johnson"), _t("Johnson's"), BOOK) == "review"


def test_a_second_ocr_of_the_same_pages_settles_a_word():
    """The 2010 text layer of the same pages: an independent OCR of the authoritative print."""
    vocab = Counter({"schwarzwaldor": 2, "schwarzwalder": 1, "landemoeglichkeiten": 1})
    second = Counter({"schwarzwalder": 3, "landemoeglichkeiten": 1})
    # it reads the transcription's word and never Chandra's: Chandra misread
    assert (
        X.classify(_t("Schwarzwalder"), _t("Schwarzwaldor"), vocab, second)
        == "ocr_crossread"
    )
    # both OCRs read the print alike, the transcription differs: the transcription is wrong
    html, pdf = _t("Landemocglichheiten"), _t("Landemoeglichkeiten")
    assert X.classify(html, pdf, vocab, second) == "html_error"
    # never for a number, nor an accent (the 2010 layer has none)
    assert X.classify(_t("11th"), _t("111th"), vocab, Counter({"11th": 2})) == "review"
    assert (
        X.classify(_t("Periers"), _t("Périers"), vocab, Counter({"periers": 5}))
        == "review"
    )


def test_evidence_that_blames_different_sides_is_reviewed():
    # the book: the PDF reading is the rare one; the second OCR: it is what is printed
    vocab, second = Counter({"aeaf": 30, "aeaff": 1}), Counter({"aeaff": 2})
    assert X.classify(_t("AEAF"), _t("AEAFF"), vocab, second) == "review"
    # look-alikes too: frequency blames the transcription, the second OCR blames the PDF
    vocab, second = Counter({"killed": 120}), Counter({"kilied": 2})
    assert X.classify(_t("kilied"), _t("killed"), vocab, second) == "review"
    # both blaming the PDF is agreement, not a conflict
    utah = Counter({"utah": 40})
    assert X.classify(_t("UTAH"), _t("УГАН"), utah, utah) == "ocr_frequency"


def test_a_look_alike_keeps_the_class_of_its_evidence():
    """W/V look-alikes the second OCR settles are reported as its verdict, not collapsed."""
    vocab, second = Counter({"wednesday": 9}), Counter({"wednesday": 1})
    assert (
        X.classify(_t("Wednesday"), _t("Vednesday"), vocab, second) == "ocr_crossread"
    )


def test_two_real_words_are_never_an_ocr_fix_even_when_rare():
    """ "fail" twice (counting itself) beside "fall" 15 times: below the 10x frequency rule, and
    "fail" is a word of the book - a real difference."""
    vocab = Counter({"fall": 15, "fail": 2})
    assert X.classify(_t("fall"), _t("fail"), vocab) == "review"


_SEP = "\n\n{n}" + "-" * 48 + "\n\n"


def _chandra(*pages):
    return "".join(
        (_SEP.format(n=i) if i else "") + page for i, page in enumerate(pages)
    )


def test_chandra_paragraphs_images_notes_captions_and_page_hyphens():
    markdown = _chandra(
        "The troops in-\n\n"
        "![Aerial photo of a model-written description](_page_0_Picture_1.jpeg)\n\n"
        "**PARACHUTE TROOPS** *marching onto airfield.*\n\n"
        "NINTH AIR FORCE B-26 OVER BEACHHEAD at *Lion-sur-Mer*.\n*Note markings.*\n\n"
        "<sup>25</sup> FUSA Rpt.\ncontinued on a second line.",
        "formation got through.\n\nXIX CORPS ordered the attack on *Texas* positions.",
    )
    stats: Counter = Counter()
    tokens = X.chandra_tokens(markdown, 297, {297: 278, 298: 279}, stats=stats)
    body = [t.text for t in tokens if t.kind == "body"]
    # an image paragraph's alt text is model-written, not the book's: dropped
    assert "Aerial" not in body and "description" not in body
    assert body[:3] == [
        "The",
        "troops",
        "information",
    ]  # joined across captions and a page
    # a note wrapped over two lines is one note
    assert [t.text for t in tokens if t.kind == "note"][:4] == [
        "25",
        "FUSA",
        "Rpt",
        "continued",
    ]
    layout = [t.text for t in tokens if t.kind == "layout"]
    assert layout[:2] == ["PARACHUTE", "TROOPS"] and "NINTH" in layout
    # a sentence opening with a unit name, with an italic ship name, stays body text
    assert "XIX" in body and "Texas" in body
    assert {t.printed for t in tokens if t.text == "XIX"} == {279}
    assert stats == Counter({"body": 3, "layout": 2, "note": 1, "image_dropped": 1})


def test_a_note_continued_on_the_next_page_is_placed_by_the_transcription():
    """Chandra prints a note's continuation as a plain paragraph anywhere on the page. Only its
    STREAM is taken from the transcription - and a transcription typo ("dunng") in its opening
    words does not defeat the placement."""
    narrative = _t(*"The battalion moved south toward the river at dawn".split())
    notes = _t(
        *"interviews conducted dunng July 1944 by Col S L A Marshall with officers".split()
    )
    markdown = _chandra(
        "The battalion moved",
        "conducted during July 1944 by Col. S. L. A. Marshall with officers.\n\n"
        "south toward the river at dawn.",
    )
    stats: Counter = Counter()
    tokens = X.chandra_tokens(markdown, 1, None, (narrative, notes), stats)
    assert [t.text for t in tokens if t.kind == "body"] == [t.text for t in narrative]
    # the PDF's own words, not the transcription's ("during", not "dunng")
    assert [t.text for t in tokens if t.kind == "note"][:3] == [
        "conducted",
        "during",
        "July",
    ]
    assert stats["note:placed"] == 1
    # without the transcription the paragraph stays body text
    assert any(
        t.text == "conducted" and t.kind == "body"
        for t in X.chandra_tokens(markdown, 1)
    )


def test_empty_inputs():
    assert X.chandra_tokens("", 1) == []
    assert X.html_streams("") == ([], [])
    check = X.crosscheck("narrative", [], [], vocab=BOOK)
    assert check.differences == [] and X.summary(check)["aligned_share"] == 0.0


def _moved_case(pdf_text: str, html_text: str):
    narrative = X.CrossCheck("narrative", 1, 1, 1)
    notes = X.CrossCheck("notes", 1, 1, 1)
    narrative.differences = [X.Difference("pdf_only", "", pdf_text, 5, 300, None, "")]
    notes.differences = [
        X.Difference("html_only", html_text, "", None, 300, None, ""),
        X.Difference("html_only", "73", "", None, 301, None, ""),  # too short to pair
    ]
    return narrative, notes


def test_text_in_the_other_stream_is_moved_not_missing():
    narrative, notes = _moved_case("62 4th Cav Gp AAR", "62 4th Cav Gp AAR")
    assert X.reconcile_moved([narrative, notes]) == {"across": 1, "within": 0}
    assert [d.cls for d in narrative.differences + notes.differences] == [
        "moved", "moved", "html_only",
    ]  # fmt: skip


def test_a_fragment_inside_a_longer_block_is_not_moved():
    narrative, notes = _moved_case(
        "the battalion held the bridge until the 4th Cav Gp arrived", "4th Cav Gp"
    )
    assert X.reconcile_moved([narrative, notes]) == {"across": 0, "within": 0}
    # "Gp" inside "Gpx" is not a word match either
    narrative, notes = _moved_case("62 4th Cav Gpx", "62 4th Cav Gp")
    assert X.reconcile_moved([narrative, notes]) == {"across": 0, "within": 0}


def test_each_html_block_pairs_once_and_same_stream_moves_are_counted_apart():
    check = X.CrossCheck("notes", 1, 1, 1)
    check.differences = [
        X.Difference("html_only", "97 Under the German command", "", None, 1, None, ""),
        X.Difference("pdf_only", "", "97 Under the German command", 2, 1, None, ""),
        X.Difference("pdf_only", "", "97 Under the German command", 3, 1, None, ""),
    ]
    assert X.reconcile_moved([check]) == {"across": 0, "within": 1}
    assert [d.cls for d in check.differences] == ["moved", "moved", "pdf_only"]


def test_reviews_are_grouped_so_a_systematic_difference_is_one_finding():
    check = X.CrossCheck("narrative", 1, 1, 1)
    check.differences = [
        X.Difference("review", "VIII", "VII", 1, 278, None, ""),
        X.Difference("ocr_spacing", "Army", "A rmy", 1, 279, None, ""),
        X.Difference("review", "that", "than", 1, 304, None, ""),
        X.Difference("review", "VIII", "VII", 1, 290, None, ""),
    ]
    assert X.grouped_reviews(check) == [
        ("VIII", "VII", 2, [278, 290]),
        ("that", "than", 1, [304]),
    ]


# --- the CLI end to end, on a generated page ------------------------------------------------------


def _cli():
    spec = importlib.util.spec_from_file_location(
        "crosscheck_cli",
        Path(__file__).resolve().parent.parent / "scripts/crosscheck_pdf_html.py",
    )
    assert spec is not None and spec.loader is not None
    cli = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = cli  # dataclasses resolve their module through sys.modules
    spec.loader.exec_module(cli)
    return cli


_PAGE_HTML = (
    '<html><body><a id="page301"></a><p>The troops moved toward the river at dawn and '
    "the fortifications fell before noon. Second column begins here.</p>"
    "<h3>Footnotes</h3><p>23 Hoffmann Report.</p></body></html>"
)
_PAGE_MD = (
    "The troops moved toward the river at dawn and the fortifica-\n\n"
    "tions fell before noon. Second column begins here.\n\n<sup>22</sup> Hoffmann Report."
)


@pytest.fixture
def cli_inputs(two_column_pdf, tmp_path):
    pdf = tmp_path / "book.pdf"
    two_column_pdf.save(str(pdf))
    html = tmp_path / "page.html"
    html.write_text(_PAGE_HTML, encoding="utf-8")
    md = tmp_path / "chandra.md"
    md.write_text(_PAGE_MD, encoding="utf-8")
    out = tmp_path / "out"
    args = ["--pdf", str(pdf), "--html", str(html), "--first", "1", "--last", "1"]
    return args + ["--out", str(out)], md, out


def test_cli_writes_report_and_grouped_review(cli_inputs, capsys):
    args, md, out = cli_inputs
    assert _cli().main(args + ["--chandra-md", str(md), "--crops"]) == 0
    report = json.loads((out / "report.json").read_text(encoding="utf-8"))
    assert report["pdf_text"] == "chandra"
    assert len(report["sha256"]) == 3
    assert report["chandra_paragraphs"] == {"body": 2, "note": 1}
    assert report["differences"]["narrative"] == []
    notes = report["differences"]["notes"]
    # a note number is a claim: never decided, always looked at
    assert [(d["html"], d["pdf"], d["cls"], d["printed_page"]) for d in notes] == [
        ("23", "22", "review", 301)
    ]
    review = (out / "review.md").read_text(encoding="utf-8")
    assert "| 23 | 22 | 1 | 301 |" in review
    # Chandra tokens carry no position: the reading "22" occurs twice on the page, so the
    # whole page is saved for the look
    assert [p.name for p in (out / "crops").iterdir()] == ["page-p1.png"]
    err = capsys.readouterr().err
    assert "OBSERV crosscheck: crops: 0 review lines located, 1 whole pages" in err
    assert "0 HTML-only items with no PDF page" in err


def test_cli_rejects_missing_and_unreadable_inputs(cli_inputs, tmp_path, capsys):
    args, _md, _out = cli_inputs
    assert _cli().main(args + ["--chandra-md", str(tmp_path / "absent.md")]) == 2
    assert "--chandra-md" in (err := capsys.readouterr().err) and "no such file" in err
    corrupt = tmp_path / "corrupt.pdf"
    corrupt.write_bytes(b"not a pdf")
    bad = list(args)
    bad[bad.index("--pdf") + 1] = str(corrupt)
    assert _cli().main(bad) == 2
    assert "not a readable PDF" in capsys.readouterr().err


def test_cli_reads_the_embedded_text_layer_without_chandra(cli_inputs):
    args, _md, out = cli_inputs
    assert _cli().main(args) == 0
    report = json.loads((out / "report.json").read_text(encoding="utf-8"))
    assert report["pdf_text"] == "embedded text layer" and report["chandra_md"] is None
    # every HTML word aligns with the text layer's reading of the page
    assert report["streams"][0]["aligned_share"] == 1.0


def test_cli_rejects_pages_outside_the_pdf(cli_inputs, capsys):
    args, _md, _out = cli_inputs
    bad = [a if a != "1" else "0" for a in args]  # --first 0 --last 0
    assert _cli().main(bad) == 2
    assert "1 <= first <= last <= 1" in capsys.readouterr().err


def test_cli_rejects_a_chandra_file_of_other_pages(cli_inputs, capsys):
    args, md, _out = cli_inputs
    md.write_text(_PAGE_MD + _SEP.format(n=1) + "another page", encoding="utf-8")
    assert _cli().main(args + ["--chandra-md", str(md)]) == 2
    assert "has 2 pages but --first..--last spans 1" in capsys.readouterr().err


def test_cli_fails_when_a_stream_does_not_align(cli_inputs, capsys):
    """A chapter's Chandra file of the wrong pages: few differences, but almost nothing
    aligned - the run fails instead of reporting a clean result."""
    args, md, _out = cli_inputs
    html = Path(args[args.index("--html") + 1])
    sentence = "The regiment crossed the causeway under fire and held the far bank. "
    html.write_text(
        f"<html><body><p>{sentence * 6}</p></body></html>", encoding="utf-8"
    )
    md.write_text("Entirely different words on this page.", encoding="utf-8")
    assert _cli().main(args + ["--chandra-md", str(md)]) == 1
    assert "narrative aligned only 0%" in capsys.readouterr().err


def test_cli_writes_the_corrected_text_and_proves_it(cli_inputs, capsys):
    """--corrected-out: Chandra's small caps "УТАН" become "UTAH" (the PDF's own letters), the
    log records it, and the corrected text cross-checks clean."""
    args, md, out = cli_inputs
    html = Path(args[args.index("--html") + 1])
    html.write_text(_PAGE_HTML.replace("the river", "UTAH Beach"), encoding="utf-8")
    md.write_text(_PAGE_MD.replace("the river", "УТАН Beach"), encoding="utf-8")
    fixed = out.parent / "fixed"
    assert (
        _cli().main(args + ["--chandra-md", str(md), "--corrected-out", str(fixed)])
        == 0
    )
    assert "toward UTAH Beach at dawn" in (fixed / "corrected.md").read_text(
        encoding="utf-8"
    )
    log = json.loads((fixed / "corrections.json").read_text(encoding="utf-8"))
    assert log["applied"] == 1 and log["correctable_remaining"] == 0
    assert log["other_differences_unchanged"] is True
    assert [(c["cls"], c["original"], c["corrected"]) for c in log["corrections"]] == [
        ("ocr_homoglyph", "УТАН", "UTAH")
    ]
    report = json.loads((out / "report.json").read_text(encoding="utf-8"))
    assert report["corrections"]["applied"] == 1


def test_cli_corrections_need_the_chandra_text(cli_inputs, tmp_path):
    args, _md, _out = cli_inputs
    with pytest.raises(SystemExit) as exc:
        _cli().main(args + ["--corrected-out", str(tmp_path / "fixed")])
    assert exc.value.code == 2  # argparse usage error


def test_cli_keeps_the_sources_crlf_line_endings(cli_inputs):
    args, md, out = cli_inputs
    html = Path(args[args.index("--html") + 1])
    html.write_text(_PAGE_HTML.replace("the river", "UTAH Beach"), encoding="utf-8")
    md.write_bytes(
        _PAGE_MD.replace("the river", "УТАН Beach").replace("\n", "\r\n").encode()
    )
    fixed = out.parent / "fixed"
    assert (
        _cli().main(args + ["--chandra-md", str(md), "--corrected-out", str(fixed)])
        == 0
    )
    expected = (
        _PAGE_MD.replace("the river", "UTAH Beach").replace("\n", "\r\n").encode()
    )
    assert (
        fixed / "corrected.md"
    ).read_bytes() == expected  # only the correction differs


def test_cli_refuses_mixed_line_endings(cli_inputs, capsys):
    args, md, out = cli_inputs
    md.write_bytes(_PAGE_MD.replace("\n", "\r\n", 1).encode())
    fixed = out.parent / "fixed"
    assert (
        _cli().main(args + ["--chandra-md", str(md), "--corrected-out", str(fixed)])
        == 2
    )
    assert "mixes line endings" in capsys.readouterr().err


def test_cli_writes_nothing_but_the_failure_when_a_correction_does_not_land(
    cli_inputs, monkeypatch, capsys
):
    """A correction that does not change the text (a bug) leaves the difference in place: the
    re-check catches it, exit 3, and no corrected.md exists for anything to pick up."""
    args, md, out = cli_inputs
    html = Path(args[args.index("--html") + 1])
    html.write_text(_PAGE_HTML.replace("the river", "UTAH Beach"), encoding="utf-8")
    md.write_text(_PAGE_MD.replace("the river", "УТАН Beach"), encoding="utf-8")
    cli = _cli()
    monkeypatch.setattr(
        cli.C, "apply_corrections", lambda markdown, _corrections: markdown
    )
    fixed = out.parent / "fixed"
    assert (
        cli.main(args + ["--chandra-md", str(md), "--corrected-out", str(fixed)]) == 3
    )
    assert not (fixed / "corrected.md").exists()
    failed = json.loads((fixed / "corrections.FAILED.json").read_text(encoding="utf-8"))
    assert failed["remaining_are_the_skipped"] is False
    assert "did not land cleanly" in capsys.readouterr().err


# --- numbers: the transcription's look-alike typos inside a number (Cross-Channel Attack notes) --


@pytest.mark.parametrize(
    "html,pdf",
    [("V1", "VI"), ("X11", "XII"), ("I47th", "147th"), ("1O", "10")],
)
def test_a_malformed_number_in_the_transcription_is_its_error(html, pdf):
    """The PDF's number is well formed, the transcription's is no number at all - and the two
    are look-alikes: the transcription is wrong, decided even without vocabulary."""
    assert X.classify(_t(html), _t(pdf)) == "html_error"
    assert X.classify(_t(html), _t(pdf), BOOK) == "html_error"


@pytest.mark.parametrize(
    "html,pdf,cls",
    [
        ("VII", "VI", "review"),  # two valid numerals: a claim, never decided
        ("11th", "111th", "review"),
        ("44", "544", "review"),
        ("V1", "VIII", "review"),  # not look-alikes
        ("101st", "lOlst", "ocr_confusion"),  # the PDF's malformed number is still OCR
    ],
)
def test_numbers_otherwise_stay_claims(html, pdf, cls):
    assert X.classify(_t(html), _t(pdf), BOOK) == cls


def test_well_formed_numbers():
    assert all(
        X.well_formed_number(w) for w in ("6", "147th", "2d", "XII", "IV", "MCMXLIV")
    )
    assert not any(
        X.well_formed_number(w) for w in ("V1", "X11", "IIII", "vi", "I47th", "")
    )


def test_an_ordinal_set_as_a_superscript_is_one_word():
    """Chandra writes "709<sup>th</sup>"; the book prints the ordinal "709th"."""
    tokens = X.chandra_tokens(
        "the 709<sup>th</sup> and 716<sup>th</sup> *Divisions*", 1
    )
    assert [t.text for t in tokens] == ["the", "709th", "and", "716th", "Divisions"]
    assert (
        next(t for t in tokens if t.text == "709th").span is None
    )  # never a correction site
    # a note marker is not an ordinal
    assert [t.text for t in X.chandra_tokens("held.<sup>3</sup> Then", 1)] == [
        "held",
        "3",
        "Then",
    ]


def test_a_failed_correction_run_still_writes_its_report_and_clears_stale_outputs(
    cli_inputs, monkeypatch
):
    """The cross-check is valid even when the corrections fail: report.json is this run's, and
    no earlier run's corrected.md is left beside the failure."""
    args, md, out = cli_inputs
    html = Path(args[args.index("--html") + 1])
    html.write_text(_PAGE_HTML.replace("the river", "UTAH Beach"), encoding="utf-8")
    md.write_text(_PAGE_MD.replace("the river", "УТАН Beach"), encoding="utf-8")
    fixed = out.parent / "fixed"
    run = args + ["--chandra-md", str(md), "--corrected-out", str(fixed)]
    assert _cli().main(run) == 0  # a good run first: corrected.md exists
    cli = _cli()
    monkeypatch.setattr(cli.C, "apply_corrections", lambda markdown, _c: markdown)
    assert cli.main(run) == 3
    assert sorted(p.name for p in fixed.iterdir()) == ["corrections.FAILED.json"]
    report = json.loads((out / "report.json").read_text(encoding="utf-8"))
    assert report["corrections"]["remaining_are_the_skipped"] is False


def test_the_recheck_ignores_a_one_sided_run_grouped_in_another_order():
    """After a correction the alignment may group "Van Fleet Col James A" as "Fleet Col James A
    Van": the same words - not a change. A changed word is."""
    from src.ingestion.pdf_text_corrections import difference_key as key

    assert key("pdf_only", "", "Van Fleet Col James A") == key(
        "pdf_only", "", "Fleet Col James A Van"
    )
    assert key("pdf_only", "", "Van Fleet Col James A") != key(
        "pdf_only", "", "Van Fleet Col James B"
    )


def test_a_designation_the_book_prints_is_never_blamed_on_the_transcription():
    """HyperWar "M1" (the rifle, printed throughout) against a PDF "MI": the OCR's misread."""
    vocab = Counter({"m1": 40, "mi": 0})
    assert X.classify(_t("M1"), _t("MI"), vocab) != "html_error"
    # "I1" against "11": the book prints "i1" as often as "11" - no evidence of a typo
    assert X.classify(_t("I1"), _t("11"), Counter({"i1": 2, "11": 2})) == "review"
    # a few OCR slips of the 2010 text layer ("v1" 18 times) are no evidence against "VI" (200)
    assert (
        X.classify(_t("V1"), _t("VI"), Counter({"v1": 18, "vi": 200})) == "html_error"
    )


def test_lowercase_or_two_malformed_numbers_are_not_decided_by_the_numeral_rule():
    assert (
        X.classify(_t("v1"), _t("vi"), BOOK) != "html_error"
    )  # "vi" is not well formed
    assert X.classify(_t("V1"), _t("X11"), BOOK) == "review"  # neither is


def test_only_a_suffix_that_agrees_with_the_number_joins():
    def texts(md):
        return [t.text for t in X.chandra_tokens(md, 1)]

    assert texts("the 22<sup>d</sup> and 103<sup>d</sup> and 1<sup>st</sup>") == [
        "the", "22d", "and", "103d", "and", "1st",
    ]  # fmt: skip
    assert texts("in 1944<sup>d</sup> it") == ["in", "1944", "d", "it"]  # a note marker
    assert texts("the 11<sup>st</sup> unit") == ["the", "11", "st", "unit"]


def test_list_numbers_keep_page_anchors_and_nested_lists():
    html = (
        '<ol type="I" start="2"><li>Command <a id="page460"></a>is held'
        '<ol type="a"><li>first</li><li>second</li></ol></li><li>Next</li></ol>'
    )
    narrative, _notes = X.html_streams(html)
    assert [t.text for t in narrative] == (
        "II Command is held a first b second III Next".split()
    )
    assert [t.printed for t in narrative if t.text == "held"] == [460]


def test_centred_text_stays_but_credits_navigation_and_figures_go():
    html = (
        "<center><h2>Appendix C</h2></center><center>Fuehrer Headquarters</center>"
        '<center><a href="x.jpg"><img src="x.jpg"></a><br>A PHOTO</center>'
        '<center><a href="index.html">Table of Contents</a> * <a href="b.html">Next Chapter</a>'
        "</center><center><address>Transcribed by Someone</address></center>"
        "<table><tr><td>Date</td><td>Paper</td></tr></table>"
        "<table><tr><td><img src='m.jpg'></td><td>MAP LABEL</td></tr></table>"
    )
    narrative, _notes = X.html_streams(html)
    assert [t.text for t in narrative] == (
        "Appendix C Fuehrer Headquarters Date Paper".split()
    )

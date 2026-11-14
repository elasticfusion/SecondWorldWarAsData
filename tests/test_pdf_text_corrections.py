"""Corrections of the authoritative PDF's Chandra text from the cross-check
(src/ingestion/pdf_text_corrections). Cases are the real Cross-Channel Attack ch. VIII
corrections: "УТАН" -> "UTAH", "counter-attack" -> "counterattack", "Schwarzwaldor" ->
"Schwarzwalder", and the owner's rule that the newer (1993) printing is deferred to.
"""

from __future__ import annotations

from collections import Counter

import pytest

from src.ingestion import pdf_html_crosscheck as X
from src.ingestion import pdf_text_corrections as C
from src.ingestion.pdf_html_crosscheck import Token

BOOK = Counter(
    {"utah": 41, "counterattack": 88, "forty-eight": 3, "forty": 9, "eight": 20,
     "the": 900, "troops": 60, "landed": 30, "on": 400, "at": 300, "dawn": 9, "beach": 50,
     "schwarzwalder": 1, "speidel": 14, "were": 200, "was": 300, "of": 500, "capt": 9,
     "men": 80, "johnson's": 4}
)  # fmt: skip


def _html(text: str):
    return [Token(w, printed=300) for w in text.split()]


def _plan(markdown: str, html: str, vocab=BOOK, second=None):
    tokens = X.chandra_tokens(markdown, 5, {5: 300})
    check = X.crosscheck("narrative", _html(html), tokens, vocab=vocab, second=second)
    return [check], C.plan_corrections([check], markdown, vocab, second)


def _fixes(plan):
    return [(c.cls, c.original, c.corrected) for c in plan.corrections]


def test_tokens_point_at_their_text_in_the_source():
    markdown = "**TROOPS** landed at *Utah* <sup>3</sup> and co-ordinated it."
    tokens = X.chandra_tokens(markdown, 1)
    assert all(markdown[t.span[0] : t.span[1]] == t.text for t in tokens)  # type: ignore[index]


def test_a_word_joined_across_a_break_or_normalised_has_no_position():
    tokens = X.chandra_tokens("the in-\n\nformation &amp; more", 1)
    assert [(t.text, t.span) for t in tokens][:1] == [("the", (0, 3))]
    assert next(t for t in tokens if t.text == "information").span is None
    # an entity shortens the normalised text: offsets would not hold
    assert next(t for t in tokens if t.text == "more").span is None


def test_artefacts_are_corrected_to_the_books_reading():
    markdown = "The troops landed on УТАН Beach at dawn and the counter-attack came."
    html = "The troops landed on UTAH Beach at dawn and the counterattack came"
    _checks, plan = _plan(markdown, html)
    assert _fixes(plan) == [
        ("ocr_homoglyph", "УТАН", "UTAH"),
        ("ocr_spacing", "counter-attack", "counterattack"),
    ]
    assert plan.skipped == Counter() and plan.skipped_differences == []
    assert plan.corrections[1].evidence == {
        "book": {"counterattack": 88, "counter-attack": 0}
    }
    assert C.apply_corrections(markdown, plan.corrections) == (
        "The troops landed on UTAH Beach at dawn and the counterattack came."
    )


def test_a_homoglyph_fix_writes_the_pdfs_own_letters_case_kept():
    """Not the transcription's text: "Утан" stays capitalised, not "UTAH"."""
    _checks, plan = _plan(
        "The troops landed on Утан Beach.", "The troops landed on UTAH Beach"
    )
    assert _fixes(plan) == [("ocr_homoglyph", "Утан", "Utah")]


def test_spacing_is_restored_to_the_books_form_not_the_transcriptions():
    """The book prints "forty-eight"; HyperWar has "forty eight", the OCR "fortyeight"."""
    _checks, plan = _plan(
        "The troops landed fortyeight at dawn.", "The troops landed forty eight at dawn"
    )
    assert _fixes(plan) == [("ocr_spacing", "fortyeight", "forty-eight")]


def test_a_second_ocr_correction_carries_its_evidence():
    vocab = BOOK + Counter({"schwarzwaldor": 1})
    second = Counter({"schwarzwalder": 2})
    _checks, plan = _plan(
        "The troops of Capt. Schwarzwaldor landed.",
        "The troops of Capt Schwarzwalder landed",
        vocab,
        second,
    )
    assert _fixes(plan) == [("ocr_crossread", "Schwarzwaldor", "Schwarzwalder")]
    assert plan.corrections[0].evidence["second_ocr"] == {
        "schwarzwalder": 2,
        "schwarzwaldor": 0,
    }


def test_the_pdfs_case_style_and_apostrophe_are_kept():
    vocab = BOOK + Counter({"schwarzwaldor": 1})
    second = Counter({"schwarzwalder": 2})
    _checks, plan = _plan(
        "THE TROOPS OF SCHWARZWALDOR LANDED.",
        "THE TROOPS OF Schwarzwalder LANDED",
        vocab,
        second,
    )
    assert _fixes(plan) == [("ocr_crossread", "SCHWARZWALDOR", "SCHWARZWALDER")]
    vocab = BOOK + Counter({"jobnson's": 1, "johnson's": 40})
    _checks, plan = _plan(
        "The troops of Jobnson’s men.", "The troops of Johnson's men", vocab
    )
    assert [c.corrected for c in plan.corrections] == ["Johnson’s"]


def test_a_reading_the_newer_edition_does_not_print_is_never_written():
    """HyperWar follows the 1951 printing; the PDF is the 1993 one and is deferred to - above
    all for people's names. A look-alike fix toward a spelling the PDF never prints is skipped.
    """
    vocab = BOOK + Counter(
        {"hoffrnan": 1}
    )  # rn/m look-alike, read once; never "Hoffman"
    checks, plan = _plan(
        "The troops of Hoffrnan landed.", "The troops of Hoffman landed", vocab
    )
    assert [d.cls for d in checks[0].differences] == ["ocr_confusion"]
    assert plan.corrections == [] and plan.skipped == Counter(
        {"not_in_newer_edition": 1}
    )
    assert plan.skipped_differences == [("ocr_confusion", "Hoffman", "Hoffrnan")]


def test_a_multi_word_reading_is_not_rewritten_from_the_transcription():
    """Its hyphens, commas and apostrophes are the PDF's: a two-word confusion is skipped."""
    d = X.Difference(
        "ocr_confusion", "well known", "vvell known", 5, 300, None, "", (4, 15)
    )
    check = X.CrossCheck("narrative", 1, 1, 1, [d])
    plan = C.plan_corrections([check], "The vvell-known road.", BOOK)
    assert plan.corrections == [] and plan.skipped == Counter({"multi_word": 1})


def test_what_the_pdf_has_right_or_needs_a_look_is_left_alone():
    vocab = BOOK + Counter({"spieled": 0})
    checks, plan = _plan(
        "Speidel landed at dawn and the troops was there.",
        "Spieled landed at dawn and the troops were there",
        vocab,
    )
    assert {d.cls for d in checks[0].differences} == {"html_error", "review"}
    assert plan == C.Plan()


def test_markup_inside_a_reading_is_skipped_not_guessed():
    _checks, plan = _plan(
        "The troops landed and the counter-*attack* came.",
        "The troops landed and the counterattack came",
    )
    assert plan.corrections == [] and plan.skipped == Counter({"markup_inside": 1})


def test_a_reading_across_a_paragraph_break_is_skipped():
    d = X.Difference(
        "ocr_spacing", "counterattack", "counter attack", 5, 300, None, "", (0, 16)
    )
    check = X.CrossCheck("narrative", 1, 1, 1, [d])
    plan = C.plan_corrections([check], "counter\n\n attack", BOOK)
    assert plan.skipped == Counter({"paragraph_break_inside": 1})


def test_a_reading_without_a_position_is_skipped():
    checks, plan = _plan(
        "The troops landed forty-\n\neight at dawn.",  # joined across a paragraph break
        "The troops landed forty eight at dawn",
    )
    assert [d.cls for d in checks[0].differences] == ["ocr_spacing"]
    assert plan.corrections == [] and plan.skipped == Counter({"no_position": 1})


def test_overlapping_corrections_keep_the_first():
    first = X.Difference("ocr_spacing", "Army", "A rmy", 1, 1, None, "", (0, 5))
    second = X.Difference("ocr_homoglyph", "rmy", "rmy", 1, 1, None, "", (2, 5))
    check = X.CrossCheck("n", 1, 1, 1, [second, first])
    plan = C.plan_corrections(
        [check], "A rmy marched.", Counter({"army": 30, "a": 900})
    )
    assert [c.span for c in plan.corrections] == [(0, 5)]
    assert plan.skipped == Counter({"overlap": 1})


def test_cut_needs_the_lengths_to_add_up():
    assert C._cut("fortyeight", ["forty", "eight"]) == ["forty", "eight"]
    assert C._cut("fortyeight", ["forty", "eighty"]) == []


def test_applying_refuses_a_correction_that_does_not_fit():
    c = C.Correction(5, 300, "ocr_homoglyph", "УТАН", "UTAH", (0, 4))
    with pytest.raises(ValueError, match="does not fit"):
        C.apply_corrections("OMAHA Beach", [c])


def test_a_spacing_fix_never_drops_a_comma():
    d = X.Difference("ocr_spacing", "1000", "1 000", 5, 300, None, "", (0, 5))
    check = X.CrossCheck("narrative", 1, 1, 1, [d])
    plan = C.plan_corrections([check], "1,000 men", Counter({"1000": 5}))
    assert plan.corrections == [] and plan.skipped == Counter({"comma_inside": 1})


def test_a_spacing_fix_keeps_the_typographic_apostrophe():
    d = X.Difference(
        "ocr_spacing", "Johnson's", "John son's", 5, 300, None, "", (0, 10)
    )
    check = X.CrossCheck("narrative", 1, 1, 1, [d])
    plan = C.plan_corrections([check], "John son’s men", Counter({"johnson's": 40}))
    assert [c.corrected for c in plan.corrections] == ["Johnson’s"]


def test_an_empty_reading_is_never_attested():
    assert C._attested("", BOOK, None) is False

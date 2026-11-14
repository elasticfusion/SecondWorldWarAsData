"""Reference-free OCR sanitising, run on every OCR'd document (with or without a validation URL):
small-caps look-alike letters and superscript ordinals, each only where the document settles it.
"""

from src.ingestion.ocr_sanitise import sanitise_parts, vocabulary

SEP = "\n\n1" + "-" * 48 + "\n\n"


def _run(*texts, first=10):
    parts = [(first, t) for t in texts]
    return sanitise_parts(parts)


def test_a_lookalike_word_the_book_prints_is_written_in_latin_letters():
    (out,), log = _run(
        "The landings on УТАН and ОМАНА beaches." + SEP + "UTAH and OMAHA were taken."
    )
    assert out.startswith("The landings on UTAH and OMAHA beaches.")
    assert [(c.original, c.corrected, c.page) for c in log.changes] == [
        ("УТАН", "UTAH", 10),
        ("ОМАНА", "OMAHA", 10),
    ]
    assert log.changes[0].evidence == {"book": 1}


def test_case_is_kept_letter_by_letter():
    (out,), _ = _run("Утан beach. Utah again.")
    assert out == "Utah beach. Utah again."


def test_a_lookalike_word_the_book_never_prints_is_left_for_review():
    (out,), log = _run("Sector ХОРА held.")  # "XOPA" appears nowhere else
    assert out == "Sector ХОРА held."
    assert [(u.text, u.reason) for u in log.unresolved] == [("ХОРА", "not_attested")]


def test_a_letter_with_no_latin_lookalike_is_left_for_review():
    (out,), log = _run("УГАН and UTAH.")  # "Г" has no Latin look-alike
    assert out == "УГАН and UTAH."
    assert log.unresolved[0].reason == "no_latin_lookalike"


def test_a_cyrillic_page_is_not_touched():
    russian = " ".join(["Наступление началось утром и продолжалось весь день"] * 3)
    (out,), log = _run(russian + " СОМ" + SEP + "COM text.")
    assert out.startswith(russian + " СОМ")
    assert log.skipped_foreign_pages == [10]


def test_a_small_caps_heading_alone_on_a_page_is_still_fixed():
    (out,), _ = _run("## УТАН" + SEP + "UTAH beach.")
    assert out.startswith("## UTAH")


def test_superscript_ordinals_join_only_when_the_suffix_agrees():
    (out,), log = _run(
        "the 709<sup>th</sup> and 352<sup>d</sup> and 22<sup>nd</sup> Divisions; 1944<sup>d</sup> "
        "and 3<sup>th</sup>."
    )
    assert (
        out
        == "the 709th and 352d and 22nd Divisions; 1944<sup>d</sup> and 3<sup>th</sup>."
    )
    assert [c.corrected for c in log.changes] == ["709th", "352d", "22nd"]


def test_a_note_marker_is_never_an_ordinal():
    (out,), log = _run("ended there.<sup>68</sup> Next")
    assert out == "ended there.<sup>68</sup> Next" and not log.changes


def test_pages_are_counted_from_the_chunk_start():
    (out,), log = _run("first page" + SEP + "on УТАН. UTAH.", first=288)
    assert log.changes[0].page == 289


def test_unknown_first_page_gives_no_page_number():
    (_,), log = sanitise_parts([(None, "УТАН and UTAH")])
    assert log.changes[0].page is None


def test_the_vocabulary_spans_all_parts():
    outs, log = sanitise_parts([(1, "on УТАН"), (51, "at UTAH")])
    assert outs[0] == "on UTAH"
    assert (
        vocabulary(["on УТАН"])["utah"] == 0
    )  # a look-alike word never attests itself


def test_summary_counts():
    _, log = _run("УТАН UTAH 709<sup>th</sup> ХОРА")
    assert log.summary() == {
        "homoglyph": 1,
        "ordinal": 1,
        "unresolved": 1,
        "foreign_pages_skipped": 0,
    }
    assert log.to_dict()["summary"]["homoglyph"] == 1


def test_a_short_word_is_not_converted():
    (out,), log = _run("Нам and Ham and ИТ and it.")
    assert out == "Нам and Ham and ИТ and it." and not log.changes


def test_a_lookalike_word_among_real_russian_letters_is_not_small_caps():
    (out,), log = _run("Он сказал СОМ гоп. Also COM here.")
    assert "СОМ" in out and not [c for c in log.changes if c.original == "СОМ"]


def test_a_year_with_a_lettered_note_is_never_an_ordinal():
    (out,), log = _run(
        "In 1942<sup>d</sup> and 1943<sup>d</sup>, the 42<sup>d</sup> Division."
    )
    assert out == "In 1942<sup>d</sup> and 1943<sup>d</sup>, the 42d Division."
    assert [(u.text, u.reason) for u in log.unresolved] == [
        ("1942<sup>d</sup>", "year"),
        ("1943<sup>d</sup>", "year"),
    ]

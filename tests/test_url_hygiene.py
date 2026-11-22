def test_a_control_character_makes_a_url_unclean():
    from src.utils.url_hygiene import clean_url, has_url_junk

    assert clean_url("https://example.org/a\nb") is None
    assert clean_url("https://example.org/a\x1b[0m") is None
    assert has_url_junk("https://example.org/a b") and not has_url_junk(
        "https://example.org/a%20b"
    )


def test_has_url_junk_flags_tabs_unicode_spaces_and_line_separators():
    from src.utils.url_hygiene import clean_url, has_url_junk

    for bad in ("a\tb", "a\u00a0b", "a\u2028b", "a\x85b", "a\nb", "a b"):
        assert has_url_junk("https://e.org/" + bad), bad
    for bad in ("a\u2028b", "a\x85b", "a\u202eb"):
        assert clean_url("https://e.org/" + bad) is None, bad


def test_invisible_marks_make_a_url_unclean_but_joiners_are_not_junk_text():
    from src.utils.url_hygiene import INVISIBLE, clean_url

    for bad in ("\u200b", "\u200e", "\ufeff", "\u2060", "\u00ad", "\U000e0041"):
        assert clean_url("https://e.org/a" + bad + "b") is None, repr(bad)
    assert not INVISIBLE.search("\u200c\u200d\ufe0f")


def test_invisible_covers_every_unicode_format_character_but_the_joiners():
    """Pins the INVISIBLE list against the Unicode database, so a new Unicode version that adds a
    format character shows up here."""
    import sys
    import unicodedata

    from src.utils.url_hygiene import INVISIBLE

    keep = {"\u200c", "\u200d"}
    missed = [
        hex(c)
        for c in range(sys.maxunicode + 1)
        if unicodedata.category(chr(c)) == "Cf"
        and chr(c) not in keep
        and not INVISIBLE.search(chr(c))
    ]
    assert missed == []
    wrongly = [
        hex(c)
        for c in range(sys.maxunicode + 1)
        if INVISIBLE.search(chr(c))
        and (unicodedata.category(chr(c)) != "Cf" or chr(c) in keep)
    ]
    assert wrongly == []


def test_scrub_spaces_line_controls_and_deletes_invisible_marks():
    from src.utils.url_hygiene import scrub

    assert scrub("a\nb\u2028c\x1bd") == "a b c d"
    assert scrub("a\u200bb\u00adc") == "abc"
    assert scrub(None) == "" and scrub(5) == "5"
    persian = "می\u200cخواهم"
    assert scrub(persian) == persian and scrub("🏳\ufe0f") == "🏳\ufe0f"

"""Tests for Phase 0 language detection + Grok translation (src/ingestion/translation.py).

A fake Grok client records calls and returns canned responses, so detection /
translation orchestration is tested with no network.
"""

from __future__ import annotations

from src.ingestion import translation as t


class FakeGrok:
    """Records chat_completion calls; canned detect + fake translation."""

    def __init__(self, detect: str = "German"):
        self.detect = detect
        self.calls: list = []

    def chat_completion(
        self,
        prompt,
        system_prompt=None,
        temperature=0.1,
        use_cache=True,
        cache_type="default",
    ):
        self.calls.append({"cache_type": cache_type, "prompt": prompt})
        if "Identify the PRIMARY language" in prompt:
            return self.detect
        # Fake translation: prefix so we can assert it ran per chunk.
        return "[EN]\n\n" + prompt.split("Document:\n", 1)[-1].strip()[:40]


def test_is_english():
    assert t.is_english("English")
    assert t.is_english(" en ")
    assert not t.is_english("German")


def test_detect_language_returns_capitalized_word():
    assert t.detect_language("Deutscher Text", FakeGrok("german")) == "German"


def test_detect_language_empty_defaults_english():
    g = FakeGrok("German")
    assert t.detect_language("   ", g) == "English"
    assert g.calls == []  # no Grok call for empty doc


def test_detect_language_unusable_response_defaults_english():
    assert t.detect_language("text", FakeGrok("!!!")) == "English"


def test_english_document_is_passthrough_no_translation():
    g = FakeGrok("English")
    r = t.normalize_to_english("# Title\n\nBody.", g, model_name="grok-4")
    assert r.source_language == "English"
    assert r.translated is False
    assert r.english_markdown == "# Title\n\nBody."
    assert r.original_markdown is None
    assert r.translator is None
    # Only the detect call — no translation call.
    assert len(g.calls) == 1


def test_non_english_document_is_translated_and_original_kept():
    g = FakeGrok("German")
    r = t.normalize_to_english("# Titel\n\nDeutscher Text.", g, model_name="grok-4")
    assert r.source_language == "German"
    assert r.translated is True
    assert r.original_markdown == "# Titel\n\nDeutscher Text."
    assert r.translator == "grok:grok-4"
    assert "[EN]" in r.english_markdown
    assert len(g.calls) >= 2  # detect + >=1 translation chunk


def test_translator_tag_without_model_name():
    r = t.normalize_to_english("Texte français", FakeGrok("French"))
    assert r.translator == "grok"


def test_split_markdown_respects_block_boundaries():
    md = "\n\n".join(f"paragraph number {i} content" for i in range(10))
    chunks = t.split_markdown_chunks(md, max_chars=120)
    assert len(chunks) > 1
    # No chunk splits a block: every original block appears intact in some chunk.
    joined = "\n\n".join(chunks)
    for i in range(10):
        assert f"paragraph number {i} content" in joined


def test_split_oversized_block_kept_whole():
    big = "X" * 5000
    chunks = t.split_markdown_chunks(big, max_chars=1000)
    assert len(chunks) == 1
    assert chunks[0] == big


def test_split_empty_returns_empty():
    assert t.split_markdown_chunks("   ") == []


def test_translate_markdown_translates_each_chunk():
    g = FakeGrok("German")
    md = "\n\n".join(f"Block {i}" for i in range(3))
    out = t.translate_markdown(md, "German", g, use_cache=False)
    # One translation call per chunk (small doc → 1 chunk here).
    trans_calls = [c for c in g.calls if "Identify" not in c["prompt"]]
    assert trans_calls
    assert "[EN]" in out
    assert all(c["cache_type"] == "translation" for c in g.calls)


# --- Per-page normalization -------------------------------------------------


def _sep(i: int) -> str:
    return "\n\n" + str(i) + "-" * 48 + "\n\n"


class PageAwareGrok:
    """Fake Grok that detects language by a marker string in each page."""

    def chat_completion(
        self,
        prompt,
        system_prompt=None,
        temperature=0.1,
        use_cache=True,
        cache_type="default",
    ):
        if "Identify the PRIMARY language" in prompt:
            # The detect sample is the page text; classify by content.
            return "German" if "DEUTSCH" in prompt else "English"
        return "[EN]\n\n" + prompt.split("Document:\n", 1)[-1].strip()[:30]


def test_marker_is_inert_html_comment():
    m = t.translation_marker("German", "grok:grok-4")
    assert m == "<!-- translated from German by grok:grok-4 -->"
    assert m.startswith("<!--") and m.endswith("-->")


def test_per_page_mixed_document_translates_only_foreign_pages():
    # page1 English (cover), page2 German (DEUTSCH marker), page3 English
    md = "English cover" + _sep(1) + "DEUTSCH transcript" + _sep(2) + "English summary"
    r = t.normalize_pages_to_english(md, 1, PageAwareGrok(), model_name="grok-4")
    assert r.translated is True
    assert [p.page for p in r.pages] == [1, 2, 3]
    assert [p.translated for p in r.pages] == [False, True, False]
    assert r.source_languages == ["English", "German"]
    # Only the German page's paragraphs carry the inert marker - each of them (the fake
    # translation is two paragraphs)
    assert "<!-- translated from German by grok:grok-4 -->" in r.english_markdown
    assert r.english_markdown.count("<!-- translated from") == 2
    # English pages are passed through unchanged.
    assert "English cover" in r.english_markdown
    assert "English summary" in r.english_markdown
    # Original kept because a page was translated.
    assert r.original_markdown == md


def test_per_page_all_english_is_passthrough():
    md = "English one" + _sep(1) + "English two"
    r = t.normalize_pages_to_english(md, 1, PageAwareGrok(), model_name="grok-4")
    assert r.translated is False
    assert r.original_markdown is None
    assert r.translator is None
    assert "<!-- translated" not in r.english_markdown
    assert r.source_languages == ["English"]


def test_per_page_physical_page_offset():
    md = "DEUTSCH a" + _sep(1) + "DEUTSCH b"
    r = t.normalize_pages_to_english(md, 155, PageAwareGrok(), model_name="grok-4")
    assert [p.page for p in r.pages] == [155, 156]
    assert all(p.translated for p in r.pages)


ENGLISH = (
    "The 4th Division landed on Utah Beach at 0630 and the leading battalions of the 8th "
    "Infantry were ashore within minutes. They found the beach obstacles fewer than expected, "
    "and the German defenders were stunned by the naval bombardment that had preceded them."
)
GERMAN = (
    "Die Division wurde am 6. Juni an der Küste eingesetzt und hatte den Auftrag, die "
    "Landungen der Alliierten zu verhindern. Sie war nicht vollständig ausgerüstet, und die "
    "meisten Soldaten waren noch jung. Nach den Kämpfen zog sich die Division zurück."
)


def test_clearly_english_is_free_and_anything_else_asks_grok():
    assert t.clearly_english(ENGLISH)
    assert not t.clearly_english(GERMAN)
    assert not t.clearly_english("Дивизия была введена в бой шестого июня")
    assert not t.clearly_english("Deutscher Text.")  # short, no English function word
    assert not t.clearly_english(
        "Letter of Instruction, V Corps"
    )  # one function word: unsure
    assert t.clearly_english(
        "Letter of Instruction to the V Corps"
    )  # two, English only
    # a quoted German order inside an English paragraph is not "clearly English"
    assert not t.clearly_english(ENGLISH + " " + GERMAN)
    # markup is not language: an English table
    table = "<table><tr><td>CO</td><td>Commanding Officer of the battalion</td></tr></table>"
    assert t.clearly_english(table)
    g = FakeGrok("German")
    assert t.detect_language(ENGLISH, g) == "English" and g.calls == []


def test_a_mixed_language_document_translates_only_its_foreign_sections():
    """A book may mix languages: an English history quoting a German document. Detection per
    section, not one language for the whole document from its first page."""

    class Grok(FakeGrok):
        def chat_completion(self, prompt, **kw):
            self.calls.append({"prompt": prompt})
            if "Identify the PRIMARY language" in prompt:
                return "German"
            return "The division was committed on the coast."

    english_part = "\n\n".join([ENGLISH] * 60)  # past one translation chunk
    md = english_part + "\n\n" + GERMAN
    g = Grok()
    r = t.normalize_to_english(md, g, model_name="grok-4")
    assert r.translated and r.source_language == "German"
    assert r.english_markdown.startswith(ENGLISH)  # the English part untouched
    assert "<!-- translated from German by grok:grok-4 -->" in r.english_markdown
    assert GERMAN not in r.english_markdown and r.original_markdown == md
    # the English sections cost no Grok call: one detect + one translate for the German one
    assert len(g.calls) == 2


def test_languages_outside_the_big_five_are_never_judged_english():
    """Short Polish / Czech / Norwegian text shares "to", "for", "at" with English: judged
    English, it would pass untranslated, silently (review 2026-11-16)."""
    for text in (
        "To jest rozkaz dla dywizji, który przyszedł rano.",
        "To byl rozkaz pro divizi, který přišel ráno.",
        "Det var en kald morgen da bataljonen gikk i land på stranden.",
        "O batalhão atravessou o rio ao amanhecer e tomou a ponte sem perdas.",
    ):
        assert not t.clearly_english(text), text
    # an English page with a German order embedded: Grok decides
    mixed = (
        "Task Force Hogan held the crossroads until dusk. Den Befehl hatte der Kommandeur "
        "am Morgen gegeben, und die Lage war nicht klar. The 3d Armored Division then moved "
        "north toward the river and the bridge."
    )
    assert not t.clearly_english(mixed)
    assert t.clearly_english(
        "The battalion crossed the river at dawn and took the bridge."
    )


def test_grok_is_sent_a_short_sample_only():
    g = FakeGrok("German")
    t.detect_language(GERMAN * 40, g)
    (call,) = g.calls
    assert len(call["prompt"]) < len(GERMAN * 40) and GERMAN[:200] in call["prompt"]


ORDER = (
    "Den Befehl hatte der Kommandeur am Morgen gegeben, und die Lage war nicht klar."
)


class PassageGrok(FakeGrok):
    """Names a mostly-English sample English (as Grok does), the German passage German;
    translates to a fixed English sentence."""

    def chat_completion(self, prompt, **kw):
        self.calls.append({"prompt": prompt})
        if "Identify the PRIMARY language" in prompt:
            return "English" if "4th Division landed" in prompt else "German"
        return "The commander had given the order in the morning, and the situation was unclear."


def test_a_foreign_quotation_inside_an_english_page_is_translated_in_place():
    page = f"{ENGLISH} {ORDER} The attack went in at dawn and the bridge was taken by noon."
    g = PassageGrok()
    text, langs = t.translate_embedded(page, g, tag="grok:grok-4")
    assert langs == ["German"]
    assert ORDER not in text
    assert text.startswith(ENGLISH)  # the English around it untouched
    assert (
        "<!-- translated from German by grok:grok-4 --> The commander had given the order"
        in text
    )
    assert text.endswith("the bridge was taken by noon.")
    assert len(g.calls) == 2  # one detect + one translate, for the passage only


def test_names_titles_citations_and_place_names_are_never_translated():
    g = PassageGrok()
    for text in (
        "The *352d Infanterie-Division* and *Kampfgruppe Meyer* held the line near the river.",
        "The 4th Division reached Ste. Marie-du-Mont, Chef-du-Pont and Pont l'Abbé by noon.",
        "Geyr von Schweppenburg, General der Panzertruppen Leo Freiherr, commanded the group.",
        "See *Grundlegender Befehl des Oberbefehlshabers West Nr. 3, Aufgaben der Ost-Divisionen*.",
        "<sup>44</sup> Grundlegender Befehl des Oberbefehlshabers West Nr. 3, und die Aufgaben der "
        "Ost-Divisionen, 10 May 42.",
        ENGLISH,
    ):
        assert t.translate_embedded(text, g) == (text, []), text
    assert g.calls == []  # no Grok call for any of them


def test_foreign_sentences_in_several_languages_are_found():
    for text in (
        ORDER,
        "L'ordre a été donné le matin et la division est partie pour la côte avec ses chars.",
        "Rozkaz został wydany rano i dywizja nie była gotowa do ataku na pozycje wroga.",
        "Приказ был отдан утром, и дивизия двинулась к побережью.",
    ):
        assert t.foreign_sentence(text), text
    assert not t.foreign_sentence(
        "The division reached Ste. Marie-du-Mont by noon on 6 June."
    )


def test_pages_and_sections_judged_english_get_the_embedded_pass():
    g = PassageGrok()
    page = f"{ENGLISH} {ORDER}"
    res = t.normalize_pages_to_english(page, 7, g)
    (outcome,) = res.pages
    assert outcome.translated and outcome.source_language == "English (with German)"
    assert ORDER not in res.english_markdown and res.original_markdown == page
    doc = t.normalize_to_english(f"{ENGLISH}\n\n{ENGLISH} {ORDER}", PassageGrok())
    assert doc.translated and doc.source_language == "German"
    assert ORDER not in doc.english_markdown and ENGLISH in doc.english_markdown


def test_headings_quotes_and_lists_keep_their_markdown_prefix():
    g = PassageGrok()
    block = f"# {ORDER}\n> {ORDER}\n- {ORDER}"
    text, langs = t.translate_embedded(block, g)
    lines = text.split("\n")
    assert [ln[:2] for ln in lines] == ["# ", "> ", "- "]
    assert all("<!-- translated from German" in ln for ln in lines) and langs == [
        "German"
    ]


def test_a_translation_stays_one_paragraph_and_indentation_is_kept():
    class TwoParagraphs(PassageGrok):
        def chat_completion(self, prompt, **kw):
            out = super().chat_completion(prompt, **kw)
            return (
                out if out in ("English", "German") else "First part.\n\nSecond part."
            )

    text, _ = t.translate_embedded(f"  {ORDER}", TwoParagraphs())
    assert text.startswith("  <!-- translated")
    assert "\n\n" not in text and "First part.\nSecond part." in text


def test_a_passage_grok_calls_english_is_left_and_a_note_is_never_touched():
    class SaysEnglish(PassageGrok):
        def chat_completion(self, prompt, **kw):
            self.calls.append({"prompt": prompt})
            return "English"

    g = SaysEnglish()
    assert t.translate_embedded(ORDER, g) == (ORDER, [])
    note = f"<sup>12</sup> {ORDER}"
    assert t.translate_embedded(note, PassageGrok()) == (note, [])


def test_two_passages_in_one_block_are_each_translated():
    block = (
        f"{ORDER} The attack went in at dawn and the bridge was taken by noon. {ORDER}"
    )
    text, _ = t.translate_embedded(block, PassageGrok())
    assert text.count("<!-- translated from German") == 2
    assert "The attack went in at dawn and the bridge was taken by noon." in text


def test_the_parser_lifts_markers_into_translated_from(tmp_path):
    from src.parser import (
        _mark_translated_paragraphs,
        split_into_blocks,
        _create_paragraphs,
    )

    german_page = t.mark_translated(
        "The commander gave the order.\n\nThe division moved.", "German", "grok"
    )
    raw = "The attack went in. <!-- translated from French by grok --> It held.\n\n"
    raw += german_page + "\n\nAn English page follows."
    content, lines = t.lift_translation_markers(raw)
    assert "<!--" not in content
    blocks = split_into_blocks(content)
    paras = _create_paragraphs(blocks, 1, "full", tmp_path / "c.md", content, {})
    _mark_translated_paragraphs(paras, [], lines, tmp_path / "c.md", content)
    assert [p.text for p in paras] == [
        "The attack went in. It held.",
        "The commander gave the order.",
        "The division moved.",
        "An English page follows.",
    ]
    # each paragraph its own provenance - the English page after a German one is not marked
    assert [p.translated_from for p in paras] == ["French", "German", "German", None]


def test_marked_text_parses_exactly_as_unmarked_text(tmp_path):
    """Notes headings / definitions, asterisk headings, separators, tables: a marked page lifts
    back to the unmarked text, so every detector reads what it read before markers existed.
    """
    from src.notes import separate_notes

    page = (
        "## *The Landing*\n\nThe division moved at dawn.\n\n* * *\n\n"
        "It reached the river.<sup>[1](http://h/p.html#fn1)</sup>\n\n"
        "| a | b |\n|---|---|\n| 1 | 2 |\n\n### Footnotes\n\n"
        '\\[<a href="http://h/p.html#cn1" id="fn1">1</a>\\] Report of the division.\n'
    )
    plain_notes = separate_notes(page)[1]
    assert plain_notes  # the fixture really has a notes section
    lifted, lines = t.lift_translation_markers(
        t.mark_translated(page, "German", "grok")
    )
    assert lifted == page
    assert len(separate_notes(lifted)[1]) == len(plain_notes) and lines


def test_a_paragraph_without_a_marker_is_byte_identical():
    for text in ("Alpha  beta\n  indented line", "| a  | b |\n|  1 | 2 |", ENGLISH):
        assert t.strip_translation_markers(text) == (text, [])
    assert t.strip_translation_markers(
        "A <!-- translated from German by grok --> B"
    ) == (
        "A B",
        ["German"],
    )


def test_every_translated_paragraph_is_marked_at_the_end_of_its_first_line():
    m = "<!-- translated from German by g -->"
    marked = t.mark_translated(
        "# Heading\n\nFirst para\nsecond line.\n\n- item\n- two\n\n* * *", "German", "g"
    )
    assert marked == (
        f"# Heading {m}\n\nFirst para {m}\nsecond line.\n\n- item {m}\n- two\n\n* * *"
    )


def _marked(tmp_path, raw):
    from src.parser import (
        _create_paragraphs,
        _mark_translated_paragraphs,
        split_into_blocks,
    )

    content, lines = t.lift_translation_markers(raw)
    paras = _create_paragraphs(
        split_into_blocks(content), 1, "f", tmp_path / "c.md", content, {}
    )
    _mark_translated_paragraphs(paras, [], lines, tmp_path / "c.md", content)
    return [p.translated_from for p in paras]


M = "<!-- translated from German by g -->"


def test_a_repeated_line_elsewhere_is_never_marked_translated(tmp_path):
    """Matched by occurrence, not by text: an identical English line BEFORE or AFTER the
    translated one stays English."""
    assert _marked(tmp_path, f"Ibid.\n\nThe English one.\n\nIbid. {M}\n\nMore.") == [
        None, None, "German", None
    ]  # fmt: skip
    assert _marked(tmp_path, f"Ibid. {M}\n\nEnglish one.\n\nIbid.") == [
        "German",
        None,
        None,
    ]
    assert _marked(tmp_path, f"A para. {M}\n\nA para.\n\nA para. {M}") == [
        "German", None, "German"
    ]  # fmt: skip
    assert _marked(tmp_path, f"Ibid.\n\n* * *\n\nEnglish.\n\nIbid. {M}") == [
        None, None, "German"
    ]  # fmt: skip
    # a page anchor in the line is no obstacle; several marked lines of one paragraph: once
    assert _marked(tmp_path, f'Der text <a id="page5"></a>geht weiter. {M}') == [
        "German"
    ]
    assert _marked(tmp_path, f"Line one. {M}\nline two. {M}") == ["German"]


def _parse_marked(tmp_path, monkeypatch, raw, *, repair=None):
    """(paragraph languages, {note number: language}, unplaced warnings) for ``raw`` parsed
    end to end by ``parse_content_file``."""
    import src.parser as P
    import src.utils.observe as O
    from src.models import Metadata

    warned = []
    monkeypatch.setattr(O, "observe_warn", lambda *a, **k: warned.append((a, k)))
    if repair:
        monkeypatch.setattr(P, "_apply_structure_repair", lambda c: (repair(c), {}))
    path = tmp_path / "chapter1-content.md"
    path.write_text(raw, encoding="utf-8")
    doc = P.parse_content_file(
        path, "s", 1, Metadata(book="B"), apply_structure_repair=bool(repair)
    )
    notes = {n.number: n.translated_from for n in doc.footnotes}
    lost = [k for a, k in warned if a[1:2] == ("translated_line_unplaced",)]
    return [p.translated_from for p in doc.paragraphs], notes, lost


_NAV_LINE = "[Table of Contents](toc.html) [Previous Chapter](p.html)"


def _note(n, text):
    return f'\\[<a href="http://h/p.html#cn{n}" id="fn{n}">{n}</a>\\] {text}'


def test_lines_removed_by_notes_and_nav_do_not_shift_the_placement(
    tmp_path, monkeypatch
):
    """The notes section and nav lines leave the body between the file and the paragraphs: an
    identical line among them must not push the translated body line onto the wrong one.
    """
    raw = "\n\n".join(
        [
            f"{_NAV_LINE} {M}",
            "Ibid.",
            "### Footnotes",
            _note(1, "Report of the division."),
            "Ibid.",
            "----------",
            f"Ibid. {M}",
        ]
    )
    langs, _notes, lost = _parse_marked(tmp_path, monkeypatch, raw)
    assert langs == [None, None, "German"] and not lost  # Ibid., the rule, Ibid.


def test_a_translated_note_carries_its_language(tmp_path, monkeypatch):
    raw = "\n\n".join(
        [
            "The division moved.",
            "### Footnotes",
            _note(1, "Report of the division."),
            _note(2, f"Bericht der Division, translated. {M}"),
            f"Second paragraph of the note. {M}",
            _note(3, "English note."),
        ]
    )
    langs, notes, lost = _parse_marked(tmp_path, monkeypatch, raw)
    assert langs == [None] and not lost
    assert notes == {1: None, 2: "German", 3: None}


def test_structure_repair_shifting_lines_does_not_misplace_them(tmp_path, monkeypatch):
    """Repair can add blank lines; the marked line numbers index the file BEFORE it."""
    raw = f"English one.\n\nIbid.\n\nIbid. {M}\n\nMore."
    langs, _notes, lost = _parse_marked(
        tmp_path, monkeypatch, raw, repair=lambda c: c.replace("\n\n", "\n\n\n\n")
    )
    assert langs == [None, None, "German", None] and not lost


def test_a_marked_line_with_nowhere_to_go_is_reported(tmp_path, monkeypatch):
    """An out-of-range or textless marked line is LOST, never skipped silently."""
    import src.utils.observe as O
    from src.parser import _mark_translated_paragraphs

    warned = []
    monkeypatch.setattr(O, "observe_warn", lambda *a, **k: warned.append(k))
    _mark_translated_paragraphs(
        [], [], [(99, "German"), (0, "German")], tmp_path / "c.md", ""
    )
    assert warned and warned[0]["count"] == 2


def test_a_short_note_continuation_goes_to_its_own_note(tmp_path, monkeypatch):
    """ "Ibid." in a note continuation belongs to the note defined above it, not the first note
    whose text happens to contain it."""
    raw = "\n\n".join(
        [
            "The division moved.",
            "### Footnotes",
            _note(1, "See Ibid. for the report of the 5th Army."),
            _note(2, "Plain English note."),
            f"Ibid. {M}",
        ]
    )
    _langs, notes, lost = _parse_marked(tmp_path, monkeypatch, raw)
    assert notes == {1: None, 2: "German"} and not lost


def test_a_standalone_marker_line_marks_the_next_line():
    """The older per-page form put the marker on its own line before the translation."""
    content, lines = t.lift_translation_markers(f"{M}\n\nDer Angriff.\n\nEnglish.")
    assert content == "\n\nDer Angriff.\n\nEnglish." and lines == [(2, "German")]
    assert t.lift_translation_markers(f"Text.\n\n{M}")[1] == [(2, "German")]

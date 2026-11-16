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
    # Only the German page carries the inert marker.
    assert "<!-- translated from German by grok:grok-4 -->" in r.english_markdown
    assert r.english_markdown.count("<!-- translated from") == 1
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

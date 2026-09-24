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

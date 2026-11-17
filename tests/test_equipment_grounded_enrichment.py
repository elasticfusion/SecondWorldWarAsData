"""Phase 2 equipment TEXT enrichment: WWII-qualified encyclopedia text, descriptions grounded in it.

Owner rules (2026-11-07): "world war" qualifies the TEXT (the Wikipedia/Grokipedia JSON), not
images; accuracy and completeness outrank cost. Before: the Grok description was written from the
equipment NAME alone (no source), and any Wikipedia article was used unchecked.
"""

import pytest

from src.extraction import equipment as EQ
from src.extraction import equipment_identity as EI


@pytest.mark.parametrize(
    "text,ok",
    [
        (
            "The M1 Garand was the standard U.S. service rifle during World War II.",
            True,
        ),
        ("Used throughout the Second World War.", True),
        # a lone year is not enough (a post-war design's article can mention "1944" once)
        ("Introduced in 1943 and used by US forces.", False),
        ("In service with the Royal Navy 1939-1945.", True),
        ("Served 1939–1945 in the Atlantic.", True),
        ("A WWII heavy cruiser.", True),
        ("The Mariana Trench is the deepest oceanic trench on Earth.", False),
        ("A benchrest rifle is used in precision shooting competitions.", False),
        ("", False),
        (None, False),
    ],
)
def test_mentions_wwii(text, ok):
    assert EI.mentions_wwii(text) is ok


def _wiki(monkeypatch, extract):
    monkeypatch.setattr(
        "src.enrichment.equipment_wikipedia.search_equipment_wikipedia",
        lambda name: {
            "extract": extract,
            "wikipedia_url": "https://en.wikipedia.org/wiki/X",
            "image": "",
            "license": "unknown",
        },
    )


def test_wikipedia_article_without_wwii_is_not_used(monkeypatch):
    _wiki(monkeypatch, "The Mariana Trench is the deepest oceanic trench on Earth.")
    rec = {}
    assert EQ._apply_wikipedia_text_extract(rec, "trench") is None
    assert rec["wikipedia_wwii_relevant"] is False
    assert "wikipedia_extract" not in rec and "external_data" not in rec
    assert rec["wikipedia_checked_at"]  # a genuine miss is stamped (not re-fetched)


def test_wwii_article_is_stored_and_returned_for_grounding(monkeypatch):
    text = "The M1 Garand was the standard U.S. service rifle during World War II."
    _wiki(monkeypatch, text)
    rec = {}
    assert EQ._apply_wikipedia_text_extract(rec, "M1 Garand") == (
        text,
        "https://en.wikipedia.org/wiki/X",
    )
    assert rec["wikipedia_wwii_relevant"] is True
    assert rec["external_data"]["wikipedia_url"] == "https://en.wikipedia.org/wiki/X"


def test_grokipedia_text_needs_wwii(monkeypatch):
    monkeypatch.setattr(
        "src.enrichment.grokipedia.resolve_grokipedia_url",
        lambda name: "https://grokipedia.com/page/X",
    )
    monkeypatch.setattr(
        "src.enrichment.grokipedia.fetch_grokipedia_article_text",
        lambda url: "A modern sports rifle.",
    )
    rec = {}
    assert EQ._apply_grokipedia_text(rec, "rifle") is None
    assert "external_data" not in rec  # the URL of an unqualified article is not stored


class _Grok:
    def __init__(self):
        self.prompts = []

    def extract_json(self, prompt, **_k):
        self.prompts.append(prompt)
        return {
            "description": "The M1 Garand was the US service rifle in WWII.",
            "specifications": {"weight_kg": 4.3},
        }


def test_no_qualifying_text_means_no_grok_description(monkeypatch):
    monkeypatch.setattr(EQ, "_apply_grokipedia_text", lambda d, n: None)
    monkeypatch.setattr(EQ, "_apply_wikipedia_text_extract", lambda d, n: None)
    grok = _Grok()
    rec = {"common_name": "M1 Garand", "category": "infantry_weapons"}
    EQ._enrich_from_encyclopedias(rec, "M1 Garand", grok)
    assert grok.prompts == []  # no source -> no generated facts
    assert "description" not in rec


def test_description_is_grounded_in_and_cites_the_source(monkeypatch):
    src_text = "The M1 Garand was the standard U.S. service rifle during World War II."
    monkeypatch.setattr(
        EQ,
        "_apply_grokipedia_text",
        lambda d, n: (src_text, "https://grokipedia.com/page/M1_Garand"),
    )
    monkeypatch.setattr(EQ, "_apply_wikipedia_text_extract", lambda d, n: None)
    grok = _Grok()
    rec = {"common_name": "M1 Garand", "category": "infantry_weapons"}
    EQ._enrich_from_encyclopedias(rec, "M1 Garand", grok)
    assert src_text in grok.prompts[0]  # the prompt carries the source text
    assert "https://grokipedia.com/page/M1_Garand" in grok.prompts[0]
    assert rec["description"].startswith("The M1 Garand")
    assert rec["description_source_url"] == "https://grokipedia.com/page/M1_Garand"


def test_narrative_description_is_not_overwritten_or_misattributed(monkeypatch):
    monkeypatch.setattr(
        EQ,
        "_apply_grokipedia_text",
        lambda d, n: ("WWII rifle 1943.", "https://grokipedia.com/page/G"),
    )
    monkeypatch.setattr(EQ, "_apply_wikipedia_text_extract", lambda d, n: None)
    rec = {
        "common_name": "M1 Garand",
        "category": "x",
        "description": "From the narrative.",
    }
    EQ._enrich_from_encyclopedias(rec, "M1 Garand", _Grok())
    assert rec["description"] == "From the narrative."
    assert "description_source_url" not in rec


def test_batch_collect_propagates_from_enrichment():
    from src.grok_client import BatchModeCollecting

    class _Collecting:
        def extract_json(self, *a, **k):
            raise BatchModeCollecting("k")

    with pytest.raises(BatchModeCollecting):
        EQ._enrich_equipment_data(
            "M1", "M1", "x", _Collecting(), source_text="WWII 1943"
        )


def test_generic_record_never_searched(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("generic equipment must not be searched")

    monkeypatch.setattr(EQ, "_apply_grokipedia_text", boom)
    monkeypatch.setattr(EQ, "_apply_wikipedia_text_extract", boom)
    rec = {
        "common_name": "cannon",
        "technical_identifier": "cannon",
        "category": "artillery",
    }
    EQ._enrich_on_identity(rec, _Grok())
    assert rec["identity_scope"] == "generic"

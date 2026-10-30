"""Prototype tests for deterministic anchor-quote -> paragraph resolution (the real fix for the
paragraph_numbers starvation: LLM returns verbatim quotes, CODE assigns paragraph numbers).
"""

from src.extraction.anchor_resolve import resolve_anchor_paragraphs, _normalize

_PARAS = [
    {
        "absolute_number": 1,
        "text": "Force U (seaborne units of the VII Corps) was marshaled in the Tor Bay area and east of Plymouth.",
    },
    {
        "absolute_number": 2,
        "text": "The entire 5th Armored Division provided housekeeping services for the mounting.",
    },
    {
        "absolute_number": 3,
        "text": "Admiral Ramsay commanded the naval forces for Operation Neptune.",
    },
]


def test_exact_quote_resolves_to_paragraph():
    nums, unresolved = resolve_anchor_paragraphs(
        ["marshaled in the Tor Bay area"], _PARAS, "S1"
    )
    assert nums == [1] and unresolved == []


def test_whitespace_and_case_tolerant():
    nums, unresolved = resolve_anchor_paragraphs(
        ["THE  ENTIRE   5th armored division"], _PARAS, "S2"
    )
    assert nums == [2] and unresolved == []


def test_hallucinated_quote_is_unresolved_not_fabricated():
    nums, unresolved = resolve_anchor_paragraphs(
        ["the 101st Airborne stormed the beach at dawn"], _PARAS, "S3"
    )
    assert nums == []  # NOT fabricated to a wrong paragraph
    assert len(unresolved) == 1


def test_multiple_quotes_union_across_paragraphs():
    nums, unresolved = resolve_anchor_paragraphs(
        [
            "seaborne units of the VII Corps",
            "Admiral Ramsay commanded the naval forces",
        ],
        _PARAS,
        "S4",
    )
    assert nums == [1, 3] and unresolved == []


def test_mixed_resolved_and_hallucinated():
    nums, unresolved = resolve_anchor_paragraphs(
        [
            "the entire 5th Armored Division",
            "totally invented sentence not in the text anywhere",
        ],
        _PARAS,
        "S5",
    )
    assert nums == [2] and len(unresolved) == 1  # resolves the real one, flags the fake


def test_fuzzy_fallback_tolerates_minor_reformatting():
    # a near-verbatim quote (comma/of dropped) should still resolve via fuzzy, not fabricate
    nums, unresolved = resolve_anchor_paragraphs(
        ["Admiral Ramsay commanded naval forces for Operation Neptune"], _PARAS, "S6"
    )
    assert nums == [3]


def test_fail_safe_on_bad_input():
    nums, unresolved = resolve_anchor_paragraphs(
        ["x"], [{"absolute_number": "notanint"}], "S7"
    )
    assert nums == []  # no crash; unmatched


def test_normalize_is_pure():
    assert _normalize("The  VII Corps, marshaled!") == "the vii corps marshaled"

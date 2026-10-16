"""_verbatim_looks_citational: military/archival green-book citations must be recognized
(so they reach NARA resolution instead of being rejected as not_citation), while prose and
stubs are still rejected (no over-matching)."""

import pytest

from src.enrichment.bibliography_resolver import _verbatim_looks_citational as cit

_CITATIONS = [
    "MS # B-810, Generalleutnant Hans Schmidt, Kaempfe im Rheinland 275. Infanterie Division.",
    "VII Corps Opns Memo 101, 1 Oct, confirming oral orders of 30 Sep.",
    "Annex 2 to 5th Armd Div G-2 Per Rpt 85, 24 Oct, 5th Armd Div G-2 file.",
    "See Interv with Collins, 25 Jan 54.",
    "Ltr, Hodges to Gerow, 11 Sep 44, FUSA G-3 file.",
    "V Corps Operations in the ETO, p. 256.",
    "12th A Gp and FUSA G-1 Daily Summaries, 12 Sep 44, FUSA G-2 TAC Misc file.",
]

_PROSE = [
    "General Cota assumed command on 14 August 1944.",  # full month name, not DD Mon YY
    "An American heavy tank did not reach the theater until 1945.",
    "The attack began at dawn.",
    "See above.",
    "Ibid.",
    # Adversarial: a military SHORT-date in narrative prose is NOT a citation on its own
    # (dates are pervasive in WWII prose) — the date is a WEAK/corroborating signal only.
    "On 6 Jun the division attacked across the river.",
    "By 12 Sep the corps had reached the border.",
    "General Collins ordered the attack at dawn on 1 Oct.",
    "Operations continued through the night of 19 Aug.",
    "Fighting raged near the Roer dams until 9 Feb.",
    "The report of enemy strength proved exaggerated.",
    "The 5th Armored Division held the line.",
]


@pytest.mark.parametrize("v", _CITATIONS)
def test_military_archival_citations_recognized(v):
    assert cit(v) is True


@pytest.mark.parametrize("v", _PROSE)
def test_prose_and_stubs_rejected(v):
    assert cit(v) is False


def test_deterministic_record_verbatim_on_mention_recognized():
    """Deterministic bibliography records (source-url split) carry the verbatim on the
    MENTION, not top-level. _looks_like_citation must read it there — else every
    deterministic record is wrongly not_citation and NARA never runs."""
    from src.enrichment.bibliography_resolver import _looks_like_citation

    rec = {
        "BibliographyID": "01X",
        "title": "Unknown",
        "citation": {"title": "Unknown"},
        "verbatim_reference": None,
        "resource_urls": [],
        "mentions": [
            {"verbatim_reference": "MS # B-810, Schmidt, Kaempfe im Rheinland."}
        ],
    }
    assert _looks_like_citation(rec) is True
    # prose on the mention must still be rejected (no over-match)
    prose = dict(rec, mentions=[{"verbatim_reference": "The attack began at dawn."}])
    assert _looks_like_citation(prose) is False


def test_nara_query_sanitized_to_keywords():
    """Raw citation punctuation (#, commas, trailing periods) triggers NARA's
    invalid-query -> 200 HTML shell failure mode. The query must be reduced to plain
    keywords (accented letters preserved for German names)."""
    from src.enrichment.bibliography_resolver import _sanitize_nara_query

    out = _sanitize_nara_query(
        "MS # B-810, Generalleutnant Schmidt, Kaempfe im Rheinland 275. Division."
    )
    assert "#" not in out and "," not in out
    assert "MS" in out and "B 810" in out and "Schmidt" in out
    assert "  " not in out  # whitespace collapsed


def test_nara_html_shell_detected():
    """NARA's documented failure mode (200 OK + SPA HTML) must be detected so it is not
    silently treated as a real not_found."""
    from src.enrichment.bibliography_resolver import _is_html_shell

    class _R:
        def __init__(self, ct, txt):
            self.headers = {"Content-Type": ct}
            self.text = txt

    assert _is_html_shell(_R("text/html; charset=utf-8", "<!doctype html>")) is True
    assert _is_html_shell(_R("application/json", '{"body":{}}')) is False
    # content-type lies but body is HTML -> still detected
    assert _is_html_shell(_R("application/json", "  <html>")) is True


def test_nara_naid_extracted_across_shapes():
    from src.enrichment.bibliography_resolver import _extract_na_id

    assert (
        _extract_na_id({"_id": "417180915", "_source": {"record": {}}}) == "417180915"
    )
    assert (
        _extract_na_id(
            {"_source": {"metadata": {"controlGroup": {"naId": 597542}}, "record": {}}}
        )
        == "597542"
    )
    assert _extract_na_id({"_source": {"record": {"naId": 123}}}) == "123"
    assert _extract_na_id({"_source": {"record": {}}}) == ""

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


def test_nara_html_shell_widened_sniff():
    """A shell prefixed by an edge comment/BOM (content-type lies) is still detected."""
    from src.enrichment.bibliography_resolver import _is_html_shell

    class _R:
        def __init__(self, ct, txt):
            self.headers = {"Content-Type": ct}
            self.text = txt

    assert (
        _is_html_shell(_R("application/json", "<!-- edge -->\n<!doctype html>")) is True
    )
    assert _is_html_shell(_R("application/json", '{"body":{"hits":{}}}')) is False


def test_nara_searchable_greenbook_forms():
    """Green-book/OCMH citation forms (MS #, Interv, Opns Memo, corps) must be NARA-
    searchable so _search_nara actually runs; plain prose must not (incl. substrings like
    'rg' in 'argues' that a bare-substring match wrongly accepted)."""
    from src.enrichment.bibliography_resolver import _is_nara_searchable

    e = {"citation": {"title": "Unknown"}}
    for v in (
        "MS # B-810, Generalleutnant Schmidt, Kaempfe im Rheinland.",
        "VII Corps Opns Memo 101, 1 Oct.",
        "See Interv with Collins, 25 Jan 54.",
        "AAR, 30th Inf Div, Oct 44.",
        "Ltr, Hodges to Bradley, 12 Nov 44.",
        "Jnl, 1st Inf Div G-3, 16 Nov 44.",
    ):
        assert _is_nara_searchable(e, v) is True, v
    for v in (
        "The attack began at dawn and continued all day.",
        "Smith argues the decision was premature.",
        "The urbane colonel charged across a large bridge in an organized group.",
    ):
        assert _is_nara_searchable(e, v) is False, v


def test_deterministic_record_routes_to_archive_resolver():
    """A deterministic record (no document_type, archival verbatim on the mention) must
    route to the ARCHIVE resolver (which queries NARA), not the generic fallback that
    never touches NARA."""
    from src.enrichment.bibliography_resolver import (
        _pick_resolver,
        _entry_verbatim,
        _is_nara_searchable,
        _resolve_archive,
    )

    rec = {
        "title": "Unknown",
        "citation": {"title": "Unknown"},
        "verbatim_reference": None,
        "mentions": [
            {"verbatim_reference": "MS # B-810, Schmidt, Kaempfe im Rheinland."}
        ],
    }
    v = _entry_verbatim(rec)
    assert v == "MS # B-810, Schmidt, Kaempfe im Rheinland."
    assert _pick_resolver("", "", _is_nara_searchable(rec, v)) is _resolve_archive
    # prose must NOT route to archive
    prose = {
        "title": "Unknown",
        "citation": {"title": "Unknown"},
        "mentions": [
            {
                "verbatim_reference": "An American heavy tank did not reach the theater until 1945."
            }
        ],
    }
    pv = _entry_verbatim(prose)
    assert (
        _pick_resolver("", "", _is_nara_searchable(prose, pv)) is not _resolve_archive
    )


def test_official_commander_diary_is_citation_and_nara_searchable():
    """A senior commander's 'X Diary' is an OFFICIAL record series (NARA RG 407 unit
    records), not a personal item. A bare 'Irwin Diary.' must be recognized as a citation
    and routed to NARA — not dropped as prose."""
    from src.enrichment.bibliography_resolver import (
        _looks_like_citation,
        _is_nara_searchable,
    )

    for v in (
        "Irwin Diary.",
        "Sylvan Diary, passim.",
        "Hodges Diary, 12 Nov 44.",
        "Patton Diary.",
    ):
        rec = {
            "citation": {"title": "Unknown"},
            "verbatim_reference": None,
            "resource_urls": [],
            "mentions": [{"verbatim_reference": v}],
        }
        assert _looks_like_citation(rec) is True, v
        assert _is_nara_searchable(rec, v) is True, v


def test_citation_key_distinct_for_deterministic_records():
    """Deterministic records (top-level verbatim=None, title='Unknown') must key on the
    MENTION verbatim / alt_title so they don't all collapse to '|unknown' and wrongly dedup
    to one in the resolver (the deduped=15/16 bug)."""
    from src.enrichment.bibliography_resolver import _citation_key

    mk = lambda v, at: {
        "verbatim_reference": None,
        "citation": {"title": "Unknown", "alt_title": at},
        "mentions": [{"verbatim_reference": v}],
    }
    keys = [
        _citation_key(mk("See Interv with Collins, 25 Jan 54.", "Interv with Collins")),
        _citation_key(mk("MS # B-810, Schmidt.", "MS # B-810")),
        _citation_key(mk("VII Corps Opns Memo 101, 1 Oct.", "VII Corps Opns Memo 101")),
    ]
    assert len(set(keys)) == 3, keys
    assert "|unknown" not in keys
    # alt_title fallback when no mention verbatim either
    only_alt = {
        "verbatim_reference": None,
        "citation": {"title": "Unknown", "alt_title": "Sylvan Diary"},
        "mentions": [],
    }
    assert _citation_key(only_alt) == "sylvan diary"


def test_record_group_lookup_is_deterministic_not_grok():
    """Record groups are a DETERMINISTIC lookup against NARA's Record Group Explorer, not a
    Grok guess, and cover ALL service branches (not just ground army). _identify_nara_record
    returns a bare 'RG N' (no prose rationale)."""
    from src.enrichment.bibliography_resolver import (
        _lookup_record_group,
        _identify_nara_record,
    )

    expect = {
        "MS # B-810, Schmidt.": "RG 549",
        "VII Corps Opns Memo 101, 1 Oct.": "RG 407",
        "5th Armd Div G-2 Per Rpt 85.": "RG 407",
        "Kriegstagebuch, OB WEST, 16 Nov 44.": "RG 242",
        "SHAEF G-3 Summary.": "RG 331",
        "398th Bomb Gp Mission Report, 8th Air Force, 14 Nov 44.": "RG 18",
        "War Diary, USS Texas, 6 Jun 44.": "RG 38",
        "4th Marine Division AAR, Iwo Jima.": "RG 127",
        "USCG cutter patrol report.": "RG 26",
        "USSBS European Report 3.": "RG 243",
        "OSS London mission file.": "RG 226",
    }
    for v, rg in expect.items():
        got = _lookup_record_group(v)
        assert got is not None and got[0] == rg, (v, got)
    # service-specific must win over the generic Army unit-record rule
    assert _lookup_record_group("4th Marine Division AAR, Iwo Jima.")[0] == "RG 127"
    assert _lookup_record_group("War Diary, USS Texas, 6 Jun 44.")[0] == "RG 38"
    assert _lookup_record_group("Just some narrative prose.") is None
    # bare RG string, no Grok call, no rationale
    ref = _identify_nara_record("MS # B-810, Schmidt.", grok_client=None)
    assert ref == "RG 549"
    assert "\n" not in ref and "references" not in ref.lower()
    assert _identify_nara_record("Just some narrative prose.", grok_client=None) is None


def test_rg_lookup_multi_theater_and_service_categories():
    """RG lookup covers all WWII service branches + theaters + technical services by
    subject matter (not ETO-only), and existing corpus citations don't regress."""
    from src.enrichment.bibliography_resolver import _lookup_record_group

    cases = {
        "MS # B-810, Schmidt.": "RG 549",
        "VII Corps Opns Memo 101.": "RG 407",
        "398th Bomb Gp Mission Report, 8th Air Force.": "RG 18",
        "War Diary, USS Texas.": "RG 38",
        "4th Marine Division AAR.": "RG 127",
        "CBI Theater, Stilwell.": "RG 493",
        "CINCPAC, Pacific Ocean Areas.": "RG 494",
        "GHQ SWPA, MacArthur HQ.": "RG 554",
        "Joint Chiefs of Staff, CCS 94.": "RG 218",
        "Chief Signal Officer photo file.": "RG 111",
        "Chemical Warfare Service report.": "RG 175",
        "Judge Advocate General court-martial.": "RG 153",
        "Department of State, FRUS 1944.": "RG 59",
        "War Relocation Authority center.": "RG 210",
    }
    for v, rg in cases.items():
        got = _lookup_record_group(v)
        assert got is not None and got[0] == rg, (v, got)
    assert _lookup_record_group("Just narrative prose.") is None


def test_nara_digital_objects_detects_online():
    """A NARA record with digitalObjects is ALREADY ONLINE -> return direct (http) scan URLs;
    a description-only record returns none. Non-http object URLs are dropped."""
    from src.enrichment.bibliography_resolver import _nara_digital_objects

    online = _nara_digital_objects(
        {
            "digitalObjects": [
                {"objectUrl": "https://catalog.archives.gov/media/x.pdf"},
                {"objectUrl": "ftp://bad"},
            ]
        }
    )
    assert online == ["https://catalog.archives.gov/media/x.pdf"]
    assert _nara_digital_objects({"title": "desc only"}) == []


def test_nara_match_context_includes_series_and_unit():
    """Verification context carries the series/ancestors path + level + scope so a correct
    series/unit match is recognizable behind a cryptic file-unit title."""
    from src.enrichment.bibliography_resolver import _nara_match_context

    ctx = _nara_match_context(
        {
            "levelOfDescription": "fileUnit",
            "title": "V Corps: 3-011",
            "ancestors": [
                {"title": "Records of the Adjutant General's Office"},
                {"title": "World War II Operations Reports"},
            ],
            "scopeAndContentNote": "G-2 periodic reports.",
        }
    )
    assert "World War II Operations Reports" in ctx
    assert "fileUnit" in ctx
    assert "G-2 periodic reports" in ctx
    assert _nara_match_context({}) == ""

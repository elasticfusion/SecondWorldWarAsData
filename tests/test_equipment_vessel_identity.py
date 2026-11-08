"""Named-vessel disambiguation (src/extraction/equipment_identity) + identity matching.

Owner rule (2026-11-07): "the ship must be disambiguated ... we are only interested in the WWII
version" - and hull numbers are not in the text. Two WWII USS Quincys existed (CA-39 sunk at Savo
Island 9 Aug 1942; CA-71 commissioned 15 Dec 1943, at Normandy), so the mention's DATE decides.
Infobox shapes below are from the real Wikipedia articles (verified live 2026-11-07), including
the field-spacing differences between pages.
"""

import datetime as dt
import json

import pytest

from src.extraction import equipment_identity as EI

AK10 = """{{Infobox ship begin}}
{{Infobox ship career
| Ship name = ''Bavaria''
| commissioned =
| decommissioned =
| fate = Seized in 1917 by US Customs at [[Pensacola, Florida]]
}}
{{Infobox ship career
| Ship name = USS ''Quincy''
| commissioned = 2 February 1918, as USS ''Quincy''
| recommissioned =
| decommissioned = 5 June 1922
| struck =
}}
{{Infobox ship characteristics
| Ship type = Cargo ship
}}"""
CA39 = """{{Infobox ship career
|commissioned= 9 June 1936
|decommissioned=
|struck=
|fate=Sunk, [[Battle of Savo Island]] 9 August 1942
}}
{{Infobox ship characteristics
|class={{sclass|New Orleans|cruiser}}
}}"""
CA71 = """{{Infobox ship career
|commissioned=15 December 1943
|decommissioned=2 July 1954
|struck=1 October 1973
|fate=[[Ship breaking|Scrapped]], 1974
}}
{{Infobox ship characteristics
|class={{sclass|Baltimore|cruiser|0}} [[heavy cruiser]]
}}"""
PAGES = {
    "USS Quincy (AK-10)": AK10,
    "USS Quincy (CA-39)": CA39,
    "USS Quincy (CA-71)": CA71,
}


# --- parsing -------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value,end,expected",
    [
        ("9 June 1936", False, dt.date(1936, 6, 9)),
        ("December 15, 1943", False, dt.date(1943, 12, 15)),
        ("Sunk, [[Battle of Savo Island]] 9 August 1942", True, dt.date(1942, 8, 9)),
        ("[[Ship breaking|Scrapped]], 1974", True, dt.date(1974, 12, 31)),
        ("1974", False, dt.date(1974, 1, 1)),
        ("", False, None),
    ],
)
def test_parse_date(value, end, expected):
    assert EI.parse_date(value, end=end) == expected


def test_service_periods_handle_careers_fates_and_spacing():
    # AK-10: first career ended by seizure (end only, no start), second 1918-1922
    assert EI.service_periods(AK10) == [
        (None, dt.date(1917, 12, 31)),
        (dt.date(1918, 2, 2), dt.date(1922, 6, 5)),
    ]
    assert EI.in_service(EI.service_periods(AK10), dt.date(1944, 6, 6)) is False
    # CA-39: a sinking ENDS service (no decommissioning date)
    assert EI.service_periods(CA39) == [(dt.date(1936, 6, 9), dt.date(1942, 8, 9))]
    assert EI.service_periods(CA71) == [(dt.date(1943, 12, 15), dt.date(1954, 7, 2))]


def test_ship_class_reads_only_its_own_line():
    assert "baltimore" in EI.ship_class(CA71) and "cruiser" in EI.ship_class(CA71)
    assert (
        EI.ship_class("| class =\n| type = Cargo ship") == ""
    )  # empty field, not the next line


# --- resolution ----------------------------------------------------------------------------------


@pytest.fixture
def wiki(monkeypatch):
    """Fake Wikipedia: the "USS Quincy" index page links the three articles."""
    calls = []
    store = {}

    def fake_get(params, subject):
        calls.append(params["titles"])
        title = params["titles"]
        if title == "USS Quincy":
            return {
                "query": {
                    "pages": {
                        "1": {
                            "title": "USS Quincy",
                            "links": [{"title": t} for t in PAGES]
                            + [{"title": "Quincy, MA"}],
                            "revisions": [{"slots": {"main": {"*": "{{shipindex}}"}}}],
                        }
                    }
                }
            }
        if title in PAGES:
            return {
                "query": {
                    "pages": {
                        "2": {
                            "title": title,
                            "revisions": [{"slots": {"main": {"*": PAGES[title]}}}],
                        }
                    }
                }
            }
        return {"query": {"pages": {"-1": {"title": title, "missing": ""}}}}

    import src.utils.search_cache as sc

    monkeypatch.setattr(EI, "_wiki_get", fake_get)
    monkeypatch.setattr(sc, "get_cached", lambda s, q: store.get((s, q)))
    monkeypatch.setattr(
        sc,
        "cache_result",
        lambda s, q, r: (
            None if r is sc.TRANSIENT else store.__setitem__((s, q), r or "NOT_FOUND")
        ),
    )
    return calls, store


@pytest.mark.parametrize(
    "date,expected",
    [("1944-06-06", "USS Quincy (CA-71)"), ("1942-08-08", "USS Quincy (CA-39)")],
)
def test_the_date_picks_the_ship(wiki, date, expected):
    mention = {"date": date, "operating_country": "USA"}
    out = EI.resolve_vessel("Quincy", mention, "heavy_cruiser")
    assert out["resolved_title"] == expected and out["method"] == "service_dates"
    assert out["date_used"] == date and out["date_source"] == "mention"
    assert {c["title"] for c in out["candidates"]} == set(
        PAGES
    )  # every candidate recorded


def test_candidate_list_is_cached_per_name_and_reused_for_any_date(wiki):
    calls, _ = wiki
    EI.resolve_vessel(
        "Quincy", {"date": "1944-06-06", "operating_country": "USA"}, None
    )
    n = len(calls)
    out = EI.resolve_vessel(
        "Quincy", {"date": "1942-08-08", "operating_country": "USA"}, None
    )
    assert len(calls) == n  # no new Wikipedia calls
    assert out["resolved_title"] == "USS Quincy (CA-39)"


def test_transient_wikipedia_is_not_cached_and_reported(monkeypatch, wiki):
    import src.utils.search_cache as sc

    _, store = wiki
    monkeypatch.setattr(EI, "_wiki_get", lambda params, subject: None)
    out = EI.resolve_vessel(
        "Quincy", {"date": "1944-06-06", "operating_country": "USA"}, None
    )
    assert out["transient"] is True and out["resolved_title"] is None
    assert not store


class _ChooserGrok:
    def __init__(self, title, confidence):
        self.title, self.confidence, self.prompts = title, confidence, []

    def extract_json(self, prompt, **_k):
        self.prompts.append(prompt)
        return {"title": self.title, "confidence": self.confidence}


def test_no_date_falls_back_to_grok_choosing_among_candidates(wiki):
    grok = _ChooserGrok("USS Quincy (CA-71)", 0.9)
    out = EI.resolve_vessel(
        "Quincy",
        {"original_text": "Quincy fired on the Utah Beach batteries."},
        "heavy_cruiser",
    )
    assert out["resolved_title"] is None  # no grok client passed above
    out = EI.resolve_vessel(
        "Quincy",
        {"original_text": "Quincy fired on the Utah Beach batteries."},
        "heavy_cruiser",
        grok,
    )
    assert (
        out["resolved_title"] == "USS Quincy (CA-71)" and out["method"] == "grok_choice"
    )
    assert "Utah Beach" in grok.prompts[0] and "USS Quincy (CA-39)" in grok.prompts[0]


@pytest.mark.parametrize(
    "title,confidence",
    [("USS Quincy (CA-99)", 0.99), ("USS Quincy (CA-71)", 0.5), (None, 0.9)],
)
def test_grok_cannot_invent_a_ship_or_guess(wiki, title, confidence):
    out = EI.resolve_vessel(
        "Quincy", {"original_text": "x"}, None, _ChooserGrok(title, confidence)
    )
    assert out["resolved_title"] is None


# --- identity matching in the extractor ----------------------------------------------------------


def test_two_ships_of_the_same_name_never_merge(wiki, tmp_path, monkeypatch):
    from src.extraction import equipment as EQ

    monkeypatch.setattr(EQ, "_enrich_on_identity", lambda *a, **k: None)
    index = {}
    rec = lambda: {
        "common_name": "Quincy",
        "category": "naval",
        "subcategory": "heavy_cruiser",
    }  # noqa: E731
    f44 = EQ.merge_or_create_equipment(
        rec(),
        {
            "MentionID": "01A",
            "EventID": "01E1",
            "date": "1944-06-06",
            "operating_country": "USA",
        },
        tmp_path,
        index,
    )
    f42 = EQ.merge_or_create_equipment(
        rec(),
        {
            "MentionID": "01B",
            "EventID": "01E2",
            "date": "1942-08-08",
            "operating_country": "USA",
        },
        tmp_path,
        index,
    )
    f44b = EQ.merge_or_create_equipment(
        rec(),
        {
            "MentionID": "01C",
            "EventID": "01E3",
            "date": "1944-06-07",
            "operating_country": "USA",
        },
        tmp_path,
        index,
    )
    assert f44 != f42 and f44 == f44b
    ca71 = json.loads(f44.read_text())
    assert ca71["canonical_name"] == "USS Quincy (CA-71)"
    assert ca71["identity_source"] == "vessel_disambiguation"
    assert ca71["identity_scope"] == "specific"
    assert len(ca71["event_mentions"]) == 2
    assert json.loads(f42.read_text())["canonical_name"] == "USS Quincy (CA-39)"


def test_unresolved_vessel_is_kept_apart_and_alerted(monkeypatch, tmp_path, caplog):
    import logging

    import src.utils.search_cache as sc
    from src.extraction import equipment as EQ
    from src.utils.observe import reset_counted_warnings

    reset_counted_warnings()
    monkeypatch.setattr(EI, "_wiki_get", lambda params, subject: None)
    monkeypatch.setattr(sc, "get_cached", lambda s, q: None)
    monkeypatch.setattr(EQ, "_enrich_on_identity", lambda *a, **k: None)
    with caplog.at_level(logging.WARNING):
        f = EQ.merge_or_create_equipment(
            {"common_name": "Augusta", "category": "naval", "subcategory": "cruiser"},
            {"MentionID": "01A", "EventID": "01E1", "date": "1944-06-06"},
            tmp_path,
            {},
        )
    rec = json.loads(f.read_text())
    assert rec["identity_scope"] == "unresolved"
    # keyed by the mention year: unresolved mentions of different years never share a record
    assert rec["canonical_name"] == "Augusta (unresolved vessel, 1944)"
    assert (
        rec["identity_resolution"]["transient"] is True
    )  # why (Wikipedia unreachable)
    assert any("vessel_unresolved" in r.getMessage() for r in caplog.records)


def test_unresolved_vessel_is_never_enriched():
    from src.extraction import equipment as EQ

    class _Boom:
        def extract_json(self, *a, **k):
            raise AssertionError("must not be called")

    rec = {"common_name": "Augusta", "identity_scope": "unresolved"}
    EQ._enrich_on_identity(rec, _Boom())
    assert "enrichment_status" not in rec


def test_same_name_records_created_in_the_same_millisecond_get_distinct_files(tmp_path):
    # the filename used the ULID TIMESTAMP prefix: same name + same ms -> same file, overwritten
    from src.extraction import equipment as EQ

    index = {}
    f1 = EQ._create_new_equipment(
        {"common_name": "Quincy"}, {"MentionID": "01A"}, tmp_path, index, None, False
    )
    f2 = EQ._create_new_equipment(
        {"common_name": "Quincy"}, {"MentionID": "01B"}, tmp_path, index, None, False
    )
    assert f1 != f2 and f1.exists() and f2.exists()

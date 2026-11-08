"""Hardening of the equipment identity / enrichment / image rules (independent review, 2026-11-07).

Each test pins one way the first version could be wrong:
- generic names ("AA gun", "HE shell") read as designations and got searched;
- a ship was "resolved" with no date evidence, or a torpedoed-and-repaired ship was ended early;
- transient Wikipedia / Grokipedia failures were stamped as misses (never retried) or cached;
- a resolved vessel was not found again after a restart (duplicate record per resume);
- legacy images were trusted, fetch failures spent the vision budget, duplicates slipped across runs.
"""

import datetime as dt
import json
import logging

import pytest

from src.extraction import equipment as EQ
from src.extraction import equipment_identity as EI

# --- generic vs specific ----------------------------------------------------------------------


@pytest.mark.parametrize(
    "name", ["AA gun", "AT gun", "US rifle", "HE shell", "SS troops", "Allied AT guns"]
)
def test_generic_capital_words_are_not_designations(name):
    assert EI.has_designation(name) is False


@pytest.mark.parametrize(
    "name",
    [
        "LCI(L)",
        "LCVP",
        "LST-325",
        "DUKW",
        "PT boat",
        "BAR",
        "PIAT",
        "DD tank",
        "M1 Garand",
    ],
)
def test_class_acronyms_and_designations_stay_specific(name):
    assert EI.has_designation(name) is True


@pytest.mark.parametrize(
    "name",
    ["Higgins Boat", "Mulberry Harbour", "Landing Craft Vehicle", "Liberty Ship"],
)
def test_naval_kinds_are_not_named_vessels(name):
    assert EI.is_named_vessel(name, "naval") is False


def test_a_ships_own_name_is_a_named_vessel():
    assert EI.is_named_vessel("Quincy", "naval") is True


def test_a_lone_year_does_not_qualify_text_as_wwii():
    assert EI.mentions_wwii("A 1944 photograph of the post-war prototype.") is False
    assert EI.mentions_wwii("It served throughout the Second World War.") is True


# --- service periods ---------------------------------------------------------------------------


def _career(fate, decommissioned=""):
    return (
        "{{Infobox ship career\n| commissioned = 1 March 1916\n"
        f"| decommissioned = {decommissioned}\n| fate = {fate}\n}}}}"
    )


def test_a_ship_sunk_then_raised_and_repaired_stays_in_service():
    periods = EI.service_periods(
        _career("Sunk 7 December 1941, raised, repaired, returned to service", "1946")
    )
    assert periods == [(dt.date(1916, 3, 1), dt.date(1946, 12, 31))]
    assert EI.in_service(periods, dt.date(1944, 6, 6)) is True


def test_torpedoed_alone_does_not_end_service():
    periods = EI.service_periods(_career("Torpedoed 5 May 1942", "1947"))
    assert periods == [(dt.date(1916, 3, 1), dt.date(1947, 12, 31))]


def test_a_terminal_fate_ends_service():
    periods = EI.service_periods(_career("Scuttled 21 June 1919"))
    assert periods == [(dt.date(1916, 3, 1), dt.date(1919, 6, 21))]


# --- candidate filtering + choice ---------------------------------------------------------------


def _cand(title, cls="", in_service=None):
    return {"title": title, "class": cls, "service": [], "in_service": in_service}


def test_type_filter_drops_a_contradicting_class_and_keeps_unknown():
    cands = [
        _cand("A", "baltimore class cruiser"),
        _cand("B", "fletcher class destroyer"),
        _cand("C", ""),
    ]
    kept = EI.filter_candidates(cands, None, "heavy_cruiser")
    assert [c["title"] for c in kept] == ["A", "C"]


def test_destroyer_escort_is_not_a_destroyer_and_vice_versa():
    cands = [
        _cand("DE", "buckley class destroyer escort"),
        _cand("DD", "fletcher class destroyer"),
    ]
    assert [c["title"] for c in EI.filter_candidates(cands, None, "destroyer")] == [
        "DD"
    ]
    assert [
        c["title"] for c in EI.filter_candidates(cands, None, "destroyer_escort")
    ] == ["DE"]


def test_vague_subcategory_does_not_filter():
    cands = [_cand("A", "x class cruiser"), _cand("B", "y class destroyer")]
    assert len(EI.filter_candidates(cands, None, "warship")) == 2


class _Chooser:
    def __init__(self, result):
        self.result, self.calls = result, 0

    def extract_json(self, prompt, **_k):
        self.calls += 1
        return self.result


def test_a_sole_candidate_without_a_date_is_not_taken_on_faith(monkeypatch):
    monkeypatch.setattr(
        EI, "wikipedia_candidates", lambda name, navy, on: [_cand("USS Texas (BB-35)")]
    )
    out = EI.resolve_vessel(
        "Texas", {"operating_country": "USA"}, None, grok_client=None
    )
    assert out["resolved_title"] is None  # no date, no Grok: unresolved, never guessed
    grok = _Chooser({"title": "USS Texas (BB-35)", "confidence": 0.9})
    out = EI.resolve_vessel(
        "Texas", {"operating_country": "USA"}, None, grok_client=grok
    )
    assert grok.calls == 1 and out["method"] == "grok_choice"
    assert out["resolved_title"] == "USS Texas (BB-35)"


def test_a_sole_in_service_candidate_on_the_date_is_resolved_by_dates(monkeypatch):
    monkeypatch.setattr(
        EI,
        "wikipedia_candidates",
        lambda name, navy, on: [_cand("USS Texas (BB-35)", in_service=True)],
    )
    out = EI.resolve_vessel("Texas", {"date": "1944-06-06"}, None)
    assert (
        out["resolved_title"] == "USS Texas (BB-35)"
        and out["method"] == "service_dates"
    )


# --- Wikipedia transient handling --------------------------------------------------------------


class _Resp:
    status_code = 200

    def __init__(self, body):
        self._body = body

    def json(self):
        return self._body


def test_a_mediawiki_error_body_is_transient(monkeypatch):
    import src.utils.wikimedia_client as WC

    monkeypatch.setattr(
        WC, "wikimedia_get", lambda *a, **k: _Resp({"error": {"code": "maxlag"}})
    )
    assert EI._wiki_get({"titles": "USS Quincy"}, "Quincy") is None


def _pages(**page):
    return {"query": {"pages": {"1": page}}}


def test_a_listed_article_with_no_revision_is_transient_and_a_red_link_skipped(
    monkeypatch,
):
    monkeypatch.setattr(EI, "_index_titles", lambda name, prefix: ["A", "B"])
    replies = {
        "A": _pages(title="A", missing=""),  # red link
        "B": _pages(title="B"),  # listed but unreadable now
    }
    monkeypatch.setattr(EI, "_query", lambda title, with_links: replies[title])
    assert EI._fetch_candidates("Q", ["USS"]) is None
    replies["B"] = _pages(title="B", revisions=[{"*": "{{Infobox ship career\n}}"}])
    assert [c["title"] for c in EI._fetch_candidates("Q", ["USS"])] == ["B"]


def test_index_links_follow_continuation(monkeypatch):
    calls = []

    def fake_get(params, subject):
        calls.append(params.get("plcontinue"))
        if not params.get("plcontinue"):
            return {
                "continue": {"plcontinue": "x"},
                **_pages(title="USS Q", links=[{"title": "USS Q (CA-1)"}]),
            }
        return _pages(title="USS Q", links=[{"title": "USS Q (CA-2)"}])

    monkeypatch.setattr(EI, "_wiki_get", fake_get)
    data = EI._query("USS Q", with_links=True)
    assert [link["title"] for link in EI._page(data)["links"]] == [
        "USS Q (CA-1)",
        "USS Q (CA-2)",
    ]
    assert calls == [None, "x"]


def test_unending_continuation_is_incomplete_not_cached(monkeypatch):
    monkeypatch.setattr(
        EI,
        "_wiki_get",
        lambda params, subject: {"continue": {"plcontinue": "x"}, **_pages(title="T")},
    )
    assert EI._query("T", with_links=True) is None


def test_a_corrupted_cached_candidate_list_is_refetched(monkeypatch):
    import src.utils.search_cache as sc

    monkeypatch.setattr(
        sc,
        "get_cached",
        lambda s, q: json.dumps([{"title": "X", "service": [["bad", None]]}]),
    )
    monkeypatch.setattr(sc, "cache_result", lambda *a: None)
    monkeypatch.setattr(EI, "_fetch_candidates", lambda name, prefixes: [_cand("Y")])
    out = EI.wikipedia_candidates("Q", "USA", dt.date(1944, 6, 6))
    assert [c["title"] for c in out] == ["Y"]


# --- encyclopedia text: transient is deferred, never stamped ------------------------------------


def test_grokipedia_search_outage_defers(monkeypatch):
    monkeypatch.setattr(
        "src.enrichment.grokipedia.resolve_grokipedia_url_status",
        lambda n: (None, True),
    )
    with pytest.raises(EQ._SourceDeferred):
        EQ._apply_grokipedia_text({}, "M1 Garand")


def test_grokipedia_article_outage_defers(monkeypatch):
    from src.utils.search_cache import TRANSIENT

    monkeypatch.setattr(
        "src.enrichment.grokipedia.resolve_grokipedia_url_status",
        lambda n: ("https://grokipedia.com/page/M1_Garand", False),
    )
    monkeypatch.setattr(
        "src.enrichment.grokipedia.fetch_grokipedia_article_text", lambda url: TRANSIENT
    )
    with pytest.raises(EQ._SourceDeferred):
        EQ._apply_grokipedia_text({}, "M1 Garand")


def test_grokipedia_parser_error_defers_not_misses(monkeypatch):
    def boom(n):
        raise ValueError("parser")

    monkeypatch.setattr("src.enrichment.grokipedia.resolve_grokipedia_url_status", boom)
    with pytest.raises(EQ._SourceDeferred):
        EQ._apply_grokipedia_text({}, "M1 Garand")


def test_grokipedia_genuine_miss_is_none(monkeypatch):
    monkeypatch.setattr(
        "src.enrichment.grokipedia.resolve_grokipedia_url_status",
        lambda n: (None, False),
    )
    assert EQ._apply_grokipedia_text({}, "M1 Garand") is None


def test_resolve_status_reports_transient_and_does_not_cache(monkeypatch):
    import src.enrichment.grokipedia as G
    import src.utils.search_cache as sc

    cached = []
    monkeypatch.setattr(sc, "get_cached", lambda s, q: None)
    monkeypatch.setattr(sc, "cache_result", lambda *a: cached.append(a))
    monkeypatch.setattr(G, "_scrape_grokipedia_url", lambda n, t: sc.TRANSIENT)
    assert G.resolve_grokipedia_url_status("M1 Garand") == (None, True)
    assert G.resolve_grokipedia_url("M1 Garand") is None
    assert cached == []


class _Grok:
    def extract_json(self, *a, **k):
        return {}


def test_deferred_enrichment_leaves_the_record_unstamped_and_alerts(
    monkeypatch, caplog
):
    from src.utils.observe import reset_counted_warnings

    reset_counted_warnings()

    def deferred(*a, **k):
        raise EQ._SourceDeferred("wikipedia: M4 Sherman")

    monkeypatch.setattr(EQ, "_enrich_from_encyclopedias", deferred)
    monkeypatch.setattr(EQ, "_resolve_canonical_identity", lambda d, g: "M4 Sherman")
    rec = {
        "common_name": "M4 Sherman",
        "technical_identifier": "M4",
        "category": "armor",
    }
    with caplog.at_level(logging.WARNING):
        EQ._enrich_on_identity(rec, _Grok())
    assert "enrichment_status" not in rec  # retried on the next mention / run
    assert (
        "enrichment_checked_at" not in rec
    )  # the staleness gate does not hold it back
    assert any("enrich_deferred" in r.getMessage() for r in caplog.records)


# --- vessel records are found again after a restart ---------------------------------------------


def test_index_carries_canonical_names(tmp_path):
    (tmp_path / "a.json").write_text(
        json.dumps({"common_name": "Quincy", "canonical_name": "USS Quincy (CA-71)"})
    )
    (tmp_path / "b.json").write_text(json.dumps({"common_name": "Sherman"}))
    idx = EQ.load_equipment_index(tmp_path)
    assert idx["USS Quincy (CA-71)"] == tmp_path / "a.json"
    assert (
        idx["Quincy"] == tmp_path / "a.json" and idx["Sherman"] == tmp_path / "b.json"
    )


def test_a_common_name_key_wins_over_another_records_canonical(tmp_path):
    (tmp_path / "a.json").write_text(
        json.dumps({"common_name": "X", "canonical_name": "M4"})
    )
    (tmp_path / "b.json").write_text(json.dumps({"common_name": "M4"}))
    assert EQ.load_equipment_index(tmp_path)["M4"] == tmp_path / "b.json"


def test_a_resolved_vessel_merges_after_a_restart(tmp_path, monkeypatch):
    monkeypatch.setattr(
        EI,
        "resolve_vessel",
        lambda name, mention, sub, grok=None: {
            "resolved_title": "USS Quincy (CA-71)",
            "method": "service_dates",
            "candidates": [],
            "date_used": mention.get("date"),
            "date_source": "mention",
            "confidence": 1.0,
        },
    )
    monkeypatch.setattr(EQ, "_enrich_on_identity", lambda *a, **k: None)
    data = {"common_name": "Quincy", "category": "naval", "subcategory": "cruiser"}
    first = EQ.merge_or_create_equipment(
        dict(data),
        {"MentionID": "01A", "EventID": "01E1", "date": "1944-06-06"},
        tmp_path,
        {},
    )
    # a new process: the index comes from disk
    second = EQ.merge_or_create_equipment(
        dict(data),
        {"MentionID": "01B", "EventID": "01E2", "date": "1944-06-10"},
        tmp_path,
        EQ.load_equipment_index(tmp_path),
    )
    assert first == second
    rec = json.loads(first.read_text())
    assert len(rec["event_mentions"]) == 2
    assert len(list(tmp_path.glob("*.json"))) == 1


def test_unresolved_mentions_of_different_years_stay_apart(tmp_path, monkeypatch):
    monkeypatch.setattr(
        EI,
        "resolve_vessel",
        lambda name, mention, sub, grok=None: {
            "resolved_title": None,
            "method": None,
            "candidates": [],
            "date_used": mention.get("date"),
            "date_source": "mention",
            "confidence": None,
        },
    )
    monkeypatch.setattr(EQ, "_enrich_on_identity", lambda *a, **k: None)
    data = {"common_name": "Quincy", "category": "naval"}
    a = EQ.merge_or_create_equipment(
        dict(data),
        {"MentionID": "01A", "EventID": "01E1", "date": "1942-08-08"},
        tmp_path,
        {},
    )
    b = EQ.merge_or_create_equipment(
        dict(data),
        {"MentionID": "01B", "EventID": "01E2", "date": "1944-06-06"},
        tmp_path,
        EQ.load_equipment_index(tmp_path),
    )
    assert a != b
    names = {json.loads(f.read_text())["canonical_name"] for f in (a, b)}
    assert names == {
        "Quincy (unresolved vessel, 1942)",
        "Quincy (unresolved vessel, 1944)",
    }


# --- review round 2 ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "fate",
    [
        "Sunk 9 June 1944; wreck salvaged and scrapped 1950",
        "Scrapped 1946, parts rebuilt",
    ],
)
def test_salvage_or_rebuild_of_parts_does_not_revive_a_lost_ship(fate):
    (period,) = EI.service_periods(_career(fate))
    assert period[1] is not None and period[1].year in (1944, 1946)


def test_a_continuation_completing_on_the_last_allowed_page_is_complete(monkeypatch):
    n = {"i": 0}

    def fake_get(params, subject):
        n["i"] += 1
        more = n["i"] <= EI._MAX_LINK_PAGES  # the last allowed fetch ends the chain
        page = _pages(title="T", links=[{"title": f"T ({n['i']})"}])
        return {"continue": {"plcontinue": str(n["i"])}, **page} if more else page

    monkeypatch.setattr(EI, "_wiki_get", fake_get)
    data = EI._query("T", with_links=True)
    assert data is not None and len(EI._page(data)["links"]) == EI._MAX_LINK_PAGES + 1


@pytest.mark.parametrize(
    "key,expected",
    [
        ("USS Quincy (CA-71)", True),
        ("HMS Belfast (C35)", True),
        ("German battleship Bismarck", True),
        ("Quincy (unresolved vessel, 1944)", True),
        ("M4 Sherman", False),
        ("Quincy", False),
    ],
)
def test_vessel_keys(key, expected):
    assert EI.is_vessel_key(key) is expected


def test_a_ship_title_is_never_a_fuzzy_match(tmp_path):
    f = tmp_path / "q.json"
    f.write_text(json.dumps({"common_name": "Quincy"}))
    idx = {"USS Quincy (CA-71)": f}
    assert EQ._fuzzy_match_equipment("USS Quincy (CA-7)", idx) is None

"""Casualties Phase A cross-reference upgrades: shared-library resolution."""

from src.extraction.casualties import _resolve_equipment, _resolve_date


def test_equipment_resolves_via_disambiguator_and_index():
    idx = {"m4 sherman": "01EQSHERMAN0000000000000AA"}
    r = _resolve_equipment([{"name": "Sherman", "relation": "causative"}], idx)
    assert r[0]["EquipmentID"] == "01EQSHERMAN0000000000000AA"
    assert r[0]["relation"] == "causative"


def test_equipment_unresolved_is_null_not_fabricated():
    r = _resolve_equipment([{"name": "ambulance", "relation": "medical"}], {})
    assert r == [{"EquipmentID": None, "name": "ambulance", "relation": "medical"}]


def test_date_interval_link_matches_approximate_record():
    # dates_index as _build_date_id_lookup emits: date_start -> {DateID, resolved_*}
    idx = {
        "mid-December 1944": {
            "DateID": "01DATEMIDDEC00000000000000",
            "resolved_earliest": "1944-12-14",
            "resolved_latest": "1944-12-18",
            "time_source": "Allied",
        }
    }
    # a casualty on a specific in-interval day links to the approximate record
    res = _resolve_date("1944-12-16", idx)
    assert res["DateID"] == "01DATEMIDDEC00000000000000"
    assert res["time_source"] == "Allied"


def test_date_unmatched_keeps_string_null_id():
    res = _resolve_date("1943-01-01", {})
    assert res["DateID"] is None and res["date_string"] == "1943-01-01"


def test_year_inferred_from_context_when_missing():
    from src.extraction.casualties import _parse_date_string, _resolve_date

    # year-less date + context year -> parses; no fabrication without context
    assert _parse_date_string("9 August", 1944) == "1944-08-09"
    assert _parse_date_string("9 August") is None
    assert _parse_date_string("August", 1944) == "1944-08-01"
    # a date that already has a year ignores the fallback (no overwrite)
    assert _parse_date_string("18 July 1943", 1944) == "1943-07-18"
    # resolve path: bare 'August' resolves against an in-year interval record
    idx = {
        "1944-08": {
            "DateID": "01DATEAUG19440000000000000",
            "resolved_earliest": "1944-08-01",
            "resolved_latest": "1944-08-31",
            "time_source": None,
        }
    }
    assert (
        _resolve_date("9 August", idx, 1944)["DateID"] == "01DATEAUG19440000000000000"
    )


def test_event_year_lookup_single_vs_multi(tmp_path):
    import json
    from src.extraction.casualties import _event_year_lookup

    d = tmp_path / "dates"
    d.mkdir()
    (d / "a.json").write_text(
        json.dumps(
            {
                "DateID": "01A",
                "date_start": "1944-08-09",
                "event_mentions": [{"EventID": "EV1"}, {"EventID": "EV2"}],
            }
        )
    )
    (d / "b.json").write_text(
        json.dumps(
            {
                "DateID": "01B",
                "date_start": "1945-01-02",
                "event_mentions": [{"EventID": "EV2"}],
            }
        )
    )
    lk = _event_year_lookup(d)
    assert lk["EV1"] == 1944  # single-year event -> that year
    assert lk["EV2"] is None  # spans 1944 + 1945 -> ambiguous, no inference

"""Tests for people deduplication logic."""

from src.extraction.people import (
    _deduplicate_ranks,
    _deduplicate_awards,
    _deduplicate_units,
    _normalize_rank,
    _normalize_branch,
    _normalize_name,
)


def test_deduplicate_ranks_prefers_dated():
    ranks = [
        {"rank": "General", "date": None, "branch": "U.S. Army"},
        {"rank": "General", "date": "1944", "branch": "U.S. Army"},
    ]
    result = _deduplicate_ranks(ranks)
    assert len(result) == 1
    assert result[0]["date"] == "1944"


def test_deduplicate_ranks_normalizes_abbreviations():
    ranks = [
        {"rank": "Gen.", "date": "1944", "branch": "U.S. Army"},
        {"rank": "General", "date": None, "branch": "U.S. Army"},
    ]
    result = _deduplicate_ranks(ranks)
    assert len(result) == 1
    assert result[0]["rank"] == "General"
    assert result[0]["date"] == "1944"


def test_deduplicate_ranks_keeps_different():
    ranks = [
        {"rank": "Colonel", "date": "1942", "branch": "U.S. Army"},
        {"rank": "General", "date": "1944", "branch": "U.S. Army"},
    ]
    result = _deduplicate_ranks(ranks)
    assert len(result) == 2


def test_deduplicate_awards_prefers_full_date():
    awards = [
        {"award": "Purple Heart", "class": None, "date_awarded": "1944"},
        {"award": "Purple Heart", "class": None, "date_awarded": "1944-06-06"},
    ]
    result = _deduplicate_awards(awards)
    assert len(result) == 1
    assert result[0]["date_awarded"] == "1944-06-06"


def test_deduplicate_awards_prefers_year_over_none():
    awards = [
        {"award": "Purple Heart", "class": None, "date_awarded": None},
        {"award": "Purple Heart", "class": None, "date_awarded": "1944"},
    ]
    result = _deduplicate_awards(awards)
    assert len(result) == 1
    assert result[0]["date_awarded"] == "1944"


def test_deduplicate_units_prefers_both_dates():
    units = [
        {"unit": "101st Airborne", "from": "1942", "to": None},
        {"unit": "101st Airborne", "from": "1942", "to": "1945"},
    ]
    result = _deduplicate_units(units)
    assert len(result) == 1
    assert result[0]["to"] == "1945"


def test_deduplicate_units_normalizes():
    units = [
        {"unit": "OPD", "from": "1942", "to": "1945"},
        {"unit": "Operations Division", "from": None, "to": None},
    ]
    result = _deduplicate_units(units)
    assert len(result) == 1
    assert result[0]["unit"] == "Operations Division (OPD), War Department"
    assert result[0]["from"] == "1942"


def test_normalize_rank():
    assert _normalize_rank("Gen.") == "General"
    assert _normalize_rank("Lt. Gen.") == "Lieutenant General"
    assert _normalize_rank("General") == "General"


def test_normalize_branch():
    assert _normalize_branch("US Army") == "U.S. Army"
    assert _normalize_branch("U.S Army") == "U.S. Army"
    assert _normalize_branch("U.S. Army") == "U.S. Army"


def test_normalize_name():
    assert _normalize_name("John Smith") == "john smith"
    assert _normalize_name("  John Smith  ") == "john smith"
    assert _normalize_name("JOHN SMITH") == "john smith"

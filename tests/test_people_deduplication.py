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


def test_v25_nationality_served_and_primary_group_survive_merge():
    """v2.5: nationality_served + primary_group_id are optional and survive the
    gap-fill person merge."""
    from src.extraction.people import BiographicalProfile, _update_missing_fields

    # optional at schema level (old records omit them)
    bp = BiographicalProfile(nationality="FRA")
    assert bp.nationality_served is None and bp.primary_group_id is None

    # settable
    bp2 = BiographicalProfile(
        nationality="FRA",
        nationality_served="DEU",
        primary_group_id="01M3G34VAE8Q198HA79T21C1KZ",
    )
    assert bp2.nationality_served == "DEU"
    assert bp2.primary_group_id == "01M3G34VAE8Q198HA79T21C1KZ"

    # gap-fill merge preserves them when existing lacks them
    existing = {"nationality": "FRA"}
    _update_missing_fields(
        existing,
        {"nationality_served": "DEU", "primary_group_id": "01ABCDEF"},
    )
    assert existing["nationality_served"] == "DEU"
    assert existing["primary_group_id"] == "01ABCDEF"

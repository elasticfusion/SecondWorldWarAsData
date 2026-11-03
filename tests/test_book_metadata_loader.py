"""DRY P1.5 guard: the 5-way book-metadata loader (people, people_groups, places, dates,
weather_central) is now ONE shared loader (_entity_common.load_book_metadata) with ONE safe
isinstance-dict guard. Previously the guards diverged — places/dates had NO non-dict guard and would
crash on a parsed file that is a list/scalar; people used a falsy guard; only weather used
isinstance. These tests pin the consistent safe behavior across every entity path.
"""

import json
from pathlib import Path

import pytest

from src.extraction._entity_common import load_book_metadata, parsed_sibling


def _write(p: Path, obj):
    p.write_text(json.dumps(obj), encoding="utf-8")
    return p


def test_load_book_metadata_dict(tmp_path):
    p = _write(
        tmp_path / "c-parsed.json",
        {"book": "Breakout", "author": "Blumenson", "series": "Green"},
    )
    assert load_book_metadata(p) == ("Breakout", "Blumenson", "Green")


def test_load_book_metadata_missing_or_none(tmp_path):
    assert load_book_metadata(None) == ("", "", "")
    assert load_book_metadata(tmp_path / "nope-parsed.json") == ("", "", "")


def test_load_book_metadata_non_dict_is_safe(tmp_path):
    # A parsed file that is a LIST (or scalar/null) must NOT crash — the safe isinstance guard.
    # On origin/main places._load_book_metadata / dates._load_book_metadata_for_dates would raise
    # AttributeError here (no guard); the shared loader returns empty strings.
    assert load_book_metadata(_write(tmp_path / "l-parsed.json", [1, 2, 3])) == (
        "",
        "",
        "",
    )
    assert load_book_metadata(_write(tmp_path / "n-parsed.json", None)) == ("", "", "")


def test_parsed_sibling_derivation(tmp_path):
    ev = tmp_path / "chapter1-event.json"
    assert parsed_sibling(ev).name == "chapter1-parsed.json"
    assert parsed_sibling(ev).parent == tmp_path


def test_all_entity_loaders_agree(tmp_path):
    # Every per-entity loader must return the SAME (book, author, series) for the same source.
    parsed = _write(
        tmp_path / "ch-parsed.json",
        {"book": "Lorraine", "author": "Cole", "series": "Green"},
    )
    event = tmp_path / "ch-event.json"
    event.write_text("{}", encoding="utf-8")

    from src.extraction.people import _load_book_metadata as people_loader
    from src.extraction.people_groups import (
        _load_book_metadata_for_groups as groups_loader,
    )
    from src.extraction.places import _load_book_metadata as places_loader
    from src.extraction.dates import _load_book_metadata_for_dates as dates_loader
    from src.extraction.weather_central import (
        _load_book_metadata_for_weather as weather_loader,
    )

    expected = ("Lorraine", "Cole", "Green")
    # event_file-based loaders derive the -parsed.json sibling:
    assert people_loader(event) == expected
    assert groups_loader(event) == expected
    # parsed_file-based loaders take it directly:
    assert places_loader(parsed) == expected
    assert dates_loader(parsed) == expected
    assert weather_loader(parsed) == expected


def test_non_dict_does_not_crash_any_loader(tmp_path):
    # The previously-divergent crash case: all parsed_file loaders must tolerate a non-dict.
    bad = _write(tmp_path / "b-parsed.json", ["not", "a", "dict"])
    from src.extraction.places import _load_book_metadata as places_loader
    from src.extraction.dates import _load_book_metadata_for_dates as dates_loader

    assert places_loader(bad) == ("", "", "")  # would AttributeError on origin/main
    assert dates_loader(bad) == ("", "", "")  # would AttributeError on origin/main

"""Task 1.8: public find-or-create entry points for places, dates and people groups, shared by
Phase 2 and Phase 3 - one writer per entity, so a record is the same whoever creates it, and two
writers (in two processes) converge on one record through the entity's locked index.json.
"""

import json
import os

import pytest

os.environ.setdefault("S3_BUCKET", "test-bucket")

from src.extraction import entity_writers as ew  # noqa: E402


def _index(d):
    return json.loads((d / "index.json").read_text())


def test_a_place_is_created_once_and_found_again(tmp_path):
    places = tmp_path / "places"
    first = ew.find_or_create_place(
        "Bastogne", places, {"geography_type": "town", "source": "map-vision"}
    )
    again = ew.find_or_create_place("bastogne", places)
    assert first == again and first.exists()
    rec = json.loads(first.read_text())
    assert rec["PlaceID"] and _index(places)["bastogne"] == first.name


def test_a_place_another_writer_created_is_found(tmp_path):
    """The on-disk index is the authority: a record made by another process is reused."""
    places = tmp_path / "places"
    other = ew.find_or_create_place("Houffalize", places)
    from src.extraction.places import _find_or_create_place

    assert _find_or_create_place("Houffalize", {}, places, {}) == other


@pytest.mark.parametrize("name", ["", "   ", "x" * (ew.MAX_NAME + 1)])
def test_an_unusable_name_is_refused(tmp_path, name):
    with pytest.raises(ValueError):
        ew.find_or_create_place(name, tmp_path / "places")
    with pytest.raises(ValueError):
        ew.find_or_create_group({"group_name": name}, tmp_path / "people_groups")


def test_a_date_is_created_once_under_the_index_lock(tmp_path):
    dates = tmp_path / "dates"
    m = {"date_start": "1944-12-16", "original_text": "16 December 1944"}
    first = ew.find_or_create_date(m, dates)
    again = ew.find_or_create_date({"date_start": "1944-12-16"}, dates)
    assert first == again
    assert first.name in _index(dates).values()
    timed = ew.find_or_create_date(
        {"date_start": "1944-12-16", "time_start": "05:30"}, dates
    )
    assert timed != first  # a time makes it another date record


def test_a_stale_date_index_entry_is_recreated(tmp_path):
    dates = tmp_path / "dates"
    first = ew.find_or_create_date({"date_start": "1944-12-17"}, dates)
    first.unlink()
    again = ew.find_or_create_date({"date_start": "1944-12-17"}, dates)
    assert again.exists()


def test_a_date_needs_a_start(tmp_path):
    with pytest.raises(ValueError):
        ew.find_or_create_date({"original_text": "that winter"}, tmp_path / "dates")


def test_a_group_is_created_then_merged_into(tmp_path):
    groups = tmp_path / "people_groups"
    first = ew.find_or_create_group(
        {"group_name": "101st Airborne Division", "event_mentions": []},
        groups,
        book="B",
    )
    again = ew.find_or_create_group(
        {"group_name": "101st Airborne Division", "event_mentions": []},
        groups,
        book="B",
    )
    assert first is not None and first == again
    assert len([f for f in groups.glob("*.json") if f.name != "index.json"]) == 1


def test_the_photo_place_step_uses_the_shared_writer(tmp_path, monkeypatch):
    from src.extraction import image_place_link as ipl

    calls = []
    real = ew.find_or_create_place
    monkeypatch.setattr(
        ew, "find_or_create_place", lambda *a, **k: calls.append(a) or real(*a, **k)
    )
    index: dict = {}
    pf = ipl._resolve_or_create_place("St. Vith", tmp_path / "places", {}, index)
    assert (
        pf is not None and calls and calls[0][2]["source"] == "document:image-caption"
    )
    assert index["st. vith"] == pf.name  # known for the next photo in the run


@pytest.fixture(autouse=True)
def _fresh_counts(monkeypatch):
    ew.reset_creation_counts()
    # a new place triggers the Phase 2 Grokipedia/Wikipedia lookup: never live in a test
    import src.extraction.enrich_places as ep

    monkeypatch.setattr(ep, "enrich_place_from_grokipedia", lambda p: None)
    monkeypatch.setattr(ep, "enrich_place_from_wikipedia", lambda p: None)
    yield
    ew.reset_creation_counts()


@pytest.mark.parametrize(
    "name", ["_meta", "_schema_version", "a\x00b", "line\nbreak", 12, None, "é" * 101]
)
def test_names_that_are_not_names_are_refused(tmp_path, name):
    with pytest.raises(ValueError):
        ew.find_or_create_place(name, tmp_path / "places")


def test_two_places_named_alike_in_one_second_get_their_own_files(tmp_path):
    """The id in a filename was the ULID's timestamp head: two same-second places that sanitise
    alike shared a file and the second overwrote the first."""
    places = tmp_path / "places"
    a = ew.find_or_create_place("St Vith", places)
    b = ew.find_or_create_place("St. Vith!", places)
    assert a != b and a.exists() and b.exists()
    assert json.loads(a.read_text())["PlaceID"] != json.loads(b.read_text())["PlaceID"]


def test_two_groups_named_alike_get_their_own_files(tmp_path):
    groups = tmp_path / "people_groups"
    a = ew.find_or_create_group({"group_name": "Combat Command A"}, groups)
    b = ew.find_or_create_group({"group_name": "Combat Command A!"}, groups)
    assert a and b and a != b and a.exists() and b.exists()


def test_a_long_non_latin_group_name_does_not_break_the_filename(tmp_path):
    groups = tmp_path / "people_groups"
    path = ew.find_or_create_group({"group_name": "部隊" * 30}, groups)
    assert path is not None and len(path.name.encode("utf-8")) < 255


@pytest.mark.parametrize(
    "mention",
    [{"date_start": 1944}, {"date_start": "../x"}, {"date_start": "x" * 50},
     {"date_start": "1944-12-16", "time_start": 5}, "1944-12-16"],
)  # fmt: skip
def test_dates_that_are_not_dates_are_refused(tmp_path, mention):
    with pytest.raises(ValueError):
        ew.find_or_create_date(mention, tmp_path / "dates")


def test_the_callers_group_is_not_changed(tmp_path):
    group = {
        "group_name": "9th Armored Division",
        "event_mentions": [{"EventID": "e1"}],
    }
    ew.find_or_create_group(group, tmp_path / "people_groups", book="Utah")
    assert group["event_mentions"] == [
        {"EventID": "e1"}
    ]  # no book stamped on the caller's


def test_a_flood_of_new_records_is_stopped_loudly(tmp_path, monkeypatch):
    monkeypatch.setattr(ew, "MAX_CREATED_PER_RUN", 3)
    sent = []
    import src.utils.observe as obs

    monkeypatch.setattr(obs, "observe_error", lambda s, e, **k: sent.append(e))
    places = tmp_path / "places"
    for n in range(3):
        ew.find_or_create_place(f"Town {n}", places)
    ew.find_or_create_place("Town 0", places)  # an existing place is not a creation
    with pytest.raises(ValueError):
        ew.find_or_create_place("Town 9", places)
    assert sent == ["creation_cap"]


def test_a_refused_place_record_is_not_indexed(tmp_path, monkeypatch):
    import src.extraction.places as pl

    monkeypatch.setattr(pl, "write_json_with_lock", lambda *a, **k: False)
    places = tmp_path / "places"
    with pytest.raises(RuntimeError):
        ew.find_or_create_place("Clervaux", places)
    assert "clervaux" not in (
        json.loads((places / "index.json").read_text())
        if (places / "index.json").exists()
        else {}
    )


def test_only_safe_mention_fields_reach_a_new_place(tmp_path):
    pf = ew.find_or_create_place(
        "Wiltz",
        tmp_path / "places",
        {"geography_type": "town", "latitude": 49.97, "longitude": 5.93,
         "map_urls": ["javascript:alert(1)"], "PlaceMentionID": "x"},
    )  # fmt: skip
    rec = json.loads(pf.read_text())
    assert rec["geography_type"] == "town"
    assert rec["coordinates"]["latitude"] == 49.97
    assert "javascript" not in pf.read_text()


def test_coordinates_out_of_range_are_dropped(tmp_path):
    pf = ew.find_or_create_place(
        "Diekirch", tmp_path / "places", {"latitude": 999, "longitude": "5.9"}
    )
    assert "coordinates" not in json.loads(pf.read_text())

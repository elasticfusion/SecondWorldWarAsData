"""Reverse map registration: narrative entities link to a map whose extent covers their
(PlaceID, DateID). The join is the entity graph, not map OCR."""

from src.extraction.map_registration import (
    _in_date_range,
    register_entity_to_maps,
)

ROTH = "01PLACEROTH0000000000000AA"
STVITH = "01PLACESTVITH00000000000BB"
OFFMAP = "01PLACEOFFMAP0000000000CC"
MAP_III = [
    {
        "MapID": "01MAPIII000000000000000000",
        "covered_places": {ROTH, STVITH},
        "earliest": "1944-12-15",
        "latest": "1944-12-19",
    }
]
D2I = {
    "01D16DEC000000000000000000": "1944-12-16",
    "01D22DEC000000000000000000": "1944-12-22",
}


def _entity(pid, did):
    return {
        "PersonID": "01P000000000000000000000AA",
        "event_mentions": [{"PlaceID": pid, "DateID": did}],
    }


def test_sgt_smith_registers_to_map():
    # "Sgt Smith, DSC, St. Vith, 16 Dec 1944" -> Map III (covers St.Vith, 15-19 Dec)
    links = register_entity_to_maps(
        _entity(STVITH, "01D16DEC000000000000000000"), MAP_III, D2I
    )
    assert len(links) == 1
    assert links[0]["MapID"] == "01MAPIII000000000000000000"
    assert links[0]["association"] == "spatial_temporal_coverage"
    assert links[0]["PlaceID"] == STVITH and links[0]["date"] == "1944-12-16"


def test_date_out_of_range_no_link():
    assert (
        register_entity_to_maps(
            _entity(STVITH, "01D22DEC000000000000000000"), MAP_III, D2I
        )
        == []
    )


def test_place_off_map_no_link():
    assert (
        register_entity_to_maps(
            _entity(OFFMAP, "01D16DEC000000000000000000"), MAP_III, D2I
        )
        == []
    )


def test_place_only_map_matches_without_date():
    # a map with no date_range is place-only coverage: place membership alone qualifies
    place_only = [
        {
            "MapID": "01MAPX0000000000000000000A",
            "covered_places": {ROTH},
            "earliest": None,
            "latest": None,
        }
    ]
    links = register_entity_to_maps(
        {"event_mentions": [{"PlaceID": ROTH}]}, place_only, {}
    )
    assert len(links) == 1


def test_date_range_bounds():
    assert _in_date_range("1944-12-15", "1944-12-15", "1944-12-19")
    assert _in_date_range("1944-12-19", "1944-12-15", "1944-12-19")
    assert not _in_date_range("1944-12-14", "1944-12-15", "1944-12-19")
    assert not _in_date_range("1944-12-20", "1944-12-15", "1944-12-19")
    assert _in_date_range(None, None, None)  # place-only map
    assert not _in_date_range(
        None, "1944-12-15", "1944-12-19"
    )  # dated map needs a date

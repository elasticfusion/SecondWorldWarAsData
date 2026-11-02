"""Step 2: shared Map-record skeleton (map_common) consumed by all 3 source builders — removes the
~18-field copy-paste. Guards the common contract + that each builder still emits its own fields.
"""

from src.extraction.map_common import base_map_record, external_map_fields

_COMMON = {
    "MapID",
    "map_title",
    "description",
    "map_type",
    "source_url",
    "local_path",
    "local_image_path",
    "file_format",
    "storage_backend",
    "EventID",
    "Event_Name",
    "Sub_eventID",
    "Sub_event_Name",
    "place_name",
    "PlaceMentionID",
    "date",
    "DateMentionID",
    "PeopleGroupID",
    "PeopleGroupMentionID",
    "PeopleGroup_Name",
    "extracted_date",
}
_EXTERNAL = {
    "external_source",
    "external_source_url",
    "archive_id",
    "license",
    "license_url",
    "date_created",
    "creator",
    "found_via",
    "found_date",
}


def test_base_map_record_common_fields():
    r = base_map_record(
        map_id="01X",
        map_title="t",
        description="d",
        map_type="operational",
        source_url="http://u",
        local_path="output/maps/01X.json",
        local_image_path=None,
        file_format=None,
        storage_backend="filesystem",
    )
    assert set(r) == _COMMON
    assert r["MapID"] == "01X" and r["extracted_date"].endswith("Z")


def test_external_map_fields():
    r = external_map_fields(
        external_source="NARA", external_source_url="http://n", license_="PD"
    )
    assert set(r) == _EXTERNAL
    assert r["license"] == "PD" and r["external_source"] == "NARA"


def test_source_document_builder_keys():
    from src.extraction.maps import _create_map_record

    r = _create_map_record(
        "01X",
        "d",
        "Book",
        "Auth",
        "ETO",
        "Map 1",
        "http://u/m.jpg",
        None,
        "jpg",
        "filesystem",
        {},
    )
    assert _COMMON <= set(r)
    assert {
        "source_book",
        "source_author",
        "source_series",
        "page_number",
        "figure_number",
    } <= set(r)


def test_external_builder_keys():
    from src.extraction.external_maps import create_map_record

    r = create_map_record(
        {
            "title": "t",
            "external_source": "NARA",
            "external_source_url": "u",
            "license": "PD",
            "file_url": "http://f/m.jpg",
            "place_keywords": ["Caen"],
        },
        ("E", "N", "S", "SN"),
        None,
        None,
        "filesystem",
    )
    assert _COMMON <= set(r) and _EXTERNAL <= set(r)

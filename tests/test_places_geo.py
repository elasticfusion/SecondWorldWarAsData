"""Tests for offline place geo-enrichment (derived fields, outliers, work-queue)."""

import json
from pathlib import Path

from src.enrichment.places_geo import (
    enrich_place,
    enrich_places_dir,
    is_geocodable_name,
)


def test_derived_fields_backfilled_from_coordinates() -> None:
    place = {
        "current_name": "Metz",
        "coordinates": {"latitude": 49.12, "longitude": 6.18},
    }
    updated, changes = enrich_place(place)
    assert "derived" in changes
    assert updated["bounding_box"]["north"] == round(49.12 + 0.9, 4)
    assert "google_maps" in updated["map_urls"]
    assert "openstreetmap" in updated["map_urls"]


def test_existing_derived_fields_not_overwritten() -> None:
    place = {
        "current_name": "Metz",
        "coordinates": {"latitude": 49.12, "longitude": 6.18},
        "bounding_box": {"north": 1, "south": 2, "east": 3, "west": 4},
        "map_urls": {"google_maps": "x", "openstreetmap": "y"},
    }
    updated, changes = enrich_place(place)
    assert "derived" not in changes
    assert updated["bounding_box"]["north"] == 1  # untouched


def test_in_theater_coordinate_not_flagged() -> None:
    place = {
        "current_name": "Aachen",
        "coordinates": {"latitude": 50.78, "longitude": 6.08},
    }
    updated, changes = enrich_place(place)
    assert "outlier" not in changes
    assert "geo_review" not in updated


def test_out_of_theater_coordinate_flagged_not_moved() -> None:
    place = {
        "current_name": "Adak Island",
        "coordinates": {"latitude": 51.88, "longitude": -176.6},
    }
    updated, changes = enrich_place(place)
    assert "outlier" in changes
    assert "geo_review" in updated
    # Coordinates are never moved — verification, not correction.
    assert updated["coordinates"]["longitude"] == -176.6


def test_missing_coordinates_no_change() -> None:
    place = {
        "current_name": "Hill 401",
        "coordinates": {"latitude": 0.0, "longitude": 0.0},
    }
    updated, changes = enrich_place(place)
    assert changes == []


def test_is_geocodable_name_filters_unit_areas() -> None:
    assert is_geocodable_name("Aachen") is True
    assert is_geocodable_name("Aa River") is True
    assert is_geocodable_name("26th Infantry Division sector") is False
    assert is_geocodable_name("XII Corps area") is False
    assert is_geocodable_name("6th Parachute Regiment") is False


def test_enrich_dir_reports_and_writes(tmp_path: Path) -> None:
    places = tmp_path / "places"
    places.mkdir()
    # geocoded, missing derived fields
    (places / "metz.json").write_text(
        json.dumps(
            {
                "PlaceID": "01A",
                "current_name": "Metz",
                "coordinates": {"latitude": 49.12, "longitude": 6.18},
            }
        ),
        encoding="utf-8",
    )
    # out-of-theater
    (places / "adak.json").write_text(
        json.dumps(
            {
                "PlaceID": "01B",
                "current_name": "Adak Island",
                "coordinates": {"latitude": 51.88, "longitude": -176.6},
            }
        ),
        encoding="utf-8",
    )
    # no coords, real name
    (places / "aachen.json").write_text(
        json.dumps({"PlaceID": "01C", "current_name": "Aachen"}), encoding="utf-8"
    )
    # no coords, unit area
    (places / "sector.json").write_text(
        json.dumps({"PlaceID": "01D", "current_name": "5th Division area"}),
        encoding="utf-8",
    )
    # index.json is skipped
    (places / "index.json").write_text("{}", encoding="utf-8")

    report = enrich_places_dir(places, write=True)
    assert report.scanned == 4
    # Both geocoded places (metz, adak) get derived fields; adak is also flagged.
    assert report.derived_fields_added == 2
    assert report.outliers_flagged == 1  # adak
    assert report.needs_geocoding == ["Aachen"]
    assert report.non_settlement == ["5th Division area"]

    # Write actually persisted the derived fields on metz.
    metz = json.loads((places / "metz.json").read_text())
    assert "bounding_box" in metz and "map_urls" in metz
    adak = json.loads((places / "adak.json").read_text())
    assert "geo_review" in adak

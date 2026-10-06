"""Enforced logistics schema: all extractor-written fields validate; drift fixed."""

import jsonschema
import pytest

from src.schemas.logistics_output import LOGISTICS_OUTPUT_SCHEMA as S

UL = "01ABCDEFGH0123456789ABCDEF"


def _full():
    return {
        "LogisticsID": UL,
        "logistics_type": "supply_shortage",
        "category": "fuel",
        "description": "Fuel shortage halted the advance",
        "severity": "high",
        "status": "unresolved",
        "temporal": {
            "date_start": "1944-09-04",
            "date_end": "1944-09-07",
            "date_type": "range",
            "DateID_start": UL,
            "DateID_end": UL,
        },
        "delivery_method": "ground_transport",
        "quantity": {
            "required": 100.0,
            "available": 40.0,
            "unit": "tons",
            "shortage": 60.0,
        },
        "resolution": {"resolved": True, "resolution_method": "air_delivery"},
        "extracted_date": "2026-10-06T00:00:00+00:00",
        "impacted_organizations": [
            {
                "PeopleGroupID": UL,
                "group_name": "CCA, 3d Armored Division",
                "impact_description": "",
            }
        ],
        "impacted_people": [
            {"PersonID": UL, "name": "Patton", "impact_description": ""}
        ],
        "impacted_places": [
            {"PlaceID": UL, "place_name": "Verdun", "impact_description": ""}
        ],
        "impacted_equipment": [
            {"EquipmentID": UL, "common_name": "M4 Sherman", "impact_description": ""}
        ],
        "weather_impact": {
            "WeatherID": UL,
            "impact_description": "mud",
            "severity": "medium",
        },
        "event_mentions": [
            {
                "EventMentionID": UL,
                "EventID": UL,
                "Sub_eventID": UL,
                "paragraph_numbers": [12, 13],
                "context": "fuel crisis",
            }
        ],
    }


def test_full_record_validates():
    jsonschema.validate(_full(), S)


def test_impacted_places_and_weather_impact_now_enforced():
    # the two fields that were missing from the schema (drift) are now present
    props = S["properties"]
    assert "impacted_places" in props and "weather_impact" in props


def test_additional_properties_rejected():
    bad = _full()
    bad["bogus_field"] = "x"
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(bad, S)


def test_bad_severity_enum_rejected():
    bad = _full()
    bad["severity"] = "catastrophic"
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(bad, S)

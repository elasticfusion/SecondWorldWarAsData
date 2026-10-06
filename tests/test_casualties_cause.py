"""Casualties cause dimension (Option A): weather-caused casualties at both granularities —
individual (Sgt Smith frostbite) + aggregate (45 men trench foot) — with cause +
PersonID/PlaceID anchors + original_text. Schema valid + back-compat."""

import jsonschema
from src.extraction.casualties import _build_casualty
from src.schemas.casualties_output import CASUALTIES_OUTPUT_SCHEMA as S

UL = "01ABCDEFGH0123456789ABCDEF"
PID = "01ABCDEFGH00000000000000AA"
PLID = "01ABCDEFGH00000000000000BB"


def _build(cd):
    return _build_casualty(
        cd,
        "01EV00000000000000000000AB",
        "01SE00000000000000000000AB",
        "Book",
        "Ch1",
        1,
        dates_index={},
        places_index={},
        people_index={},
        people_groups_index={},
    )


def test_individual_frostbite_anchored():
    c = _build(
        {
            "type": "non_battle",
            "cause": "weather_exposure",
            "description": "frostbite",
            "PersonID": PID,
            "PlaceID": PLID,
            "original_text": "Sergeant Smith suffered frostbite at Bastogne",
            "impacted_people": [{"name": "Sergeant Smith", "PersonID": PID}],
        }
    )
    assert c["type"] == "non_battle" and c["cause"] == "weather_exposure"
    assert c["PersonID"] == PID and c["PlaceID"] == PLID
    assert c["original_text"]
    jsonschema.validate(c, S)


def test_aggregate_frostbite_no_person():
    c = _build(
        {
            "type": "non_battle",
            "cause": "weather_exposure",
            "description": "45 men had trench foot and frostbite",
            "count": {"affected": 45},
            "original_text": "Forty-five men were evacuated with trench foot and frostbite",
            "impacted_places": [
                {"name": "Bastogne"}
            ],  # unresolved (no index) -> PlaceID null
        }
    )
    assert c["cause"] == "weather_exposure"
    assert "PersonID" not in c  # aggregate — no individual anchor
    jsonschema.validate(c, S)


def test_anchor_hoisted_from_single_impacted_person():
    from src.extraction.casualties import _build_casualty

    c = _build_casualty(
        {
            "type": "non_battle",
            "cause": "disease",
            "impacted_people": [{"name": "Pvt Jones"}],
        },
        "01EV00000000000000000000AB",
        "01SE00000000000000000000AB",
        "B",
        "C",
        1,
        dates_index={},
        places_index={},
        people_index={"pvt jones": PID},  # resolves -> PID
        people_groups_index={},
    )
    assert c["PersonID"] == PID  # hoisted from the sole RESOLVED impacted person


def test_bad_cause_dropped():
    c = _build({"type": "non_battle", "cause": "bogus"})
    assert "cause" not in c  # invalid cause not stored
    jsonschema.validate(c, S)


def test_back_compat_aggregate_no_cause():
    c = _build({"type": "casualties", "count": {"killed": 13}})
    jsonschema.validate(c, S)  # legacy shape still valid

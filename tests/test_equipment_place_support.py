"""#1 place_name -> single PlaceID resolution; #4 supporting units support_type + EquipmentID."""

from src.extraction.equipment import (
    EquipmentExtraction,
    _build_mention,
    _link_supporting_units,
)


def _event(sub_places):
    return {
        "Event": {
            "EventID": "01EVENT0000000000000000000",
            "Event_Name": "Op",
            "Sub-events": [
                {"Sub-eventID": "01SUB00000000000000000000", "places": sub_places}
            ],
        }
    }


def _eq(**kw):
    base = {
        "common_name": "M4 Sherman",
        "technical_identifier": "M4",
        "category": "armor",
    }
    base.update(kw)
    return EquipmentExtraction.model_validate(base)


def test_place_name_resolves_to_single_placeid():
    # Mention names its OWN place -> resolves via the places index to ONE PlaceID,
    # regardless of how many places the sub-event has.
    eq = _eq(place_name="the crossroads in Cherbourg")
    idx = {"the crossroads in cherbourg": "01PLACECHERBOURG0000000000"}
    m = _build_mention(
        eq,
        _event(["01PLACE_A", "01PLACE_B"]),
        None,
        None,
        None,
        [],
        {},
        __import__("pathlib").Path("."),
        idx,
    )
    assert m["PlaceID"] == "01PLACECHERBOURG0000000000"
    assert m["place_name"] == "the crossroads in Cherbourg"


def test_place_falls_back_to_single_subevent_place_when_no_name():
    eq = _eq()  # no place_name stated
    m = _build_mention(
        eq,
        _event(["01ONLYPLACE000000000000000"]),
        None,
        None,
        None,
        [],
        {},
        __import__("pathlib").Path("."),
        {},
    )
    assert m["PlaceID"] == "01ONLYPLACE000000000000000"


def test_no_placeid_when_name_unresolved_and_multiple_subevent_places():
    eq = _eq(place_name="Unknown Hamlet")
    m = _build_mention(
        eq,
        _event(["01PLACE_A", "01PLACE_B"]),
        None,
        None,
        None,
        [],
        {},
        __import__("pathlib").Path("."),
        {},
    )
    assert "PlaceID" not in m  # ambiguous + unresolved -> no wrong guess
    assert m["place_name"] == "Unknown Hamlet"


def test_supporting_unit_type_is_supporting_arm_not_parent():
    eq = _eq(
        supporting_units=[
            {
                "unit_name": "IX TAC",
                "support_type": "aircraft",
                "equipment_name": "P-47",
            }
        ]
    )
    linked = _link_supporting_units(
        eq.supporting_units, {"IX TAC": "01GRP"}, "armor", None
    )
    assert linked[0]["support_type"] == "aircraft"  # NOT the parent 'armor'
    assert linked[0]["PeopleGroupID"] == "01GRP"


def test_supporting_unit_falls_back_to_parent_category_when_type_absent():
    eq = _eq(supporting_units=[{"unit_name": "Some Bn"}])
    linked = _link_supporting_units(eq.supporting_units, {}, "armor", None)
    assert linked[0]["support_type"] == "armor"  # fallback only when unknown

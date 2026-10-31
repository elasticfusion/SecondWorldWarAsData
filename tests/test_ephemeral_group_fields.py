"""2b: ephemeral mission-scoped forces (naval task forces) must persist lifecycle + active_period
(incl precision=unresolved) + constituent_units, and NOT be flattened into the standing unit.
"""

import jsonschema
from src.schemas.groups_output import GROUPS_OUTPUT_SCHEMA
from src.extraction.people_groups import _coerce_group_schema_keys


def _valid(g):
    g = dict(g)
    g["GroupID"] = "01ARZ3NDEKTSV4RRFFQ69G5FAV"
    jsonschema.validate(g, GROUPS_OUTPUT_SCHEMA)
    return True


def test_ephemeral_force_fields_are_schema_valid_and_survive_coercion():
    g = {
        "group_name": "Force O",
        "group_type": "military_unit",
        "organization_lifecycle": "ephemeral",
        "active_period": {
            "start": None,
            "end": None,
            "precision": "unresolved",
            "original_text": "Force O (1st Division)",
        },
        "constituent_units": ["1st Division"],
    }
    _coerce_group_schema_keys(g)
    assert g["organization_lifecycle"] == "ephemeral"
    assert (
        g["active_period"]["precision"] == "unresolved"
    )  # unknown date kept, not dropped
    assert g["constituent_units"] == ["1st Division"]
    assert _valid(g)


def test_approximate_date_window_valid():
    g = {
        "group_name": "Force U",
        "organization_lifecycle": "ephemeral",
        "active_period": {
            "start": "1944-06-06",
            "end": "D+1",
            "precision": "approximate",
            "original_text": "landed on D-Day",
        },
    }
    _coerce_group_schema_keys(g)
    assert _valid(g)


def test_standing_lifecycle_valid():
    g = {"group_name": "1st Infantry Division", "organization_lifecycle": "standing"}
    assert _valid(g)

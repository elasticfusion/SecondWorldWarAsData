"""BUG 1 fix: a group with an unmodeled top-level field must NOT be silently dropped by
additionalProperties:False — strip the stray key (observably) and persist the valid record.
Permitted fields must survive untouched."""

from src.extraction.people_groups import _coerce_group_schema_keys
from src.schemas.groups_output import GROUPS_OUTPUT_SCHEMA


def test_strips_unknown_top_level_key():
    g = {
        "group_name": "Force O",
        "group_type": "military_unit",
        "delivered_unit": "1st Division",  # NOT in schema -> would cause additionalProperties reject
        "alliance_membership": ["Allied Powers"],
    }
    _coerce_group_schema_keys(g)
    assert "delivered_unit" not in g  # stripped
    assert g["group_name"] == "Force O"  # valid record preserved
    assert g["alliance_membership"] == ["Allied Powers"]


def test_keeps_all_permitted_keys():
    allowed = set(GROUPS_OUTPUT_SCHEMA["properties"].keys())
    g = {
        k: None
        for k in [
            "group_name",
            "group_type",
            "alliance_membership",
            "parent_organization",
            "sub_organizations",
            "aliases",
            "description",
            "military_hierarchy",
        ]
    }
    assert all(k in allowed for k in g)  # these ARE permitted
    before = dict(g)
    _coerce_group_schema_keys(g)
    assert g == before  # nothing stripped


def test_fail_safe_on_bad_input():
    g = {"group_name": "X"}
    _coerce_group_schema_keys(g)  # must not raise
    assert g["group_name"] == "X"


def test_strip_emits_observable_canonical_OBSERV(caplog):
    """The strip MUST emit a loud canonical OBSERV (not be swallowed) — regression lock on the
    observe_error(status=) TypeError-swallow bug found during development."""
    import logging

    with caplog.at_level(logging.WARNING):
        _coerce_group_schema_keys(
            {"group_name": "Force O", "delivered_unit": "1st Division"}
        )
    assert any(
        "people_groups.extra_field_stripped" in r.message and "status=warn" in r.message
        for r in caplog.records
    ), "strip OBSERV did not emit (swallowed?)"

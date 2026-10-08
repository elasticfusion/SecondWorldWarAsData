"""Regression tests for the 3 Phase-2 extractor bugs found by the chapter15e E2E run."""

import json

from src.extraction.logistics_resolver import build_logistics_index


def test_equipment_prompt_fills_event_data():
    # BUG1: render_prompt passed text= but the template expects {event_data}.
    from src.utils.prompt_loader import render_prompt

    p = render_prompt("equipment", event_data=json.dumps({"Event": {"EventID": "x"}}))
    assert "{event_data}" not in p
    assert "EventID" in p


def test_logistics_index_handles_list_shaped_file(tmp_path):
    # BUG2: a logistics file whose top level is a LIST crashed build_logistics_index
    # with "'list' object has no attribute 'get'".
    (tmp_path / "list.json").write_text(
        json.dumps(
            [
                {
                    "LogisticsID": "01HX7YZABCDEFGHJKMNPQRSTVW",
                    "logistics_type": "supply",
                    "impacted_organizations": [
                        {"PeopleGroupID": "01HX7YZABCDEFGHJKMNPQRSTVX"}
                    ],
                },
                "a-non-dict-item",  # must be skipped, not crash
            ]
        )
    )
    (tmp_path / "dict.json").write_text(
        json.dumps(
            {"LogisticsID": "01HX7YZABCDEFGHJKMNPQRSTVY", "logistics_type": "fuel"}
        )
    )
    idx = build_logistics_index(tmp_path)
    assert len(idx["by_id"]) == 2  # both list-record and dict-record indexed
    assert "01HX7YZABCDEFGHJKMNPQRSTVX" in idx["by_group"]


def test_supplemental_defaults_yield_valid_ulids():
    # BUG3: empty-string ID defaults ("") failed the ULID pattern before generate_ulids,
    # which only replaces the GENERATE_NEW_ULID sentinel.
    import re

    from src.extraction.supplemental import _apply_defaults, generate_ulids

    material: dict = {}
    _apply_defaults(material)
    resolved = generate_ulids(material)
    pat = r"^[0-9A-HJKMNP-TV-Z]{26}$"
    for key in ("MaterialID", "EventID", "Sub-eventID"):
        assert re.match(
            pat, resolved[key]
        ), f"{key} not a valid ULID: {resolved[key]!r}"

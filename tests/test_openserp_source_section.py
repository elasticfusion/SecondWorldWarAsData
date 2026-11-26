"""Tests for the source_section (events) OpenSERP enrichment, keyed on the operation label."""

import json
from unittest.mock import patch

import jsonschema

import src.enrichment.openserp_enrichment as oe
from src.schemas.source_section_output import SOURCE_SECTION_OUTPUT_SCHEMA


def _write(ss_dir, name, obj):
    (ss_dir / name).write_text(json.dumps(obj), encoding="utf-8")


def test_searches_only_sections_with_operation(tmp_path):
    ss = tmp_path / "source_section"
    ss.mkdir()
    _write(
        ss,
        "a.json",
        {
            "SourceSectionID": "01HX7YZABCDEFGHJKMNPQRSTVW",
            "EventID": "01HX7YZABCDEFGHJKMNPQRSTVX",
            "operation": {"name": "Battle of the Bulge", "aliases": ["Ardennes"]},
        },
    )
    _write(
        ss,
        "b.json",
        {"SourceSectionID": "01HX7YZABCDEFGHJKMNPQRSTVY", "operation": None},
    )
    with (
        patch.object(oe, "_openserp_reachable", return_value=True),
        patch.object(
            oe,
            "search_event_content",
            return_value=[{"url": "http://s1", "title": "AAR", "source": "openserp"}],
        ) as mock_search,
    ):
        n = oe.enrich_source_sections_with_openserp(ss.parent, "http://x")
    assert n == 1
    # searched the operation name, NOT a granular event name
    assert mock_search.call_args.args[0] == "Battle of the Bulge"
    a = json.loads((ss / "a.json").read_text())
    b = json.loads((ss / "b.json").read_text())
    assert (
        "primary_sources" not in a and a["openserp_searched"] is True
    )  # held in the queue
    assert (
        mock_search.call_args.kwargs["origin"].entity_id == "01HX7YZABCDEFGHJKMNPQRSTVW"
    )
    assert b.get("primary_sources") is None  # null-over-fake: no operation -> untouched
    jsonschema.validate(a, SOURCE_SECTION_OUTPUT_SCHEMA)


def test_a_source_section_page_is_queued_once_and_never_written_to_the_record(
    tmp_path, queued_finds
):
    ss = tmp_path / "source_section"
    ss.mkdir()
    _write(
        ss,
        "a.json",
        {
            "SourceSectionID": "01HX7YZABCDEFGHJKMNPQRSTVW",
            "operation": {"name": "Battle of Metz"},
            "primary_sources": [{"url": "http://old", "source": "openserp"}],
        },
    )
    page = {
        "url": "http://example.org/new",
        "title": "y",
        "summary": "s",
        "source": "openserp",
    }
    with (
        patch.object(oe, "_openserp_reachable", return_value=True),
        patch.object(
            oe,
            "_search_openserp",
            return_value=[
                {"url": "http://example.org/new", "title": "y", "description": "d"}
            ],
        ),
        patch.object(oe, "process_positive_url", return_value=page),
    ):
        data = json.loads((ss / "a.json").read_text())
        assert oe.enrich_one_source_section(data, "http://x", object()) is True
        assert oe.enrich_one_source_section(data, "http://x", object()) is False
    a = json.loads((ss / "a.json").read_text())
    assert a["primary_sources"] == [{"url": "http://old", "source": "openserp"}]
    (event,) = queued_finds()
    assert (
        event["endpoint"] == "http://example.org/new"
        and event["review_status"] == "pending"
    )
    p = event["request_params"]
    assert p["origin_entity_type"] == "source_section" and p["summary"] == "s"
    assert p["origin_entity_id"] == "01HX7YZABCDEFGHJKMNPQRSTVW"

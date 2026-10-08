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
        n = oe.enrich_source_sections_with_openserp(ss, "http://x")
    assert n == 1
    # searched the operation name, NOT a granular event name
    assert mock_search.call_args.args[0] == "Battle of the Bulge"
    a = json.loads((ss / "a.json").read_text())
    b = json.loads((ss / "b.json").read_text())
    assert len(a["primary_sources"]) == 1 and a["openserp_searched"] is True
    assert b.get("primary_sources") is None  # null-over-fake: no operation -> untouched
    jsonschema.validate(a, SOURCE_SECTION_OUTPUT_SCHEMA)


def test_dedupes_primary_sources(tmp_path):
    ss = tmp_path / "source_section"
    ss.mkdir()
    _write(
        ss,
        "a.json",
        {
            "SourceSectionID": "01HX7YZABCDEFGHJKMNPQRSTVW",
            "operation": {"name": "Battle of Metz"},
            "primary_sources": [{"url": "http://dup", "source": "openserp"}],
        },
    )
    with (
        patch.object(oe, "_openserp_reachable", return_value=True),
        patch.object(
            oe,
            "search_event_content",
            return_value=[
                {"url": "http://dup", "title": "x", "source": "openserp"},
                {"url": "http://new", "title": "y", "source": "openserp"},
            ],
        ),
    ):
        oe.enrich_source_sections_with_openserp(ss, "http://x")
    a = json.loads((ss / "a.json").read_text())
    urls = [s["url"] for s in a["primary_sources"]]
    assert urls.count("http://dup") == 1 and "http://new" in urls

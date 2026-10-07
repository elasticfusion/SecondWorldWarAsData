"""Tests for the source_section producer (coarse-grained per-section anchor)."""

import json

import jsonschema

from src.extraction.source_section import (
    build_source_section,
    emit_source_section,
)
from src.schemas.source_section_output import SOURCE_SECTION_OUTPUT_SCHEMA

_EVENT = {
    "Chapter": "The Ardennes Counteroffensive",
    "Event": {
        "EventID": "01HX7YZABCDEFGHJKMNPQRSTVW",
        "Event_Name": "Battle in the Ardennes",
        "Sub-events": [
            {"Sub-eventID": "01HX7YZABCDEFGHJKMNPQRSTVX", "Sub-event_summary": "x"}
        ],
    },
}


def test_build_links_event_and_sets_title():
    rec = build_source_section(_EVENT, source={"book": "Ardennes", "author": "Cole"})
    assert rec is not None
    assert rec["EventID"] == "01HX7YZABCDEFGHJKMNPQRSTVW"
    assert rec["section_title"] == "The Ardennes Counteroffensive"
    # summary + operation + enrichment are null here (LLM pass is a later increment).
    assert rec["section_summary"] is None
    assert rec["operation"] is None
    assert rec["reference_articles"] is None


def test_chapter_as_object_title_extracted():
    rec = build_source_section(
        {
            "Chapter": {"title": "West Wall"},
            "Event": {"EventID": "01HX7YZABCDEFGHJKMNPQRSTVW"},
        }
    )
    assert rec["section_title"] == "West Wall"


def test_missing_event_id_returns_none():
    assert build_source_section({"Chapter": "x", "Event": {}}) is None
    assert build_source_section({"Chapter": "x"}) is None


def test_emit_saves_and_validates_against_enforced_schema(tmp_path):
    path = emit_source_section(_EVENT, tmp_path, source={"book": "Ardennes"})
    assert path is not None and path.exists()
    saved = json.loads(path.read_text())
    jsonschema.validate(saved, SOURCE_SECTION_OUTPUT_SCHEMA)
    # native version-stamped commit
    assert saved["_schema_version"]
    assert saved["SourceSectionID"]
    assert saved["EventID"] == "01HX7YZABCDEFGHJKMNPQRSTVW"


def test_emit_missing_event_id_is_noop(tmp_path):
    assert emit_source_section({"Chapter": "x", "Event": {}}, tmp_path) is None
    assert not (tmp_path / "source_section").exists() or not list(
        (tmp_path / "source_section").glob("*.json")
    )

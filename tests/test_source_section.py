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


# --- Step 3: summary + operation derivation ---------------------------------------------

from src.extraction.source_section import (  # noqa: E402
    _normalize_operation,
    _truncate_to_two_sentences,
    derive_summary_and_operation,
    gather_section_signal,
)


class _MockGrok:
    def __init__(self, result):
        self._result = result
        self.calls = 0

    def extract_json(self, prompt, system_prompt=None, cache_type="default"):
        self.calls += 1
        return self._result


def test_null_over_fake_operation():
    # below threshold, or no name -> None
    assert _normalize_operation({"name": "X", "confidence": 0.4}, 0.6) is None
    assert _normalize_operation({"confidence": 0.9}, 0.6) is None
    assert _normalize_operation(None, 0.6) is None
    ok = _normalize_operation({"name": "Battle of the Bulge", "confidence": 0.9}, 0.6)
    assert ok["name"] == "Battle of the Bulge" and ok["source"] == "derived"


def test_truncate_to_two_sentences():
    assert _truncate_to_two_sentences("One. Two. Three.") == "One. Two."
    assert _truncate_to_two_sentences(None) is None


def test_gather_signal_uses_subevents_not_raw_text():
    sig = gather_section_signal(_EVENT)
    assert sig["section_title"] == "The Ardennes Counteroffensive"
    assert sig["sub_event_summaries"] == ["x"]


def test_derive_sets_summary_and_operation_and_is_gated():
    rec = build_source_section(_EVENT, source={"book": "b"})
    grok = _MockGrok(
        {
            "section_summary": "S1. S2. S3.",
            "operation": {
                "name": "Battle of the Bulge",
                "confidence": 0.9,
                "wikipedia_title": "Battle of the Bulge",
            },
        }
    )
    assert derive_summary_and_operation(rec, _EVENT, grok, 0.6) is True
    assert rec["section_summary"] == "S1. S2."  # 2-sentence cap
    assert rec["operation"]["name"] == "Battle of the Bulge"
    # gated: already-summarized record is a no-op (no second LLM call)
    assert derive_summary_and_operation(rec, _EVENT, grok, 0.6) is False
    assert grok.calls == 1


def test_derive_low_confidence_yields_null_operation():
    rec = build_source_section(_EVENT, source={"book": "b"})
    grok = _MockGrok(
        {"section_summary": "x.", "operation": {"name": "Weak", "confidence": 0.3}}
    )
    assert derive_summary_and_operation(rec, _EVENT, grok, 0.6) is True
    assert rec["operation"] is None

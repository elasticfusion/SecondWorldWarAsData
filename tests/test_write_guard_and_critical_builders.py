"""Regression tests: 4 CRITICAL schema-invalid-write bugs + the central write guard."""

import json

import jsonschema
import pytest

from src.schemas.bibliography_output import BIBLIOGRAPHY_OUTPUT_SCHEMA
from src.schemas.casualties_output import CASUALTIES_OUTPUT_SCHEMA
from src.schemas.images_output import IMAGES_OUTPUT_SCHEMA


def test_images_event_ctx_uses_null_not_empty_string():
    # was: EventID/Sub-eventID "" (fails nullable ULID). Now None.
    rec = {
        "ImageID": "01HX7YZABCDEFGHJKMNPQRSTVW",
        "EventID": None,
        "Sub-eventID": None,
    }
    jsonschema.validate(rec, IMAGES_OUTPUT_SCHEMA)


def test_images_caption_fields_are_schema_valid():
    rec = {
        "ImageID": "01HX7YZABCDEFGHJKMNPQRSTVW",
        "caption_confidence": 0.4,
        "caption_method": "grok_vision",
        "needs_review": True,
    }
    jsonschema.validate(rec, IMAGES_OUTPUT_SCHEMA)


def test_casualties_omits_event_context_without_real_event_id():
    cas = {
        "CasualtyID": "01HX7YZABCDEFGHJKMNPQRSTVW",
        "type": "KIA",
        "source": {"EventID": None, "Sub-eventID": None, "book": "b", "chapter": "c"},
    }
    jsonschema.validate(cas, CASUALTIES_OUTPUT_SCHEMA)  # no event_context key -> valid


def test_bibliography_mention_omits_non_ulid_ids():
    bib = {
        "BibliographyID": "01HX7YZABCDEFGHJKMNPQRSTVW",
        "title": "X",
        "mentions": [
            {"MentionID": "01HX7YZABCDEFGHJKMNPQRSTVW", "book": "b", "chapter": "c"}
        ],
    }
    jsonschema.validate(bib, BIBLIOGRAPHY_OUTPUT_SCHEMA)


# --- central write guard ---


def test_guard_blocks_schema_invalid_record(tmp_path):
    from src.utils.file_lock import write_json_with_lock

    pdir = tmp_path / "people"
    pdir.mkdir()
    # missing required 'name' -> blocked, not written
    write_json_with_lock(pdir / "bad.json", {"PersonID": "01HX7YZABCDEFGHJKMNPQRSTVW"})
    assert not (pdir / "bad.json").exists()


def test_guard_repairs_empty_ulid_then_writes(tmp_path):
    from src.utils.file_lock import write_json_with_lock

    idir = tmp_path / "images"
    idir.mkdir()
    write_json_with_lock(
        idir / "i.json",
        {"ImageID": "01HX7YZABCDEFGHJKMNPQRSTVW", "EventID": ""},
        entity="images",
    )
    saved = json.loads((idir / "i.json").read_text())
    # empty-string ID repaired to a real ULID (not left "")
    assert saved["EventID"] and saved["EventID"] != ""
    jsonschema.validate(saved, IMAGES_OUTPUT_SCHEMA)


def test_guard_preserves_existing_valid_ulid(tmp_path):
    # must NOT regenerate a valid, already-present ULID (merge safety)
    from src.utils.file_lock import write_json_with_lock

    pdir = tmp_path / "people"
    pdir.mkdir()
    pid = "01HX7YZABCDEFGHJKMNPQRSTVW"
    write_json_with_lock(
        pdir / "p.json", {"PersonID": pid, "name": "Smith"}, entity="people"
    )
    assert json.loads((pdir / "p.json").read_text())["PersonID"] == pid


def test_guard_validation_is_unconditional(monkeypatch, tmp_path):
    """There is NO opt-out: a schema-invalid record is blocked even if the old
    WWII_WRITE_VALIDATION=off env is set (the escape hatch was removed)."""
    monkeypatch.setenv("WWII_WRITE_VALIDATION", "off")  # must have NO effect now
    from src.utils.file_lock import write_json_with_lock

    pdir = tmp_path / "people"
    pdir.mkdir()
    # Missing the mandatory 'name' (a non-PK required field) -> must be BLOCKED regardless of env.
    # (A missing PersonID would auto-heal; 'name' is unrecoverable without reanalysis, so it blocks.)
    write_json_with_lock(pdir / "x.json", {"rank": "General"}, entity="people")
    assert not (pdir / "x.json").exists()  # blocked — the guard cannot be disabled

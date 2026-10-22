"""Regression: a person's stated unit with NO matching people_group bootstraps a PROVISIONAL
group (owner directive) — LLM-validated as a real formation, carrying the person's event/source
lineage so dedup/Phase-3 can confirm it. Gated by PROVISIONAL_GROUPS; fail-safe otherwise.
"""

import json
from pathlib import Path

import pytest

from src.extraction import enrich_biographies as eb


class _FakeGrok:
    """Minimal chat_completion stub: returns a canned validity JSON (no network)."""

    def __init__(self, reply: str):
        self._reply = reply

    def chat_completion(self, prompt, *a, **k):  # noqa: D401, ARG002
        return self._reply


def _person(tmp: Path) -> Path:
    people = tmp / "people"
    people.mkdir()
    rec = {
        "PersonID": "01HX7YZABCDEFGHJKMNPQRSTVW",
        "name": "PFC John Smith",
        "event_mentions": [
            {
                "EventID": "01HX7YZABCDEFGHJKMNPQRSTAA",
                "Event_Name": "Battle of Aachen",
                "Sub_eventID": "01HX7YZABCDEFGHJKMNPQRSTBB",
                "Sub_event_Name": "Assault on Aachen",
                "book": "Some Green Book",
                "author": "US Army",
                "series": "US Army in WWII",
            }
        ],
        "biographical_profile": {
            "units_served": [{"unit": "4th Division", "role": "PFC"}]
        },
    }
    f = people / "john_smith.json"
    f.write_text(json.dumps(rec))
    return f


def _empty_groups(tmp: Path) -> Path:
    g = tmp / "people_groups"
    g.mkdir()
    (g / "index.json").write_text("{}")
    return g


def test_provisional_group_created_when_enabled_and_unit_valid(tmp_path, monkeypatch):
    monkeypatch.setenv("PROVISIONAL_GROUPS", "true")
    pf = _person(tmp_path)
    gdir = _empty_groups(tmp_path)
    grok = _FakeGrok(
        '{"valid": true, "canonical_name": "4th Infantry Division", "echelon": "division"}'
    )
    linked = eb._link_person_to_groups(pf, gdir, grok)
    assert linked == 1
    # A group file now exists (besides index.json) and the person is a member.
    files = [p for p in gdir.glob("*.json") if p.name != "index.json"]
    assert len(files) == 1
    grp = json.loads(files[0].read_text())
    assert grp["group_name"] == "4th Infantry Division"
    assert grp["group_type"] == "military_unit"
    assert grp["enrichment_status"] == "ambiguous"
    assert "provisional" in (grp.get("enrichment_ambiguity_reason") or "")
    assert any(m["PersonID"] == "01HX7YZABCDEFGHJKMNPQRSTVW" for m in grp["members"])
    # Event/source lineage carried from the person (EventID preserved, fresh MentionID).
    em = grp["event_mentions"][0]
    assert em["EventID"] == "01HX7YZABCDEFGHJKMNPQRSTAA"
    assert em["book"] == "Some Green Book"


def test_disabled_by_default_no_group_created(tmp_path, monkeypatch):
    monkeypatch.delenv("PROVISIONAL_GROUPS", raising=False)
    pf = _person(tmp_path)
    gdir = _empty_groups(tmp_path)
    grok = _FakeGrok('{"valid": true, "canonical_name": "4th Infantry Division"}')
    linked = eb._link_person_to_groups(pf, gdir, grok)
    assert linked == 0
    assert [p for p in gdir.glob("*.json") if p.name != "index.json"] == []


def test_invalid_unit_not_created_when_enabled(tmp_path, monkeypatch):
    monkeypatch.setenv("PROVISIONAL_GROUPS", "true")
    pf = _person(tmp_path)
    gdir = _empty_groups(tmp_path)
    grok = _FakeGrok('{"valid": false}')  # LLM says not a real unit
    linked = eb._link_person_to_groups(pf, gdir, grok)
    assert linked == 0
    assert [p for p in gdir.glob("*.json") if p.name != "index.json"] == []


def test_no_grok_client_no_creation(tmp_path, monkeypatch):
    monkeypatch.setenv("PROVISIONAL_GROUPS", "true")
    pf = _person(tmp_path)
    gdir = _empty_groups(tmp_path)
    linked = eb._link_person_to_groups(pf, gdir, None)  # no client -> fail-safe
    assert linked == 0


def test_validate_unknown_returns_none(tmp_path):
    # Garbled/non-JSON reply -> None (no creation).
    assert eb._validate_and_canonicalize_unit(_FakeGrok("I am not sure"), "xyz") is None
    assert (
        eb._validate_and_canonicalize_unit(_FakeGrok('{"valid": false}'), "xyz") is None
    )


def test_merge_promotes_provisional_group_when_real_extraction_confirms():
    """A provisional group (bootstrapped from a person) that is later confirmed by a REAL
    narrative extraction must be PROMOTED: provisional markers cleared, members + event_mentions
    unioned (no data loss). Reuses the existing _merge_group path."""
    from src.extraction.people_groups import _merge_group

    provisional = {
        "GroupID": "01HX7YZABCDEFGHJKMNPQRSTAA",
        "group_name": "4th Infantry Division",
        "enrichment_status": "ambiguous",
        "enrichment_ambiguity_reason": "provisional: bootstrapped from a person's units_served",
        "members": [{"PersonID": "01HX7YZABCDEFGHJKMNPQRSTVW", "name": "Smith"}],
        "event_mentions": [
            {
                "MentionID": "01HX7YZABCDEFGHJKMNPQRSTM1",
                "EventID": "01HX7YZABCDEFGHJKMNPQRSTE1",
            }
        ],
    }
    real = {
        "GroupID": "01HX7YZABCDEFGHJKMNPQRSTBB",
        "group_name": "4th Infantry Division",
        "enrichment_status": None,
        "enrichment_ambiguity_reason": None,
        "members": [{"PersonID": "01HX7YZABCDEFGHJKMNPQRSTVX", "name": "Jones"}],
        "event_mentions": [
            {
                "MentionID": "01HX7YZABCDEFGHJKMNPQRSTM2",
                "EventID": "01HX7YZABCDEFGHJKMNPQRSTE2",
            }
        ],
    }
    merged = _merge_group(provisional, real)
    # Promoted: provisional markers cleared.
    assert merged["enrichment_status"] is None
    assert merged["enrichment_ambiguity_reason"] is None
    # No data loss: both members + both mentions survive.
    assert len(merged["members"]) == 2
    assert len(merged["event_mentions"]) == 2


def test_merge_two_provisionals_stays_provisional():
    """Merging two provisional records (neither confirmed) leaves it provisional."""
    from src.extraction.people_groups import _merge_group

    p1 = {
        "GroupID": "01HX7YZABCDEFGHJKMNPQRSTAA",
        "group_name": "4th Infantry Division",
        "enrichment_status": "ambiguous",
        "enrichment_ambiguity_reason": "provisional: bootstrapped from a person's units_served",
        "members": [{"PersonID": "01HX7YZABCDEFGHJKMNPQRSTVW", "name": "Smith"}],
    }
    p2 = {
        "GroupID": "01HX7YZABCDEFGHJKMNPQRSTBB",
        "group_name": "4th Infantry Division",
        "enrichment_status": "ambiguous",
        "enrichment_ambiguity_reason": "provisional: bootstrapped from a person's units_served",
        "members": [{"PersonID": "01HX7YZABCDEFGHJKMNPQRSTVX", "name": "Jones"}],
    }
    merged = _merge_group(p1, p2)
    assert merged["enrichment_status"] == "ambiguous"
    assert "provisional" in merged["enrichment_ambiguity_reason"]


def test_null_eventid_person_yields_schema_clean_group(tmp_path, monkeypatch):
    """A person's event_mention EventID is NULLABLE, but the groups schema's event_mentions
    EventID is NON-nullable. A None/absent EventID must be OMITTED from the minted group's
    event_mentions (not written as null), so the record is schema-valid without relying on the
    legacy leniency path."""
    import jsonschema

    from src.schemas.groups_output import GROUPS_OUTPUT_SCHEMA

    monkeypatch.setenv("PROVISIONAL_GROUPS", "true")
    people = tmp_path / "people"
    people.mkdir()
    rec = {
        "PersonID": "01HX7YZABCDEFGHJKMNPQRSTVW",
        "name": "PFC John Smith",
        # event_mention WITHOUT an EventID (legal on a person record)
        "event_mentions": [{"Event_Name": "Battle of Aachen", "book": "Some Book"}],
        "biographical_profile": {"units_served": [{"unit": "4th Division"}]},
    }
    pf = people / "smith.json"
    pf.write_text(json.dumps(rec))
    gdir = tmp_path / "people_groups"
    gdir.mkdir()
    (gdir / "index.json").write_text("{}")
    grok = _FakeGrok('{"valid": true, "canonical_name": "4th Infantry Division"}')

    linked = eb._link_person_to_groups(pf, gdir, grok)
    assert linked == 1
    files = [p for p in gdir.glob("*.json") if p.name != "index.json"]
    assert len(files) == 1
    grp = json.loads(files[0].read_text())
    # EventID must be ABSENT (omitted), never present-as-null.
    for em in grp.get("event_mentions", []):
        assert "EventID" not in em or em["EventID"]
    # The persisted record validates cleanly against the groups schema.
    jsonschema.validate(grp, GROUPS_OUTPUT_SCHEMA)

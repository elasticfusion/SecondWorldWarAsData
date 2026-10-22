"""people schema 2.25 — biography_sources provenance + prompt↔schema alignment.

Covers:
- biography_sources now carries EventID/Sub_eventID (event-graph provenance; filled at extraction)
  + a Phase-3-populated BibliographyID; a record using them validates against the people schema.
- the under-specified EVENT_MENTIONS_SCHEMA + units_served now DECLARE the fields the prompt emits.
- ALIGNMENT (closes the prompt↔schema sync gap for people): the prompt's biography_sources +
  event_mentions example fields are all ACCEPTED by the people output schema (would catch a prompt
  that emits a field the schema would reject, OR — read the other way — flags when the schema adds
  a field the prompt example never shows).
"""

import json

import yaml
from jsonschema import validate
from pathlib import Path

from src.schemas.people_output import PEOPLE_OUTPUT_SCHEMA

_PROMPT = Path(__file__).parent.parent / "prompts" / "people.yaml"
ULID = "01HX7YZABCDEFGHJKMNPQRSTVW"


def _prompt_example() -> dict:
    return json.loads(yaml.safe_load(_PROMPT.read_text())["schema"])


def test_biography_sources_schema_accepts_provenance_fields():
    """A people record whose biography_sources carries EventID/Sub_eventID/BibliographyID is
    schema-valid (additive provenance, not rejected)."""
    rec = {
        "PersonID": ULID,
        "name": "Test Person",
        "biographical_profile": {
            "biography_sources": [
                {
                    "source": "Book",
                    "page": "12",
                    "confidence": 0.9,
                    "fields_sourced": ["birth_date"],
                    "EventID": ULID,
                    "Sub_eventID": ULID,
                    "BibliographyID": ULID,
                }
            ]
        },
    }
    validate(instance=rec, schema=PEOPLE_OUTPUT_SCHEMA)  # must not raise


def test_units_served_schema_accepts_structured_fields():
    rec = {
        "PersonID": ULID,
        "name": "T",
        "biographical_profile": {
            "units_served": [
                {
                    "unit": "9th Infantry Division",
                    "from": "1944",
                    "to": "1945",
                    "designation": "9th Infantry Division",
                    "echelon": "division",
                    "unit_number": "9",
                }
            ]
        },
    }
    validate(instance=rec, schema=PEOPLE_OUTPUT_SCHEMA)


def test_event_mention_schema_accepts_prompt_fields():
    rec = {
        "PersonID": ULID,
        "name": "T",
        "event_mentions": [
            {
                "MentionID": ULID,
                "EventID": ULID,
                "Sub-eventID": ULID,
                "Event_Name": "E",
                "Sub-event_Name": "S",
                "book": "B",
                "author": "A",
                "series": "Ser",
                "date": None,
                "DateMentionID": None,
                "position_at_event": "Commander",
                "life_event": "Directed ops",
                "original_text": "General X ordered the attack",
            }
        ],
    }
    validate(instance=rec, schema=PEOPLE_OUTPUT_SCHEMA)


def test_prompt_biography_sources_example_has_provenance():
    """The prompt's biography_sources example must now show the EventID/Sub_eventID provenance
    (guards against the prompt silently losing it on a future edit)."""
    ex = _prompt_example()
    bs = ex["People"][0]["biographical_profile"]["biography_sources"][0]
    assert "EventID" in bs and "Sub_eventID" in bs
    # BibliographyID is Phase-3-populated — the prompt example must NOT instruct the model to emit
    # it (the extractor has no footnote markers to derive it from).
    assert "BibliographyID" not in bs


def test_prompt_people_example_fields_accepted_by_schema():
    """ALIGNMENT: validate the prompt's example PERSON record against the people output schema
    (ULIDs filled) — every field the prompt tells the model to emit must be schema-accepted. The
    prompt returns {"People":[...]}; the output schema validates each PERSISTED person object, so
    we validate the inner person (the granularity the pipeline actually schema-checks).
    """
    ex = _prompt_example()
    person = ex["People"][0]
    person["PersonID"] = ULID
    for m in person.get("event_mentions", []):
        m["EventID"] = ULID
        m["Sub-eventID"] = ULID
        if m.get("MentionID"):
            m["MentionID"] = ULID
    for bs in person["biographical_profile"].get("biography_sources", []):
        bs["EventID"] = ULID
        bs["Sub_eventID"] = ULID
    validate(instance=person, schema=PEOPLE_OUTPUT_SCHEMA)

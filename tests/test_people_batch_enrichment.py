"""Regression: Phase-2 people Grokipedia/Wikipedia enrichment MUST run on the batch
RETRIEVE/apply path, not only the synchronous ``_process_person`` path.

BUG (confirmed by a live ECS run of 'RoerDams', Phase-1+2-only, --batch): in --batch
mode people records are created/finalized through the generic batch path
``_batch_extract`` -> ``_process_batch_response`` -> ``_process_entity_obj`` ->
``_get_or_create_entity`` / ``_add_event_mention_batch``. That generic path had NO
people-specific enrichment hook, so ``_enrich_person_phase2`` /
``enrich_person_from_sources`` were NEVER invoked for batch-extracted people — famous
generals persisted with empty ``biography_sources`` and no ``birth_date``. The
synchronous path (``_process_person``) enriched before save, masking the gap.

FIX: ``extract_people_batch_async`` passes an ``enrich_record`` callback
(``_enrich_person_batch_apply``) that the batch apply path invokes once the person
record is finalized; it enriches IN PLACE (reusing ``_enrich_person_phase2``), respects
the ``_is_already_enriched`` gate, and is fail-safe.

This test drives the REAL ``extract_people_batch_async`` with a mocked (non-collecting)
Grok response and asserts ``enrich_person_from_sources`` is called for the fresh person.
It FAILS on the pre-fix code (call count 0) and PASSES after the fix. No live HTTP.
"""

import asyncio
import json
from unittest import mock


def _event_data():
    """A single sub-event mentioning one famous general (fresh, not yet enriched)."""
    seid = "01M5QS1EYJN8M4W4Y8J18RY35X"
    return {
        "Chapter": "The Roer Crossing",
        "Event": {
            "EventID": "01M5QS1EYJG35JQ36NMD0BPET1",
            "Sub-events": [
                {
                    "Sub-eventID": seid,
                    "Sub-event_summary": "First Army forces the Roer.",
                    "Sub-event_fulltext": {
                        "p1": "General Courtney H. Hodges directed the crossing."
                    },
                }
            ],
        },
    }


def _fake_people_response():
    """What grok_client.extract_json returns on the RETRIEVE pass (real, not collecting)."""
    return {
        "people": [
            {
                "sub_event_id": "01M5QS1EYJN8M4W4Y8J18RY35X",
                "people": [
                    {
                        "name": "Courtney H. Hodges",
                        "rank": "General",
                        "unit": "First Army",
                        "country": "USA",
                        "position_at_event": "Commander, First Army",
                        "life_event": "Directed the Roer crossing.",
                        "original_text": "General Courtney H. Hodges directed the crossing.",
                    }
                ],
            }
        ]
    }


def test_batch_people_path_invokes_enrichment(tmp_path):
    """A fresh person extracted on the --batch apply path MUST trigger
    enrich_person_from_sources. Pre-fix: never called (assertion fails)."""
    from src.extraction import batch_parallel as bp

    output_root = tmp_path / "book"
    (output_root / "people").mkdir(parents=True)
    (output_root / "people" / "index.json").write_text("{}", encoding="utf-8")

    client = mock.Mock()
    # RETRIEVE pass: extract_json returns a real response (NOT BatchModeCollecting), so
    # the batch apply path creates + finalizes the person record.
    client.extract_json.return_value = _fake_people_response()

    enrich_calls = []

    def _fake_enrich(person_data, grok_client):
        # Record the call and simulate a successful Wikipedia/Grokipedia enrichment so
        # the record is persisted as enriched. No network.
        enrich_calls.append(person_data.get("name"))
        bio = person_data.setdefault("biographical_profile", {})
        bio["birth_date"] = "1887-01-05"
        bio.setdefault("biography_sources", []).append(
            {
                "source": "Wikipedia",
                "url": "https://en.wikipedia.org/wiki/Courtney_Hodges",
            }
        )
        person_data["enrichment_status"] = "enriched"
        return True

    # Patch at the boundary used by _enrich_person_phase2 (no live HTTP).
    with mock.patch(
        "src.extraction.enrich_biographies.enrich_person_from_sources",
        side_effect=_fake_enrich,
    ):
        asyncio.run(
            bp.extract_people_batch_async(
                _event_data(),
                client,
                output_root,
                book_meta={"book": "RoerDams", "author": "x", "series": "y"},
            )
        )

    assert enrich_calls == [
        "Courtney H. Hodges"
    ], f"enrichment must run for the fresh batch-extracted person; got {enrich_calls!r}"

    # And the enriched data must be persisted to the person file (not lost).
    people_files = [
        p for p in (output_root / "people").glob("*.json") if p.name != "index.json"
    ]
    assert people_files, "a person file must have been written"
    saved = json.loads(people_files[0].read_text(encoding="utf-8"))
    assert saved["biographical_profile"].get("birth_date") == "1887-01-05"
    assert saved["biographical_profile"].get("biography_sources"), "sources persisted"


def test_batch_people_enrichment_is_failsafe(tmp_path):
    """An enrichment error MUST NOT abort extraction: the un-enriched person still saves."""
    from src.extraction import batch_parallel as bp

    output_root = tmp_path / "book"
    (output_root / "people").mkdir(parents=True)
    (output_root / "people" / "index.json").write_text("{}", encoding="utf-8")

    client = mock.Mock()
    client.extract_json.return_value = _fake_people_response()

    with mock.patch(
        "src.extraction.enrich_biographies.enrich_person_from_sources",
        side_effect=RuntimeError("wikipedia fetch exploded"),
    ):
        # Must NOT raise.
        asyncio.run(
            bp.extract_people_batch_async(
                _event_data(),
                client,
                output_root,
                book_meta={"book": "RoerDams", "author": "x", "series": "y"},
            )
        )

    people_files = [
        p for p in (output_root / "people").glob("*.json") if p.name != "index.json"
    ]
    assert people_files, "the un-enriched person record must still be saved on error"
    saved = json.loads(people_files[0].read_text(encoding="utf-8"))
    assert saved["name"] == "Courtney H. Hodges"

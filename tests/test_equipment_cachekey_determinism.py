"""Regression: equipment=0 — the equipment extractor embeds the full event_data into its cached
prompt. Two between-pass divergences made the RETRIEVE re-run miss the cache (→ empty → 0 persisted,
the supplemental #399/#401 class):
  (1) dict-key ORDERING differences across a reload;
  (2) ADDED keys — `_stamp_schema_versions` rewrites `*-event.json` between the COLLECT and RETRIEVE
      passes, injecting `_schema_version`/`_metadata`.
The fix: (a) `sort_keys=True` canonical serialization (handles ordering), AND (b) a STABLE
content-independent `cache_key` derived from the DETERMINISTIC EventID (handles added keys too).
These tests prove the equipment cache_key is identical across BOTH divergences, and that the
people_groups prompt builder is canonical.
"""

import json
from unittest import mock


def _event_two_orderings():
    a = {
        "Event": {
            "EventID": "01EVENT00000000000000000000",
            "Event_Name": "Carentan Counterattack",
            "Sub-events": [
                {"Sub-eventID": "01SUB0000000000000000000000", "text": "tanks advanced"}
            ],
        },
        "Chapter": "9",
        "book": "XChannel",
    }
    b = {
        "book": "XChannel",
        "Chapter": "9",
        "Event": {
            "Sub-events": [
                {"text": "tanks advanced", "Sub-eventID": "01SUB0000000000000000000000"}
            ],
            "Event_Name": "Carentan Counterattack",
            "EventID": "01EVENT00000000000000000000",
        },
    }
    return a, b


def test_canonical_serialization_is_order_independent():
    a, b = _event_two_orderings()
    assert json.dumps(a, indent=2, sort_keys=True) == json.dumps(
        b, indent=2, sort_keys=True
    )
    # sanity: WITHOUT sort_keys they differ (the ordering half of the bug)
    assert json.dumps(a, indent=2) != json.dumps(b, indent=2)


def _capture_equipment_cache_key(event_data):
    """Drive the real _extract_equipment_with_llm and capture the cache_key it passes."""
    from src.extraction.equipment import _extract_equipment_with_llm
    from src.grok_client import BatchModeCollecting

    captured = {}

    def fake_extract_json(prompt, **kwargs):
        captured["prompt"] = prompt
        captured["cache_key"] = kwargs.get("cache_key")
        raise BatchModeCollecting()

    gc = mock.Mock()
    gc._get_model.return_value = "grok-4.3"
    gc.extract_json.side_effect = fake_extract_json
    try:
        _extract_equipment_with_llm(event_data, gc, max_retries=1)
    except BatchModeCollecting:
        pass
    return captured


def test_equipment_cache_key_stable_across_ordering():
    a, b = _event_two_orderings()
    ka = _capture_equipment_cache_key(a)
    kb = _capture_equipment_cache_key(b)
    assert (
        ka["prompt"] == kb["prompt"]
    ), "prompt must be identical across event dict ordering"
    assert ka["cache_key"] and ka["cache_key"] == kb["cache_key"]


def test_equipment_cache_key_stable_across_ADDED_keys():
    """The decisive case: schema-stamp ADDS keys between passes. A prompt-hash key would still
    differ; the EventID-derived cache_key must be IDENTICAL so the retrieve hits the cache.
    """
    a, _ = _event_two_orderings()
    collect = _capture_equipment_cache_key(a)
    # simulate the RETRIEVE-pass event file after _stamp_schema_versions injected metadata
    stamped = dict(a)
    stamped["_schema_version"] = "2.6"
    stamped["_metadata"] = {"stamped_at": "2026-10-20T00:00:00Z"}
    stamped["Event"] = dict(a["Event"])
    stamped["Event"]["_schema_version"] = "2.6"
    retrieve = _capture_equipment_cache_key(stamped)
    # the PROMPT differs (added keys) — that's exactly why the prompt-hash key was insufficient
    assert collect["prompt"] != retrieve["prompt"]
    # but the stable EventID-derived cache_key is IDENTICAL → retrieve cache HIT
    assert collect["cache_key"] == retrieve["cache_key"]


def test_people_groups_prompt_identical_across_event_ordering():
    from src.extraction.people_groups import _build_extraction_prompt

    a, b = _event_two_orderings()
    assert _build_extraction_prompt(a) == _build_extraction_prompt(b)

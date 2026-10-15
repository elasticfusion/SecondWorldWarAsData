"""Supplemental cache key must be STABLE across volatile prompt (fulltext) drift.

Root cause of the 0-bibliography bug: the supplemental prompt embeds the sub-event
fulltext, which is regenerated/summarized differently between a batch SUBMIT and the
later retrieve RE-RUN. That changed the prompt-hash cache key, so the retrieve populated
the cache under one key and the re-run looked up another -> cache miss -> no materials ->
no bibliography. Fix: cache key derives from a deterministic `stable_material`
(ids + sorted refs + endnote_block) that EXCLUDES the volatile fulltext.
"""

import copy
import hashlib

from src.extraction.supplemental import create_supplemental_prompt


def _event():
    return {
        "Event": {
            "EventID": "01EVENT0000000000000000000",
            "Event_Name": "Test",
            "Sub-events": [
                {
                    "Sub-eventID": "01SUB00000000000000000000A",
                    "Sub-event_summary": "A summary",
                    "Sub-event_fulltext": {"p1": "original paragraph text"},
                    "Endnote_References": [],
                    "Footnote_References": [3, 1],
                }
            ],
        }
    }


def _key(material: str) -> str:
    return hashlib.sha256(("supplemental:v1:" + material).encode()).hexdigest()


def test_stable_material_invariant_to_fulltext_drift():
    ev_a = _event()
    ev_b = copy.deepcopy(ev_a)
    # Simulate submit-vs-rerun fulltext regeneration.
    ev_b["Event"]["Sub-events"][0]["Sub-event_fulltext"] = {
        "p1": "COMPLETELY DIFFERENT regenerated/summarized fulltext across the re-run"
    }

    _, prompt_a, material_a = create_supplemental_prompt(ev_a)[0]
    _, prompt_b, material_b = create_supplemental_prompt(ev_b)[0]

    # The prompt text DOES change (fulltext is embedded for context)...
    assert prompt_a != prompt_b
    # ...but the stable material + derived cache key are IDENTICAL -> same cache entry.
    assert material_a == material_b
    assert _key(material_a) == _key(material_b)
    # And the volatile fulltext is NOT part of the stable material.
    assert "original paragraph text" not in material_a
    assert "regenerated" not in material_b.lower()


def test_stable_material_distinguishes_different_refs():
    ev = _event()
    _, _, material_refs13 = create_supplemental_prompt(ev)[0]
    ev2 = _event()
    ev2["Event"]["Sub-events"][0]["Footnote_References"] = [9, 9, 2]
    _, _, material_refs92 = create_supplemental_prompt(ev2)[0]
    # Different reference numbers -> different identity -> different key.
    assert material_refs13 != material_refs92
    assert _key(material_refs13) != _key(material_refs92)

"""Per-mention quantity + place + assertion traceability, and source-recheck recovery.

Rules under test (owner-specified):
- "10 M4 Shermans at the crossroads" -> quantity 10 (int), quantity_text "10"
- "several Shermans" -> quantity null, quantity_text "several" (vague preserved, not invented)
- ambient/stock footage that doesn't assert presence -> no mention (gate is upstream prompt;
  here we assert the schema supports assertion_source and recheck recovers quantity/place)
- conflicting mentions coexist, each with its own original_text (traceability)
- the source recheck fills only the RECORD's missing origin/category (quantity and place are the
  mention's, from refine)
"""

from src.extraction.equipment_source_recheck import recheck_equipment_from_source
from src.schemas.equipment_output import EQUIPMENT_OUTPUT_SCHEMA


class _Grok:
    def __init__(self, r):
        self._r = r

    def extract_json(self, prompt, use_cache=True, cache_type=""):
        return self._r


def test_schema_declares_quantity_place_assertion():
    props = EQUIPMENT_OUTPUT_SCHEMA["properties"]["event_mentions"]["items"][
        "properties"
    ]
    for f in ("quantity", "quantity_text", "PlaceID", "place_name", "assertion_source"):
        assert f in props, f"{f} must be declared on the mention"
    # quantity is an integer (nullable), not a string
    assert "integer" in props["quantity"]["type"]


def test_a_record_with_its_origin_and_category_is_not_rechecked():
    # quantity / quantity_text / place_name are per MENTION (refine reads them from the quote);
    # asking for them made every item a recheck request (2,249 in the 2026-11-14 run)
    class _Never:
        def extract_json(self, *a, **k):
            raise AssertionError("asked")

    e = {
        "common_name": "M4 Sherman",
        "country_of_origin": "USA",
        "category": "armor",
        "event_mentions": [{"original_text": "10 M4 Shermans at the crossroads"}],
    }
    assert recheck_equipment_from_source(e, _Never()) == 0


def test_only_the_missing_record_fields_are_asked():
    asked = []

    class _Grok2:
        def extract_json(self, prompt, use_cache=True, cache_type=""):
            asked.append(prompt)
            return {"category": "armor", "quantity": 10}

    e = {
        "common_name": "M4 Sherman",
        "country_of_origin": "USA",
        "event_mentions": [{"original_text": "10 M4 Shermans at the crossroads"}],
    }
    assert recheck_equipment_from_source(e, _Grok2()) == 1
    assert e["category"] == "armor" and "quantity" not in e
    assert '"category"' in asked[0] and '"quantity"' not in asked[0]


def test_conflicting_mentions_coexist_with_distinct_source():
    # Two sources disagree on count; both are retained as separate mentions, each traceable.
    equip = {
        "common_name": "M4 Sherman",
        "country_of_origin": "USA",
        "event_mentions": [
            {
                "MentionID": "01A",
                "quantity": 10,
                "assertion_source": "narrative",
                "original_text": "10 Shermans engaged at the Cherbourg crossroads",
                "book": "Source A",
            },
            {
                "MentionID": "01B",
                "quantity": 5,
                "assertion_source": "media_narration",
                "original_text": "narration: 5 Shermans engaged at Cherbourg",
                "book": "Newsreel B",
            },
        ],
    }
    qs = {m["quantity"] for m in equip["event_mentions"]}
    assert qs == {10, 5}  # conflict preserved, not merged
    # each is independently traceable to its origin
    for m in equip["event_mentions"]:
        assert m["original_text"] and m["book"]

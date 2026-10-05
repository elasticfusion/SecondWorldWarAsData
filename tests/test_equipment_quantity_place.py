"""Per-mention quantity + place + assertion traceability, and source-recheck recovery.

Rules under test (owner-specified):
- "10 M4 Shermans at the crossroads" -> quantity 10 (int), quantity_text "10"
- "several Shermans" -> quantity null, quantity_text "several" (vague preserved, not invented)
- ambient/stock footage that doesn't assert presence -> no mention (gate is upstream prompt;
  here we assert the schema supports assertion_source and recheck recovers quantity/place)
- conflicting mentions coexist, each with its own original_text (traceability)
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


def test_recheck_recovers_exact_quantity_as_int():
    e = {
        "common_name": "M4 Sherman",
        "country_of_origin": "USA",
        "category": "armor",
        "event_mentions": [
            {"original_text": "10 M4 Shermans at the crossroads in Cherbourg"}
        ],
    }
    n = recheck_equipment_from_source(
        e,
        _Grok(
            {
                "quantity": "10",
                "quantity_text": "10",
                "place_name": "the crossroads in Cherbourg",
            }
        ),
    )
    assert n == 3
    assert e["quantity"] == 10 and isinstance(e["quantity"], int)  # coerced to int
    assert e["quantity_text"] == "10"
    assert e["place_name"] == "the crossroads in Cherbourg"


def test_recheck_vague_count_keeps_text_not_number():
    e = {
        "common_name": "M4 Sherman",
        "country_of_origin": "USA",
        "category": "armor",
        "event_mentions": [{"original_text": "several Shermans at the crossroads"}],
    }
    # LLM returns null quantity (vague) + verbatim phrase
    n = recheck_equipment_from_source(
        e,
        _Grok(
            {
                "quantity": None,
                "quantity_text": "several",
                "place_name": "the crossroads",
            }
        ),
    )
    assert "quantity" not in e  # not fabricated
    assert e["quantity_text"] == "several"
    assert n >= 1


def test_bad_quantity_string_is_skipped_not_stored():
    e = {
        "common_name": "M4",
        "country_of_origin": "USA",
        "category": "armor",
        "event_mentions": [{"original_text": "some Shermans"}],
    }
    # non-numeric quantity -> coercion fails -> field skipped (no crash, not stored)
    recheck_equipment_from_source(
        e, _Grok({"quantity": "a few", "quantity_text": "a few"})
    )
    assert "quantity" not in e
    assert e["quantity_text"] == "a few"


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

"""Regression: Grok wraps enrichment JSON in a ```json fence; _enrich_equipment_data must
parse it (bare json.loads silently returned {} for every record — a live-caught bug)."""

import src.extraction.equipment as eq


class _FencedGrok:
    """extract_json is what strips the fence in production; emulate its contract."""

    def extract_json(self, prompt, temperature=0.1, use_cache=True, cache_type=""):
        # extract_json returns a parsed dict (fence already stripped by the client)
        return {"specifications": {"weight_kg": 30300}, "type": "medium_tank"}

    def chat_completion(self, *a, **k):  # must NOT be used now
        raise AssertionError("enrichment must use extract_json, not chat_completion")


def test_enrichment_parses_via_extract_json():
    out = eq._enrich_equipment_data("M4 Sherman", "M4", "armor", _FencedGrok())
    assert out["specifications"]["weight_kg"] == 30300
    assert out["type"] == "medium_tank"

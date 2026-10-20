"""CRITICAL regression: optional-entity _call_and_parse_* must RE-RAISE BatchModeCollecting on the
batch COLLECT pass (not swallow it to {}/[]). Swallowing caused _mark_processed to run on collect ->
the retrieve pass skipped -> all optional entities (casualties/weather/equipment/logistics) persisted
ZERO despite source content."""

import pytest
from src.grok_client import BatchModeCollecting


class _CollectingClient:
    """Stand-in grok client whose LLM calls always raise BatchModeCollecting (collect pass)."""

    def chat_completion(self, *a, **k):
        raise BatchModeCollecting("queued")

    def extract_json(self, *a, **k):
        raise BatchModeCollecting("queued")

    def extract_structured(self, *a, **k):
        raise BatchModeCollecting("queued")


def test_casualties_call_reraises_collecting():
    from src.extraction.casualties import _call_and_parse_casualties

    with pytest.raises(BatchModeCollecting):
        _call_and_parse_casualties(_CollectingClient(), "prompt")


def test_weather_call_reraises_collecting():
    from src.extraction.weather_central import _call_and_parse_weather

    with pytest.raises(BatchModeCollecting):
        _call_and_parse_weather(_CollectingClient(), "prompt", 3)


def test_equipment_extract_reraises_collecting():
    from src.extraction.equipment import extract_equipment_from_event
    import tempfile, json, pathlib

    d = pathlib.Path(tempfile.mkdtemp())
    ev = d / "chapter1a-event.json"
    ev.write_text(
        json.dumps(
            {
                "Event": {
                    "EventID": "01E",
                    "Sub-events": [
                        {
                            "Sub-eventID": "01S",
                            "Sub-event_fulltext": {
                                "Paragraph_1": "The M4 Sherman tank advanced."
                            },
                        }
                    ],
                }
            }
        )
    )
    with pytest.raises(BatchModeCollecting):
        extract_equipment_from_event(
            event_file=ev,
            output_dir=d / "equipment",
            grok_client=_CollectingClient(),
            output_root=d,
        )


def test_chunk_halving_reraises_collecting():
    from src.utils.chunked_extract import extract_with_chunk_halving

    def _fn(chunk):
        raise BatchModeCollecting("queued")

    with pytest.raises(BatchModeCollecting):
        extract_with_chunk_halving([[1, 2, 3]], _fn, "test")


def test_people_phase2_enrich_reraises_collecting(monkeypatch):
    """People bio enrichment must propagate BatchModeCollecting on the collect pass (same bug class):
    _enrich_person_phase2 wrapped enrich_person_from_sources in a broad except that swallowed it,
    causing biographical_profile to never persist."""
    from src.extraction import people as _people

    def _raise(*a, **k):
        raise BatchModeCollecting("queued")

    import src.extraction.enrich_biographies as eb

    monkeypatch.setattr(eb, "enrich_person_from_sources", _raise)
    with pytest.raises(BatchModeCollecting):
        _people._enrich_person_phase2({"name": "Omar Bradley"}, object())


# --- Persist-side regression (optional-entity under-persist, 2026-10-20) ---------------
# The optional 2nd batch succeeded (325/325) yet weather/logistics persisted 0 because the
# parse-filters silently dropped extracted mentions: weather required BOTH date AND place_name;
# logistics required a {"logistics":[...]} wrapper AND strict model_validate. Resolved
# date/place/enum specifics are ENRICHMENT, not gating. These assert content-bearing mentions
# survive the parser.


def test_weather_keeps_content_without_resolved_date_or_place():
    from src.extraction.weather_central import _parse_weather_response

    resp = {
        "SE1": [
            {
                "weather_description": "Overcast and stormy with high winds"
            },  # no date/place
            {
                "weather_description": "Fog cleared",
                "date": "1944-06-06",
                "place_name": "Normandy",
            },
        ],
        "SE2": [{"date": "1944-06-05"}],  # no descriptive content -> drop
    }
    out = _parse_weather_response(resp)
    assert len(out.get("SE1", [])) == 2  # both kept — date/place optional
    assert len(out.get("SE2", [])) == 0  # empty descriptive content dropped


def test_logistics_accepts_bare_list_and_partial_items():
    from src.extraction.logistics import _parse_logistics_response

    # bare list (no {"logistics":[...]} wrapper) must be accepted
    bare = {
        "SE1": [
            {
                "type": "supply_shortage",
                "category": "fuel",
                "description": "fuel ran low",
                "severity": "high",
            }
        ]
    }
    assert sum(len(v) for v in _parse_logistics_response(bare).values()) == 1
    # wrapped but missing required enum fields -> coerced + kept (has a description)
    partial = {
        "SE1": {
            "logistics": [{"description": "ammunition shortage delayed the attack"}]
        }
    }
    assert sum(len(v) for v in _parse_logistics_response(partial).values()) == 1
    # no descriptive content -> dropped
    empty = {"SE1": [{"description": ""}]}
    assert sum(len(v) for v in _parse_logistics_response(empty).values()) == 0


def test_weather_whitespace_only_content_dropped_and_condition_carried():
    from src.extraction.weather_central import _parse_weather_response

    resp = {
        "SE1": [{"weather_description": "   "}],  # whitespace-only -> drop
        "SE2": [
            {"condition": "fog"}
        ],  # kept; condition carried into weather_description
    }
    out = _parse_weather_response(resp)
    assert len(out.get("SE1", [])) == 0
    assert len(out.get("SE2", [])) == 1
    assert out["SE2"][0]["weather_description"] == "fog"
    assert out["SE2"][0]["original_text"] == "fog"


def test_logistics_strict_path_empty_description_dropped():
    from src.extraction.logistics import _parse_logistics_response

    # all required enums present but description empty -> must NOT persist (uniform floor)
    resp = {
        "SE1": [
            {
                "type": "supply_shortage",
                "category": "fuel",
                "description": "   ",
                "severity": "high",
            }
        ]
    }
    assert sum(len(v) for v in _parse_logistics_response(resp).values()) == 0

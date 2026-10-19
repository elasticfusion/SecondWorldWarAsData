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

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

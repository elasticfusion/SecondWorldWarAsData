"""Logistics items were 100%-dropped (dropped=4 kept=0, XChannel-8 2026-10-30): the prompt emits
impacted_* as lists of OBJECTS and fields date_string/weather_impact/paragraph_numbers, but
LogisticsExtraction wants impacted_* as List[str] and date_start/weather/paragraphs. The shape
normalizer must map prompt shape -> model shape so items validate."""

from src.extraction.logistics import (
    _validate_logistics_item,
    _normalize_logistics_shape,
)

_PROMPT_SHAPE = {
    "type": "supply_shortage",
    "category": "ammunition",
    "description": "Ammunition critically low for VII Corps",
    "severity": "critical",
    "date_string": "1944-07-15",
    "status": "unresolved",
    "impacted_organizations": [
        {"name": "VII Corps", "nationality": "USA", "role": "recipient"}
    ],
    "impacted_places": [{"name": "St. Lô"}],
    "impacted_people": [],
    "weather_impact": "Rain delayed convoys",
    "paragraph_numbers": [23, 24],
}


def test_prompt_shaped_item_validates():
    r = _validate_logistics_item(dict(_PROMPT_SHAPE))
    assert r is not None
    assert r.impacted_organizations == ["VII Corps"]
    assert r.impacted_places == ["St. Lô"]
    assert r.weather == "Rain delayed convoys"
    assert r.paragraphs == [23, 24]
    assert r.date_start == "1944-07-15"


def test_normalizer_handles_string_lists_too():
    # already-string impacted_* (the inline-prompt shape) pass through unchanged
    out = _normalize_logistics_shape(
        {"description": "x", "impacted_organizations": ["VII Corps"]}
    )
    assert out["impacted_organizations"] == ["VII Corps"]


def test_model_field_wins_over_alias():
    # if both date_start and date_string present, model field is not clobbered
    out = _normalize_logistics_shape(
        {"description": "x", "date_start": "1944-06-06", "date_string": "WRONG"}
    )
    assert out["date_start"] == "1944-06-06"

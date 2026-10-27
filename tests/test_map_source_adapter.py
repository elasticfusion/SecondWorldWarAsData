"""P0 proof: a vision-approved map, rendered by the adapter, is consumable by the UNCHANGED event
extraction path (reuse), and carries map_vision provenance. (spec: MAP_AS_SOURCE_DOCUMENT.md)
"""

import logging
from src.extraction.map_source_adapter import (
    build_map_parsed_source,
    build_map_source_metadata,
    MAP_SOURCE_TYPE,
)
from src.extraction.events import create_event_prompt

_DEPICTED = {
    "units": ["28th Infantry Division", "116th Panzer Division"],
    "places": ["Schmidt", "Vossenack", "Kall trail"],
    "front_line_or_sector": "Hürtgen Forest, Roer sector",
    "date_or_window": "2-3 November 1944",
    "operation": "First attack on Schmidt",
    "movements": ["28th Div attacks SE toward Schmidt via Vossenack"],
    "description": "Tactical map of the first attack on Schmidt in the Hürtgen Forest.",
}


def test_adapter_output_feeds_real_create_event_prompt():
    """REUSE seam: adapter parsed -> the real create_event_prompt yields a usable prompt (no
    map-specific event code)."""
    parsed = build_map_parsed_source(
        map_id="01TESTMAP",
        map_title="First Attack on Schmidt (map)",
        approved_image_url="https://example.org/schmidt.jpg",
        depicted=_DEPICTED,
        source_url="https://example.org/page",
    )
    assert parsed is not None
    # parsed is in the create_event_prompt contract
    assert parsed["book"] == "map-01TESTMAP"
    assert parsed["source_type"] == MAP_SOURCE_TYPE
    assert parsed["paragraphs"][0]["absolute_number"] == 1
    assert parsed["images"][0]["url"] == "https://example.org/schmidt.jpg"
    # the REAL event path consumes it
    prompt = create_event_prompt(parsed)
    assert prompt and "Schmidt" in prompt and "28th Infantry" in prompt


def test_adapter_skips_empty_depicted_no_fabrication(caplog):
    """No groundable content -> None (never fabricate an event) + OBSERV skipped."""
    with caplog.at_level(logging.INFO):
        out = build_map_parsed_source(
            map_id="01EMPTY", map_title="t", approved_image_url="", depicted={}
        )
    assert out is None
    assert any("OBSERV map_source.skipped" in r.message for r in caplog.records)


def test_adapter_emits_observ_built(caplog):
    with caplog.at_level(logging.INFO):
        build_map_parsed_source(
            map_id="01OBS",
            map_title="t",
            approved_image_url="http://x/y.jpg",
            depicted=_DEPICTED,
        )
    assert any("OBSERV map_source.built map=01OBS" in r.message for r in caplog.records)


def test_source_metadata_map_vision_provenance():
    sm = build_map_source_metadata(
        map_id="01TESTMAP",
        approved_image_url="https://example.org/schmidt.jpg",
        source_url="https://example.org/page",
    )
    assert sm is not None
    assert sm.source_id == "map-01TESTMAP"
    assert sm.acquisition_method == "map_vision"
    assert sm.media_type == "image"


def test_adapter_failsafe_on_bad_depicted():
    # a non-dict depicted must not raise
    assert build_map_parsed_source(map_id="x", map_title="t", approved_image_url="", depicted=None) is None  # type: ignore[arg-type]

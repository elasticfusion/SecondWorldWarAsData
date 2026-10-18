"""Geocode fixes: name fallback from mention original_text + top-level coord write clears flag."""

from src.enrichment.places_grok_geocode import _place_name, _apply_result, GeocodeResult
from src.utils.semantic_flags import flags_for


def test_place_name_falls_back_to_mention_original_text():
    # A place with NO top-level name field, name only in event_mentions[].original_text (Schmidt).
    place = {
        "PlaceID": "01S",
        "event_mentions": [{"original_text": "Schmidt"}, {"original_text": "Schmidt"}],
    }
    assert _place_name(place) == "Schmidt"


def test_place_name_prefers_explicit_name():
    place = {"current_name": "Aachen", "event_mentions": [{"original_text": "ignore"}]}
    assert _place_name(place) == "Aachen"


def test_place_name_none_when_no_name_anywhere():
    assert _place_name({"PlaceID": "01X", "event_mentions": [{}]}) is None


def test_geocode_writes_top_level_coords_and_clears_flag():
    place = {
        "PlaceID": "01S",
        "event_mentions": [{"original_text": "Schmidt"}],
        "quality_flags": ["geo_missing_coords"],
    }
    # Before: flagged missing coords.
    assert "geo_missing_coords" in flags_for("places", place)
    _apply_result(
        place,
        GeocodeResult(
            found=True,
            latitude=50.66,
            longitude=6.41,
            confidence=0.95,
            country="Germany",
            source="grok",
        ),
    )
    # Top-level coords set (schema root) + nested coords set.
    assert place["latitude"] == 50.66 and place["longitude"] == 6.41
    assert place["coordinates"]["latitude"] == 50.66
    # The geo flag now clears (reads top-level).
    assert flags_for("places", place) == []


def test_place_name_rejects_sentence_verbatim():
    from src.enrichment.places_grok_geocode import _place_name

    # A full sentence verbatim must NOT be used as a geocoder name.
    assert (
        _place_name(
            {
                "event_mentions": [
                    {"original_text": "The Germans held Schmidt until November."}
                ]
            }
        )
        is None
    )
    # A short bare name is fine.
    assert _place_name({"event_mentions": [{"original_text": "Schmidt"}]}) == "Schmidt"


def test_safe_entity_filename_blocks_traversal():
    from src.extraction.batch_parallel import _safe_entity_filename as sf

    assert sf("/abs/evil.json", "01U") == "evil.json"
    assert sf("../../x.json", "01U") == "x.json"
    assert sf("..", "01UFALLBACK") == "01UFALLBACK.json"
    assert sf("normal place.json", "01U") == "normal place.json"
    assert "/" not in sf("a/b/c", "01U")

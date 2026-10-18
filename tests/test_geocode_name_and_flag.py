"""Geocode fixes: name fallback from mention original_text + top-level coord write clears flag."""

from src.enrichment.places_grok_geocode import _place_name, _apply_result, GeocodeResult
from src.utils.semantic_flags import flags_for


def test_place_name_falls_back_to_mention_original_text():
    # No name field, no stem: a bare capitalized token mid-text is recoverable only via context.
    # With the stem (the real place name) it resolves; the stem alone is the primary source.
    place = {
        "PlaceID": "01S",
        "event_mentions": [{"original_text": "Schmidt"}, {"original_text": "Schmidt"}],
    }
    assert _place_name(place, stem="schmidt") == "schmidt"


def test_place_name_stem_is_primary_and_short_names_ok():
    # The file stem IS the place name; short real toponyms (user's ETO list) must NOT be rejected.
    for short in ("ulm", "y", "eu", "metz", "caen", "spa", "mons", "köln", "düren"):
        assert _place_name({"event_mentions": []}, stem=short) == short


def test_place_name_prefers_explicit_name():
    place = {"current_name": "Aachen", "event_mentions": [{"original_text": "ignore"}]}
    assert _place_name(place, stem="whatever") == "Aachen"


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


def test_toponym_from_prose_capitalization_and_context():
    from src.enrichment.places_grok_geocode import _place_name, _toponym_from_prose

    # A prose clause is NOT fed whole to the geocoder; only the mid-sentence toponym is extracted.
    assert (
        _place_name(
            {
                "event_mentions": [
                    {"original_text": "The Germans held Schmidt until November."}
                ]
            }
        )
        == "Schmidt"
    )
    # Mid-sentence capitalized toponym IS extracted (not sentence-initial, not a stopword).
    assert _toponym_from_prose("They attacked Aachen at dawn") == "Aachen"
    assert _toponym_from_prose("The Germans held Schmidt until November.") == "Schmidt"
    # Multi-word capitalized toponyms stay intact.
    assert (
        _toponym_from_prose("fighting raged in the Hürtgen Forest for weeks")
        == "Hürtgen Forest"
    )
    assert _toponym_from_prose("the drive on Monte Cassino") == "Monte Cassino"
    # Sentence-initial single token is ambiguous ("Ulm was on fire") -> only with stem corroboration.
    assert _toponym_from_prose("Ulm was on fire.") is None
    assert _toponym_from_prose("Ulm was on fire.", corroborate="ulm") == "Ulm"
    # Pure prose with no capitalized toponym -> None.
    assert _toponym_from_prose("move north now please go") is None


def test_safe_entity_filename_blocks_traversal():
    from src.extraction.batch_parallel import _safe_entity_filename as sf

    assert sf("/abs/evil.json", "01U") == "evil.json"
    assert sf("../../x.json", "01U") == "x.json"
    assert sf("..", "01UFALLBACK") == "01UFALLBACK.json"
    assert sf("normal place.json", "01U") == "normal place.json"
    assert "/" not in sf("a/b/c", "01U")


def test_toponym_no_cross_sentence_fusion():
    """C1 (review CRITICAL): a capitalized run must STOP at a sentence boundary — two cities in
    adjacent sentences must never fuse into a fabricated multi-town name."""
    from src.enrichment.places_grok_geocode import _toponym_from_prose

    # 'Aachen' ends a sentence; 'Cologne' starts the next. Neither should fuse; both are
    # sentence-adjacent single tokens -> sentence-initial 'Cologne' needs corroboration, and
    # 'Aachen' (mid-sentence after 'took') is the mid-sentence toponym returned.
    assert _toponym_from_prose("We took Aachen. Cologne burned.") == "Aachen"
    assert "Cologne" not in (
        _toponym_from_prose("We took Aachen. Cologne burned.") or ""
    )
    # Pure two-sentence, each city sentence-initial -> no mid-sentence toponym, no corroboration.
    assert _toponym_from_prose("Metz fell. Caen held.") is None


def test_toponym_extracts_capitalized_phrase_intact_for_grok_to_arbitrate():
    """Per design: the heuristic does NOT classify proper nouns. Two capitalized words in a row do
    NOT disqualify a place (New York City, New Jersey), so a capitalized phrase is extracted INTACT
    and handed to the Grok geocoder, which returns not-found for a non-place. The stopword set only
    bounds the phrase against function words.
    """
    from src.enrichment.places_grok_geocode import _toponym_from_prose

    # Multi-word real toponyms kept intact.
    assert _toponym_from_prose("the drive on Monte Cassino") == "Monte Cassino"
    assert _toponym_from_prose("they shipped it from New York City") == "New York City"
    assert (
        _toponym_from_prose("fighting raged in the Hürtgen Forest") == "Hürtgen Forest"
    )
    # Codename / person phrases are extracted INTACT (not fragmented) and left for Grok to reject.
    assert (
        _toponym_from_prose("the attack was called Operation Cobra")
        == "Operation Cobra"
    )
    # Function words still bound the phrase (so a whole clause is never sent).
    assert _toponym_from_prose("the attack entered Trier") == "Trier"


def test_toponym_sentence_boundary_with_closing_wrappers():
    """Review WARNING: a sentence terminator wrapped in a closing quote/paren ('Aachen.' / (Metz.))
    must still close the run so adjacent-sentence cities do not fuse."""
    from src.enrichment.places_grok_geocode import _toponym_from_prose

    assert _toponym_from_prose('took "Aachen." Cologne fell.') == "Aachen"
    assert "Cologne" not in (_toponym_from_prose('took "Aachen." Cologne fell.') or "")
    assert _toponym_from_prose("(Metz.) Caen held.") is None

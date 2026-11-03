"""PR3 (V1 / #671 class): casualties + supplemental must COERCE unmodeled top-level keys
(strip + loud OBSERV) instead of silently dropping the whole item/all-materials on
additionalProperties:False. Continuation of the deferred json_schemas schema-enforcement work.
"""

from __future__ import annotations

import logging

from src.extraction import casualties as C
from src.extraction import supplemental as S


def _valid_item():
    return {
        "CasualtyID": "01HTEST0000000000000000CAS",
        "type": "killed",
        "description": "533 soldiers killed in action",
        "original_text": "533 killed",
        "count": 533,
    }


def test_casualty_unmodeled_key_is_coerced_not_dropped(caplog):
    """An item with ONE unmodeled key is kept (key stripped + loud OBSERV), NOT dropped."""
    item = _valid_item()
    item["speculative_future_field"] = (
        "garble"  # unmodeled -> would reject the whole item
    )
    with caplog.at_level(logging.WARNING):
        out = C._validate_items([item])
    assert (
        len(out) == 1
    ), "coercible item was dropped (additionalProperties silent-drop not fixed)"
    assert "speculative_future_field" not in out[0], "unmodeled key not stripped"
    assert any(
        "OBSERV casualties.extra_field_stripped" in r.message for r in caplog.records
    ), "no loud OBSERV for the stripped key"
    # A purely-coercible drop should NOT report items_dropped (nothing truly lost).
    assert not any(
        "OBSERV casualties.items_dropped" in r.message for r in caplog.records
    )


def test_casualty_real_violation_still_dropped_with_aggregate(caplog):
    """A genuine modeled-field violation is still dropped, AND a canonical items_dropped fires."""
    bad = _valid_item()
    bad["type"] = 12345  # type must be a string -> real violation, not coercible
    with caplog.at_level(logging.WARNING):
        out = C._validate_items([bad])
    assert out == []
    assert any("OBSERV casualties.items_dropped" in r.message for r in caplog.records)
    assert any(
        "status=warn" in r.message and "dropped=1" in r.message for r in caplog.records
    )


def test_supplemental_unmodeled_toplevel_key_coerced(caplog):
    """_coerce_supplemental_schema_keys strips an unmodeled top-level key so the whole-event
    validate no longer rejects (and drops ALL materials)."""
    data = {
        "EventID": "01HTEST0000000000000000EVT",
        "Sub-eventID": "01HTEST0000000000000000SBE",
        "Supplemental_Material": [],
        "stray_llm_key": "boom",  # unmodeled top-level -> would drop all materials
    }
    with caplog.at_level(logging.WARNING):
        S._coerce_supplemental_schema_keys(data)
    assert "stray_llm_key" not in data, "unmodeled top-level key not stripped"
    assert any(
        "OBSERV supplemental.extra_field_stripped" in r.message for r in caplog.records
    )


def test_shared_strip_unmodeled_keys_respects_patternproperties():
    """The shared helper strips unmodeled keys but KEEPS patternProperties-permitted (^_) keys."""
    from src.schemas import strip_unmodeled_keys

    schema = {
        "properties": {"type": {}, "description": {}},
        "patternProperties": {"^_": {}},
    }
    rec = {
        "type": "killed",
        "description": "d",
        "_schema_version": "2.24",
        "bogus": "x",
    }
    seen = []
    n = strip_unmodeled_keys(rec, schema, seen.append)
    assert n == 1 and "bogus" not in rec  # unmodeled stripped
    assert "_schema_version" in rec  # patternProperties-permitted kept
    assert "type" in rec and "description" in rec  # modeled kept
    assert seen == ["bogus"]

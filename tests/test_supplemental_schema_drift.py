"""Regression: supplemental prompt↔schema drift that silently dropped bibliography sub-events.

Found by the 2026-10-24 RoerDams Phase-1+2 validation run — three mismatches caused
`validate_supplemental_json` to raise and the extractor to `break` (dropping the sub-event's
supplemental). Fixed by relaxing the schema (citation/reference_number optional; citation nullable)
and unwrapping keyed-by-Sub-eventID responses in `sanitize_supplemental_data`.
"""

import copy


from src.extraction.supplemental import (
    sanitize_supplemental_data,
    validate_supplemental_json,
)

EV = "01HX7YZABCDEFGHJKMNPQRSTVW"
SE = "01HX7YZABCDEFGHJKMNPQRSTVX"


def _flat(materials):
    return {
        "Event_Name": "E",
        "EventID": EV,
        "Sub-event_Name": "S",
        "Sub-eventID": SE,
        "Supplemental_Material": materials,
    }


def _assert_valid(data):
    validate_supplemental_json(sanitize_supplemental_data(copy.deepcopy(data)))


def test_prose_item_without_citation_or_refnum_validates():
    """A prose material (no citation, no reference_number) must validate, not be dropped."""
    _assert_valid(
        _flat(
            [
                {
                    "content_class": "prose",
                    "prose_content": "note",
                    "verbatim_reference": "x",
                }
            ]
        )
    )


def test_citation_item_validates():
    _assert_valid(
        _flat(
            [
                {
                    "content_class": "citation",
                    "verbatim_reference": "x",
                    "citation": {"title": "Cross-Channel Attack"},
                }
            ]
        )
    )


def test_keyed_by_subevent_response_is_unwrapped_and_validates():
    """A response wrapped as {<Sub-eventID>: {...}} must be flattened, not rejected as additional
    property. Previously raised '<ULID> do not match ^_' and dropped the data."""
    keyed = {
        SE: _flat(
            [
                {
                    "content_class": "citation",
                    "verbatim_reference": "x",
                    "citation": {"title": "T"},
                }
            ]
        )
    }
    out = sanitize_supplemental_data(copy.deepcopy(keyed))
    validate_supplemental_json(out)
    assert len(out["Supplemental_Material"]) == 1


def test_classification_reconciled_to_content_class():
    """The prompt emits `classification`; sanitize maps it to the schema's `content_class`."""
    out = sanitize_supplemental_data(
        copy.deepcopy(
            _flat(
                [
                    {
                        "classification": "citation",
                        "verbatim_reference": "x",
                        "citation": {"title": "T"},
                    }
                ]
            )
        )
    )
    validate_supplemental_json(out)
    assert out["Supplemental_Material"][0].get("content_class") == "citation"


def test_titleless_citation_is_normalized_not_dropped():
    """A citation object without a title is normalized (title='Unknown') by sanitize and still
    validates — the material is PRESERVED, not dropped on a validation error."""
    out = sanitize_supplemental_data(
        _flat(
            [{"content_class": "citation", "verbatim_reference": "x", "citation": {}}]
        )
    )
    validate_supplemental_json(out)
    assert out["Supplemental_Material"][0]["citation"]["title"] == "Unknown"


def test_dropped_subevent_is_loud(caplog):
    """A dropped sub-event (prompt with no response) must emit an explicit dropped count + anomaly."""
    import logging
    from pathlib import Path
    from src.extraction.supplemental import _observe_supplemental_extract

    prompts = [("se1", "p", "m"), ("se2", "p", "m"), ("se3", "p", "m")]
    responses = [
        {"Supplemental_Material": [{"content_class": "citation"}]}
    ]  # 2 dropped
    with caplog.at_level(logging.INFO, logger="src.extraction.supplemental"):
        _observe_supplemental_extract(Path("ch1-event.json"), prompts, responses)
    assert "dropped=2" in caplog.text
    assert "supplemental.anomaly" in caplog.text
    assert "2/3 sub-event(s) DROPPED" in caplog.text


def test_no_anomaly_when_none_dropped(caplog):
    import logging
    from pathlib import Path
    from src.extraction.supplemental import _observe_supplemental_extract

    prompts = [("se1", "p", "m")]
    responses = [{"Supplemental_Material": []}]
    with caplog.at_level(logging.INFO, logger="src.extraction.supplemental"):
        _observe_supplemental_extract(Path("ch1-event.json"), prompts, responses)
    assert "dropped=0" in caplog.text
    assert "supplemental.anomaly" not in caplog.text


def test_schema_directly_rejects_titleless_citation_object():
    """The relax is null-only: a present (non-null) citation object with no title still fails the
    RAW schema (independent of the sanitize title='Unknown' normalization)."""
    import pytest as _pytest

    with _pytest.raises(Exception):
        validate_supplemental_json(
            _flat(
                [
                    {
                        "MaterialID": "01HX7YZABCDEFGHJKMNPQRSTV1",
                        "EventID": EV,
                        "Sub-eventID": SE,
                        "reference_type": "bibliography",
                        "verbatim_reference": "x",
                        "availability": "unknown",
                        "citation": {
                            "author": ["X"]
                        },  # object present, NO title -> must fail
                    }
                ]
            )
        )


def test_unwrap_merges_multiple_keyed_subevents_without_loss():
    SE2 = "01HX7YZABCDEFGHJKMNPQRSTV2"
    keyed = {
        SE: _flat(
            [
                {
                    "content_class": "citation",
                    "verbatim_reference": "a",
                    "citation": {"title": "A"},
                }
            ]
        ),
        SE2: _flat([{"content_class": "prose", "verbatim_reference": "b"}]),
    }
    out = sanitize_supplemental_data(copy.deepcopy(keyed))
    validate_supplemental_json(out)
    assert len(out["Supplemental_Material"]) == 2  # both materials preserved

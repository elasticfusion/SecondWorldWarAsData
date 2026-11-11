"""Every entity merge unions mentions by ONE rule (src/utils/entity_records.merge_mentions) and keeps
each kept mention intact - its quote, locator and links are what provenance validation checks
(scripts/validate_provenance). Reviewed 2026-11-11: the Dedup Review UI used a different key and
deleted secondaries before writing the primary; bibliography collapsed every unnumbered note of a
sub-event into one."""

import copy
import os
from unittest import mock

import pytest

os.environ.setdefault("S3_BUCKET", "test-bucket")
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")

from src.utils.entity_records import (  # noqa: E402
    citation_identity,
    merge_mentions,
)


def _m(seid, text, mid, spelling="Sub_eventID", **kw):
    return {
        "MentionID": mid,
        "EventID": "E1",
        spelling: seid,
        "original_text": text,
        **kw,
    }


# --- the rule ------------------------------------------------------------------------------------


def test_one_mention_per_sub_event_so_a_re_extraction_does_not_stack():
    into = [_m("S1", "first wording", "M1")]
    assert merge_mentions(into, [_m("S1", "second wording", "M2")]) == 0
    assert [m["MentionID"] for m in into] == ["M1"]


def test_both_sub_event_spellings_are_one_identity():
    into = [_m("S1", "q", "M1")]
    assert merge_mentions(into, [_m("S1", "q", "M2", spelling="Sub-eventID")]) == 0


def test_mentions_without_a_sub_event_are_told_apart_by_their_quote():
    into = [_m(None, "Force U was marshaled", "M1")]
    added = merge_mentions(
        into,
        [
            _m(None, "FORCE U  was marshaled.", "M2"),
            _m(None, "a different passage", "M3"),
        ],
    )
    assert added == 1 and [m["MentionID"] for m in into] == ["M1", "M3"]


def test_a_kept_mention_is_copied_untouched():
    m = _m("S2", "q", "M2", paragraph_numbers=[7], book="B", chapter="C")
    snapshot = copy.deepcopy(m)
    into = [_m("S1", "q", "M1")]
    merge_mentions(into, [m])
    assert into[-1] == snapshot


def test_unnumbered_notes_of_a_sub_event_are_distinct_citations():
    a = {
        "EventID": "E1",
        "Sub-eventID": "S1",
        "reference_number": "",
        "verbatim_reference": "A",
    }
    b = dict(a, verbatim_reference="B")
    into = [a]
    assert merge_mentions(into, [b], citation_identity) == 1
    numbered = dict(a, reference_number="4", verbatim_reference="text")
    assert citation_identity(numbered) == citation_identity(
        dict(numbered, verbatim_reference="x")
    )


def test_bibliography_reconcile_keeps_distinct_unnumbered_notes():
    from src.extraction.bibliography import _merge_bib_records

    m1 = {
        "EventID": "E1",
        "Sub-eventID": "S1",
        "reference_number": "",
        "verbatim_reference": "A",
    }
    m2 = dict(m1, verbatim_reference="B")
    merged = _merge_bib_records(
        [
            {"BibliographyID": "1", "mentions": [m1]},
            {"BibliographyID": "2", "mentions": [m2, m1]},
        ]
    )
    assert merged["mentions"] == [m1, m2]


# --- the merge paths use it ----------------------------------------------------------------------


def test_dedup_auto_merge_unions_by_the_rule(tmp_path):
    import json

    from src.dedup import merge as M

    d = tmp_path / "places"
    d.mkdir()
    a = {"PlaceID": "P1", "name": "Caen", "event_mentions": [_m("S1", "q1", "M1")]}
    b = {
        "PlaceID": "P2",
        "name": "caen",
        "event_mentions": [
            _m("S1", "dup", "M2"),
            _m("S2", "q2", "M3", paragraph_numbers=[4]),
        ],
    }
    (d / "a.json").write_text(json.dumps(a))
    (d / "b.json").write_text(json.dumps(b))
    with (
        mock.patch.object(
            M,
            "_write_entity_guarded",
            side_effect=lambda f, data: f.write_text(json.dumps(data)) or True,
        ),
        mock.patch.object(M, "_rebase_refs_for_merge"),
        mock.patch.object(M, "_backup_before_delete", return_value=True),
        mock.patch.object(M, "_notify_deletion"),
        mock.patch.object(M, "_dynamo_merge_sync"),
    ):
        M.merge_generic(
            d, [{"filename": "a.json"}, {"filename": "b.json"}], 0, "PlaceID"
        )
    merged = json.loads((d / "a.json").read_text())
    assert [m["MentionID"] for m in merged["event_mentions"]] == ["M1", "M3"]
    assert merged["event_mentions"][1]["paragraph_numbers"] == [4]
    assert not (d / "b.json").exists()


def test_people_merge_unions_by_the_rule():
    from src.extraction.people import _merge_person

    existing = {"event_mentions": [_m("S1", "q", "M1")]}
    _merge_person(
        existing, {"event_mentions": [_m("S1", "x", "M2"), _m("S2", "y", "M3")]}
    )
    assert [m["MentionID"] for m in existing["event_mentions"]] == ["M1", "M3"]


@pytest.fixture
def ui():
    import lambda_handlers.dedup_ui_handler as h

    return h


def _storage(files):
    st = mock.Mock()
    st.read_json.side_effect = lambda path: copy.deepcopy(files[path])
    return st


def test_the_review_ui_merge_unions_by_the_rule_and_writes_before_deleting(
    ui, monkeypatch
):
    monkeypatch.setattr(ui, "_sync_entity_to_dynamo", mock.Mock())
    monkeypatch.setattr(ui, "_append_to_manifest", mock.Mock())
    monkeypatch.setattr(ui, "_rebase_event_refs_storage", mock.Mock())
    prefix = ui.ENTITY_PREFIXES["places"]
    st = _storage(
        {
            f"{prefix}/a.json": {
                "PlaceID": "P1",
                "name": "Caen",
                "event_mentions": [_m("S1", "q1", "M1")],
            },
            f"{prefix}/b.json": {
                "PlaceID": "P2",
                "name": "caen",
                "event_mentions": [_m("S1", "dup", "M2"), _m("S2", "q2", "M3")],
            },
        }
    )
    calls = []
    st.write_json.side_effect = lambda path, data: calls.append(("write", path, data))
    st.delete.side_effect = lambda path: calls.append(("delete", path))
    ui._merge_generic_files(
        "places", [{"filename": "a.json"}, {"filename": "b.json"}], 0, st
    )
    assert [c[0] for c in calls] == ["write", "delete"]
    assert [m["MentionID"] for m in calls[0][2]["event_mentions"]] == ["M1", "M3"]


def test_a_failed_primary_write_deletes_nothing(ui, monkeypatch):
    monkeypatch.setattr(ui, "_rebase_event_refs_storage", mock.Mock())
    prefix = ui.ENTITY_PREFIXES["places"]
    st = _storage(
        {
            f"{prefix}/a.json": {"PlaceID": "P1", "event_mentions": []},
            f"{prefix}/b.json": {
                "PlaceID": "P2",
                "event_mentions": [_m("S2", "q", "M3")],
            },
        }
    )
    st.write_json.side_effect = RuntimeError("s3 down")
    with pytest.raises(RuntimeError):
        ui._merge_generic_files(
            "places", [{"filename": "a.json"}, {"filename": "b.json"}], 0, st
        )
    st.delete.assert_not_called()
    ui._rebase_event_refs_storage.assert_not_called()

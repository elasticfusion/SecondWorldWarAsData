"""Removing one chapter's Phase 2 output before it is extracted again: its mentions go, a record that
came from it alone goes (and leaves its index), a record shared with other chapters stays.
"""

import importlib.util
import json
import sys
from pathlib import Path

from tests.fake_s3 import FakeS3

_SPEC = importlib.util.spec_from_file_location(
    "purge_chapter_event",
    str(Path(__file__).resolve().parents[1] / "scripts" / "purge_chapter_event.py"),
)
P = importlib.util.module_from_spec(_SPEC)
sys.modules["purge_chapter_event"] = P  # the dataclass decorator looks its module up
_SPEC.loader.exec_module(P)  # type: ignore[union-attr]

E8, E9 = "761S0P9QED2Q16K3PJ2QGAHC5E", "0YQFZD6P4XYJ5BTC72QFMAQXYB"


def _m(event_id, sub="S"):
    return {
        "MentionID": f"m-{event_id[:4]}-{sub}",
        "EventID": event_id,
        "Sub_eventID": sub,
    }


def test_a_shared_record_keeps_its_other_mentions_and_its_enrichment():
    person = {
        "name": "Omar N. Bradley",
        "bio": "kept",
        "event_mentions": [_m(E8), _m(E9)],
    }
    out, removed, empty = P.strip_event(person, E8)
    assert removed == 1 and not empty
    assert out["event_mentions"] == [_m(E9)] and out["bio"] == "kept"


def test_a_record_from_this_chapter_alone_is_empty():
    place = {"name": "Ste. Mere-Eglise", "event_mentions": [_m(E8, "a"), _m(E8, "b")]}
    _out, removed, empty = P.strip_event(place, E8)
    assert removed == 2 and empty


def test_a_record_without_the_chapter_is_untouched():
    record = {"event_mentions": [_m(E9)]}
    assert P.strip_event(record, E8) == (record, 0, False)


def test_nested_mention_lists_are_found():
    bib = {"citation": {"title": "x"}, "mentions": [_m(E8)], "notes": ["a", "b"]}
    out, removed, empty = P.strip_event(bib, E8)
    assert removed == 1 and empty and out["notes"] == ["a", "b"]


def test_a_mention_without_an_eventid_still_counts_as_a_mention():
    """A bibliography mention carries an EventID only when its material resolved: the record is
    still cited, by another chapter, and must not be deleted."""
    bib = {"mentions": [_m(E8), {"MentionID": "legacy", "chapter": "chapter3"}]}
    out, removed, empty = P.strip_event(bib, E8)
    assert removed == 1 and not empty
    assert out["mentions"] == [{"MentionID": "legacy", "chapter": "chapter3"}]


def test_other_lists_of_dicts_are_not_mentions():
    record = {"event_mentions": [_m(E9)], "units": [{"EventID": E8, "name": "x"}]}
    assert P.strip_event(record, E8)[1] == 0


def test_a_casualty_of_the_chapter_is_its_record():
    assert P.is_event_record({"event_context": {"EventID": E8}}, E8)
    assert P.is_event_record({"source": {"EventID": E8}}, E8)
    assert not P.is_event_record({"event_context": {"EventID": E9}}, E8)


def test_the_index_loses_only_deleted_files():
    flat = {"a": "a.json", "b": "b.json"}
    assert P.prune_index(flat, {"a.json"}) == ({"b": "b.json"}, 1)
    nested = {"authors": {"x": "bib_1.json", "y": "bib_2.json"}, "refs": {}}
    assert P.prune_index(nested, {"bib_1.json"}) == (
        {"authors": {"y": "bib_2.json"}, "refs": {}},
        1,
    )


def test_markers_by_name_and_by_path():
    names = ["chapter7full-event.json", "chapter8full-event.json"]
    assert P.prune_marker(names, "chapter8full-event.json", "B") == (
        ["chapter7full-event.json"],
        1,
        True,  # a bare name: that chapter of every book
    )
    paths = {
        "/app/output/content/B/chapter8full-event.json": 1,
        "/app/output/content/B/chapter18full-event.json": 1,
        "/app/output/content/Other/chapter8full-event.json": 1,  # another book's chapter 8
    }
    kept, n, bare = P.prune_marker(paths, "chapter8full-event.json", "B")
    assert n == 1 and not bare
    assert sorted(kept) == [
        "/app/output/content/B/chapter18full-event.json",
        "/app/output/content/Other/chapter8full-event.json",
    ]


def _bucket():
    j = lambda o: json.dumps(o)  # noqa: E731
    return FakeS3(
        {
            "output/content/B/chapter8full-event.json": j({"Event": {"EventID": E8}}),
            "output/content/B/chapter8full-parsed.json": j({"paragraphs": []}),
            "output/content/B/source_section/SS8.json": j({"EventID": E8}),
            "output/content/B/source_section/SS9.json": j({"EventID": E9}),
            "output/people/omar n bradley.json": j(
                {"event_mentions": [_m(E8), _m(E9)]}
            ),
            "output/people/j m stagg.json": j({"event_mentions": [_m(E8)]}),
            "output/people/other.json": j({"event_mentions": [_m(E9)]}),
            "output/people/index.json": j(
                {"j m stagg": "j m stagg.json", "other": "other.json"}
            ),
            "output/casualties/casualties_1.json": j(
                {"event_context": {"EventID": E8}}
            ),
            "output/casualties/casualties_2.json": j(
                {"event_context": {"EventID": E9}}
            ),
            "output/logistics/.processed_events.json": j(
                ["chapter8full-event.json", "chapter9full-event.json"]
            ),
            "output/images/I1.json": j({"EventID": E8}),
        }
    )


def test_the_purge_end_to_end():
    s3 = _bucket()
    event_id, plan = P.plan_purge(s3, "buck", "B", "chapter8full")
    assert event_id == E8
    before = set(s3.keys())
    P.apply(s3, "buck", plan)
    keys = set(s3.keys())
    gone = before - keys
    assert gone == {
        "output/content/B/chapter8full-event.json",  # moved aside
        "output/content/B/source_section/SS8.json",
        "output/people/j m stagg.json",  # from chapter 8 alone
        "output/casualties/casualties_1.json",
    }
    assert any(
        k.startswith("diagnostics/superseded/B/chapter8full-event.") for k in keys
    )
    assert json.loads(s3.text("output/people/omar n bradley.json"))[
        "event_mentions"
    ] == [_m(E9)]
    assert json.loads(s3.text("output/people/index.json")) == {"other": "other.json"}
    assert json.loads(s3.text("output/logistics/.processed_events.json")) == [
        "chapter9full-event.json"
    ]
    # untouched: the parsed chapter, images, other chapters
    for k in (
        "output/content/B/chapter8full-parsed.json",
        "output/images/I1.json",
        "output/people/other.json",
        "output/casualties/casualties_2.json",
        "output/content/B/source_section/SS9.json",
    ):
        assert k in keys


def test_a_dry_run_plans_and_changes_nothing():
    s3 = _bucket()
    before = {k: s3.text(k) for k in s3.keys()}
    _eid, plan = P.plan_purge(s3, "buck", "B", "chapter8full")
    assert plan.mentions == {"people": 2} and plan.records_deleted == {
        "people": 1,
        "casualties": 1,
    }
    assert {k: s3.text(k) for k in s3.keys()} == before


def test_an_enriched_record_from_this_chapter_alone_is_reported():
    s3 = _bucket()
    s3.put_object(
        Bucket="buck",
        Key="output/people/j m stagg.json",
        Body=json.dumps(
            {"event_mentions": [_m(E8)], "biographical_profile": {"born": "1900"}}
        ),
    )
    _eid, plan = P.plan_purge(s3, "buck", "B", "chapter8full")
    assert plan.enriched_deleted == ["output/people/j m stagg.json"]
    _eid, kept = P.plan_purge(s3, "buck", "B", "chapter8full", keep_enriched=True)
    assert kept.enriched_kept == ["output/people/j m stagg.json"]
    assert "output/people/j m stagg.json" not in kept.delete
    assert kept.rewrite["output/people/j m stagg.json"]["event_mentions"] == []
    assert kept.rewrite["output/people/j m stagg.json"]["biographical_profile"]


def test_an_object_changed_after_the_plan_is_not_overwritten():
    import pytest
    from botocore.exceptions import ClientError

    s3 = _bucket()
    _eid, plan = P.plan_purge(s3, "buck", "B", "chapter8full")
    # a writer updates a shared record between plan and apply
    s3.put_object(
        Bucket="buck",
        Key="output/people/omar n bradley.json",
        Body=json.dumps({"event_mentions": [_m(E8), _m(E9), _m("NEW")]}),
    )
    with pytest.raises(ClientError):
        P.apply(s3, "buck", plan)
    assert "NEW" in s3.text("output/people/omar n bradley.json")  # its change kept
    # the event file was copied but NOT removed: the purge can be re-planned
    assert "output/content/B/chapter8full-event.json" in s3.keys()

"""Tests for deterministic, content-derived event ids (HIGH-1 #1)."""

import re

import src.extraction.events as events
from src.utils.deterministic_ids import event_id, sub_event_id

_ULID = re.compile(r"^[0-9A-HJKMNP-TV-Z]{26}$")


def test_event_id_is_deterministic_and_valid():
    a = event_id("The Ardennes", "Chapter V: COBRA", "full")
    b = event_id("The Ardennes", "Chapter V: COBRA", "full")
    assert a == b  # stable across calls (the ripple-prevention guarantee)
    assert _ULID.match(a)  # schema-valid Crockford ULID shape


def test_event_id_normalizes_trivial_reformatting():
    assert event_id("The Ardennes", "COBRA") == event_id("the  ardennes", "cobra!")


def test_event_id_distinct_per_section():
    assert event_id("B", "C", "full") != event_id("B", "C", "part2")
    assert event_id("B", "C1") != event_id("B", "C2")


def test_sub_event_id_stable_by_range_and_scoped_to_parent():
    eid = event_id("B", "C")
    s1 = sub_event_id(eid, para_start=3, para_end=9)
    assert s1 == sub_event_id(eid, para_start=3, para_end=9)
    assert _ULID.match(s1)
    # Different parent -> different sub-event id for the same range.
    assert s1 != sub_event_id(event_id("B", "D"), para_start=3, para_end=9)
    # Name fallback works when no range is available.
    assert _ULID.match(sub_event_id(eid, name="Attack opens"))


def test_apply_deterministic_ids_ignores_llm_ids():
    """The LLM-supplied EventID/Sub-eventID are REPLACED by derived values. Uses the REAL parsed
    key `section_id` (not the nonexistent `section`)."""
    parsed = {"book": "The Ardennes", "chapter_title": "COBRA", "section_id": "full"}
    response = {
        "Event": {
            "EventID": "01BOGUSLLMVALUE0000000000",  # hallucinated/volatile
            "Sub-events": [
                {
                    "Sub-eventID": "01BOGUSSUBVALUE000000000X",
                    "Sub-event_Name": "x",
                    "Sub-event_fulltext": {"Paragraph_3": "...", "Paragraph_4": "..."},
                }
            ],
        }
    }
    events._apply_deterministic_ids(response, parsed)
    ev = response["Event"]
    assert ev["EventID"] == event_id("The Ardennes", "COBRA", "full")
    assert ev["EventID"] != "01BOGUSLLMVALUE0000000000"
    assert ev["Sub-events"][0]["Sub-eventID"] == sub_event_id(
        ev["EventID"], para_start=3, para_end=4, name="x"
    )


def test_sections_of_same_chapter_get_distinct_event_ids():
    """REGRESSION: distinct sections of one chapter (real key `section_id` = 'a','b',...) must get
    DIFFERENT EventIDs. A prior bug read the nonexistent `section` key, collapsing all sections of
    a chapter onto one id (over-merge). Drive the production code with the real schema key.
    """
    base = {"book": "The Ardennes", "chapter_title": "Chapter 13"}
    ids = set()
    for sid in ("a", "b", "c"):
        resp = {"Event": {"EventID": "x", "Sub-events": []}}
        events._apply_deterministic_ids(resp, {**base, "section_id": sid})
        ids.add(resp["Event"]["EventID"])
    assert len(ids) == 3, f"sections collapsed onto {len(ids)} id(s): {ids}"


def test_nameless_rangeless_subevent_does_not_crash():
    """REGRESSION / fail-safe: a sub-event with no paragraph range AND no name/summary must NOT
    raise (that would abort extraction before validation + bypass the retry loop) — it falls back
    to a deterministic positional id."""
    parsed = {"book": "B", "chapter_title": "C", "section_id": "full"}
    response = {
        "Event": {
            "EventID": "x",
            "Sub-events": [
                {"Sub-event_fulltext": {}},  # no range, no name, no summary
                {"Sub-event_fulltext": {}},
            ],
        }
    }
    events._apply_deterministic_ids(response, parsed)  # must not raise
    subs = response["Event"]["Sub-events"]
    assert all(_ULID.match(se["Sub-eventID"]) for se in subs)
    # Positional fallback keeps the two distinct within the parent.
    assert subs[0]["Sub-eventID"] != subs[1]["Sub-eventID"]


def test_dependents_inherit_the_deterministic_event_id():
    """CROSS-PHASE: the event is the single mint point; a dependent entity copies the SAME
    deterministic EventID (dependents only READ it). Proves Phase-2/3 referrers inherit the id
    rather than minting their own, so references stay resolvable across re-runs."""
    from src.extraction.source_section import build_source_section

    parsed = {"book": "The Ardennes", "chapter_title": "COBRA", "section_id": "full"}
    response = {
        "Event": {
            "EventID": "01VOLATILELLMVALUE0000000",
            "Chapter": "COBRA",
            "Sub-events": [],
        }
    }
    events._apply_deterministic_ids(response, parsed)
    derived = response["Event"]["EventID"]
    assert derived == event_id("The Ardennes", "COBRA", "full")

    # A dependent (source_section) reads + attaches the event's id — it mints nothing of its own.
    section = build_source_section(response)
    assert section is not None
    assert section["EventID"] == derived  # inherited, not a fresh id


def test_apply_deterministic_ids_idempotent_across_reruns():
    """Two independent extractions of the same source unit yield identical ids (no ripple)."""
    parsed = {"book": "B", "chapter_title": "C", "section_id": "full"}

    def _resp():
        return {
            "Event": {
                "EventID": "01DIFFERENTEACHRUN0000000",
                "Sub-events": [
                    {
                        "Sub-event_Name": "s",
                        "Sub-event_fulltext": {"Paragraph_1": "a", "Paragraph_2": "b"},
                    }
                ],
            }
        }

    r1 = _resp()
    r2 = _resp()
    events._apply_deterministic_ids(r1, parsed)
    events._apply_deterministic_ids(r2, parsed)
    assert r1["Event"]["EventID"] == r2["Event"]["EventID"]
    assert (
        r1["Event"]["Sub-events"][0]["Sub-eventID"]
        == r2["Event"]["Sub-events"][0]["Sub-eventID"]
    )

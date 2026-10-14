"""Blocker C: event extraction populates sub-event Footnote/Endnote references from markers."""

from src.extraction.events import _populate_footnote_refs


def _resp(fulltext):
    return {
        "Event": {
            "Sub-events": [{"Sub-eventID": "01SE", "Sub-event_fulltext": fulltext}]
        }
    }


def test_populates_footnote_refs_from_markers():
    parsed = {"footnotes": [{"number": 1, "text": "a"}, {"number": 3, "text": "b"}]}
    resp = _resp({"p1": "X<sup>[1](u#fn1)</sup> Y<sup>[3](u#fn3)</sup>"})
    _populate_footnote_refs(resp, parsed)
    assert resp["Event"]["Sub-events"][0]["Footnote_References"] == [1, 3]


def test_classifies_endnotes_vs_footnotes():
    # number 5 is an endnote (in endnotes array), 1 is a footnote
    parsed = {"footnotes": [{"number": 1}], "endnotes": [{"number": 5}]}
    resp = _resp({"p": "<sup>[1](u#fn1)</sup> <sup>[5](u#fn5)</sup>"})
    _populate_footnote_refs(resp, parsed)
    se = resp["Event"]["Sub-events"][0]
    assert se["Footnote_References"] == [1]
    assert se["Endnote_References"] == [5]


def test_no_markers_no_refs():
    resp = _resp({"p": "plain text no markers"})
    _populate_footnote_refs(resp, {"footnotes": [{"number": 1}]})
    se = resp["Event"]["Sub-events"][0]
    assert "Footnote_References" not in se and "Endnote_References" not in se


def test_idempotent():
    parsed = {"footnotes": [{"number": 2}]}
    resp = _resp({"p": "<sup>[2](u#fn2)</sup>"})
    _populate_footnote_refs(resp, parsed)
    _populate_footnote_refs(resp, parsed)
    assert resp["Event"]["Sub-events"][0]["Footnote_References"] == [2]


def test_failsafe_on_bad_input():
    # Should never raise even on malformed response/parsed.
    _populate_footnote_refs({"Event": {"Sub-events": [None]}}, {})
    _populate_footnote_refs({}, {"footnotes": "notalist"})


def test_malformed_subevent_does_not_drop_others():
    """Per-sub-event isolation: a None/malformed sub-event must not skip refs for valid ones."""
    from src.extraction.events import _populate_footnote_refs

    parsed = {"footnotes": [{"number": 7}]}
    resp = {
        "Event": {
            "Sub-events": [
                None,  # malformed — must be skipped, not abort the loop
                {
                    "Sub-eventID": "01SE",
                    "Sub-event_fulltext": {"p": "<sup>[7](u#fn7)</sup>"},
                },
            ]
        }
    }
    _populate_footnote_refs(resp, parsed)
    assert resp["Event"]["Sub-events"][1]["Footnote_References"] == [7]


def test_oversized_marker_ignored_good_refs_survive():
    """Defense-in-depth: a >6-digit garbage marker is ignored and does NOT drop the
    valid refs in the same sub-event (no int-digit-limit crash, no unbounded array)."""
    from src.extraction.events import _populate_footnote_refs

    big = "9" * 5000  # would exceed Python's int-string digit limit if int()'d
    resp = {
        "Event": {
            "Sub-events": [
                {
                    "Sub-eventID": "01SE",
                    "Sub-event_fulltext": {
                        "p": f"<sup>[7](u#fn7)</sup> <sup>[{big}](u)</sup>"
                    },
                }
            ]
        }
    }
    _populate_footnote_refs(resp, {"footnotes": [{"number": 7}]})
    assert resp["Event"]["Sub-events"][0]["Footnote_References"] == [7]

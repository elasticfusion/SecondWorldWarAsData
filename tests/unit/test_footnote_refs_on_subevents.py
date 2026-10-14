"""Blocker C: event extraction populates sub-event footnote refs from the RAW parsed
paragraphs assigned to each sub-event (markers live there, NOT in the summarized fulltext).
"""

from src.extraction.events import _populate_footnote_refs


def _parsed(paras, footnotes=None, endnotes=None):
    d = {"paragraphs": [{"absolute_number": n, "text": t} for n, t in paras]}
    if footnotes is not None:
        d["footnotes"] = [{"number": n} for n in footnotes]
    if endnotes is not None:
        d["endnotes"] = [{"number": n} for n in endnotes]
    return d


def _resp(paragraphs, fulltext=None):
    se = {"Sub-eventID": "01SE", "paragraphs": paragraphs}
    if fulltext is not None:
        se["Sub-event_fulltext"] = fulltext
    return {"Event": {"Sub-events": [se]}}


def test_refs_from_assigned_paragraphs():
    # Markers are in the RAW paragraphs; sub-event 'paragraphs' points at them.
    parsed = _parsed(
        [(5, "X<sup>[1](u#fn1)</sup>"), (6, "Y<sup>[3](u#fn3)</sup>")], footnotes=[1, 3]
    )
    resp = _resp([5, 6])
    _populate_footnote_refs(resp, parsed)
    assert resp["Event"]["Sub-events"][0]["Footnote_References"] == [1, 3]


def test_summarized_fulltext_without_markers_still_works():
    # The real-world case: Sub-event_fulltext is marker-free LLM prose, but the assigned
    # paragraphs carry the markers. Must still derive refs (the actual Blocker C bug).
    parsed = _parsed([(2, "The attack began.<sup>[18](u#fn18)</sup>")], footnotes=[18])
    resp = _resp([2], fulltext={"summary": "The First Army advanced many miles."})
    _populate_footnote_refs(resp, parsed)
    assert resp["Event"]["Sub-events"][0]["Footnote_References"] == [18]


def test_classifies_endnotes_vs_footnotes():
    parsed = _parsed(
        [(1, "<sup>[1](u#fn1)</sup> <sup>[5](u#fn5)</sup>")],
        footnotes=[1],
        endnotes=[5],
    )
    resp = _resp([1])
    _populate_footnote_refs(resp, parsed)
    se = resp["Event"]["Sub-events"][0]
    assert se["Footnote_References"] == [1]
    assert se["Endnote_References"] == [5]


def test_no_markers_no_refs():
    parsed = _parsed([(1, "plain text no markers")], footnotes=[1])
    resp = _resp([1])
    _populate_footnote_refs(resp, parsed)
    se = resp["Event"]["Sub-events"][0]
    assert "Footnote_References" not in se and "Endnote_References" not in se


def test_fulltext_fallback_when_no_paragraph_assignment():
    # No 'paragraphs' array -> fall back to scanning fulltext values.
    parsed = _parsed([], footnotes=[7])
    resp = {
        "Event": {
            "Sub-events": [
                {
                    "Sub-eventID": "01SE",
                    "Sub-event_fulltext": {"p": "<sup>[7](u#fn7)</sup>"},
                }
            ]
        }
    }
    _populate_footnote_refs(resp, parsed)
    assert resp["Event"]["Sub-events"][0]["Footnote_References"] == [7]


def test_idempotent():
    parsed = _parsed([(2, "<sup>[2](u#fn2)</sup>")], footnotes=[2])
    resp = _resp([2])
    _populate_footnote_refs(resp, parsed)
    _populate_footnote_refs(resp, parsed)
    assert resp["Event"]["Sub-events"][0]["Footnote_References"] == [2]


def test_malformed_subevent_does_not_drop_others():
    parsed = _parsed([(7, "<sup>[7](u#fn7)</sup>")], footnotes=[7])
    resp = {"Event": {"Sub-events": [None, {"Sub-eventID": "01SE", "paragraphs": [7]}]}}
    _populate_footnote_refs(resp, parsed)
    assert resp["Event"]["Sub-events"][1]["Footnote_References"] == [7]


def test_failsafe_on_bad_input():
    _populate_footnote_refs({"Event": {"Sub-events": [None]}}, {})
    _populate_footnote_refs({}, {"footnotes": "notalist", "paragraphs": "bad"})


def test_oversized_marker_ignored_good_refs_survive():
    big = "9" * 5000
    parsed = _parsed(
        [(1, f"<sup>[7](u#fn7)</sup> <sup>[{big}](u)</sup>")], footnotes=[7]
    )
    resp = _resp([1])
    _populate_footnote_refs(resp, parsed)
    assert resp["Event"]["Sub-events"][0]["Footnote_References"] == [7]

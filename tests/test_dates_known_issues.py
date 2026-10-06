"""The three dates known-issues: content-aware summary staleness; partial-view mention
rendering for huge dates; precision-aware merge key."""

from src.extraction.date_summary import (
    needs_summary,
    _render_mentions,
    generate_date_summary,
)
from scripts.merge_dates import make_key

UL = "01ABCDEFGH0123456789ABCDEF"


class FakeGrok:
    def __init__(self, s="sum"):
        self.calls = 0
        self._s = s

    def extract_json(self, *a, **k):
        self.calls += 1
        return {"summary": self._s}


def _mentions(texts):
    return [
        {
            "Sub_eventID": f"SE{i}",
            "time_start": None,
            "original_text": t,
            "Event_Name": f"E{i}",
        }
        for i, t in enumerate(texts)
    ]


# (1) content-aware staleness
def test_resummary_on_content_change_same_count():
    g = FakeGrok("first")
    d = {
        "DateID": UL,
        "date_start": "1945-01-05",
        "event_mentions": _mentions(["a", "b"]),
    }
    generate_date_summary(d, g)
    assert g.calls == 1
    h1 = d["summary_mentions_hash"]
    # same COUNT (2) but one mention's content corrected -> must re-summarize
    d["event_mentions"][1]["original_text"] = "b-corrected"
    assert needs_summary(d) is True
    generate_date_summary(d, FakeGrok("second"))
    assert d["summary"] == "second" and d["summary_mentions_hash"] != h1


def test_no_resummary_when_unchanged():
    d = {
        "DateID": UL,
        "date_start": "1945-01-05",
        "event_mentions": _mentions(["a", "b"]),
    }
    generate_date_summary(d, FakeGrok("x"))
    g2 = FakeGrok("y")
    generate_date_summary(d, g2)  # nothing changed
    assert g2.calls == 0 and d["summary"] == "x"


# (2) partial-view rendering
def test_partial_view_flagged_with_total():
    big = {"event_mentions": _mentions([f"t{i}" for i in range(100)])}
    rendered = _render_mentions(big, limit=60)
    assert "PARTIAL VIEW" in rendered and "of 100 total" in rendered


def test_small_date_no_partial_note():
    small = {"event_mentions": _mentions(["a", "b", "c"])}
    assert "PARTIAL VIEW" not in _render_mentions(small, limit=60)


def test_representative_selection_diversifies_events():
    # 80 mentions all same Event -> dedup leaves 1 primary; fill from rest; still <= limit
    ms = [
        {
            "Sub_eventID": f"SE{i}",
            "original_text": f"t{i}",
            "Event_Name": "SameOp",
            "Sub_event_Name": "SameSub",
        }
        for i in range(80)
    ]
    rendered = _render_mentions({"event_mentions": ms}, limit=60)
    assert "of 80 total" in rendered


# (3) precision-aware merge key
def test_make_key_includes_precision():
    exact = {"date_start": "1945-01-05", "date_precision": "exact"}
    vague = {"date_start": "1945-01-05", "date_precision": "approximate"}
    assert make_key(exact) != make_key(vague)  # different precision -> not merged


def test_make_key_same_when_all_match():
    a = {"date_start": "1945-01-05", "date_precision": "exact"}
    b = {"date_start": "1945-01-05", "date_precision": "exact"}
    assert make_key(a) == make_key(b)
